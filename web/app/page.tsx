'use client';
import { useCallback, useEffect, useState } from 'react';
import '@xyflow/react/dist/style.css';
import { API, request } from '../lib/api';
import GraphView from './GraphView';
import type { Activity, Activity_, Graph, Progress, State, WordStatus } from '../lib/types';

const stages = ['context', 'opportunity', 'mission', 'plaud', 'reflection', 'graph', 'adapt'];
const percent = (n: number) => `${Math.round(n * 100)}%`;
const refreshOn = ['context.added', 'mission.created', 'graph.updated', 'vocabulary.updated', 'learning_path.adapted', 'lesson.progress', 'lesson.completed', 'plaud.waiting', 'plaud.transcript.waiting', 'plaud.recording.detected', 'error'];
const statusOrder: WordStatus[] = ['fluent', 'practiced', 'introduced', 'new'];

export default function Home() {
  const [state, setState] = useState<State | null>(null);
  const [graph, setGraph] = useState<Graph>({ nodes: [], edges: [] });
  const [progress, setProgress] = useState<Progress | null>(null);
  const [activities, setActivities] = useState<Activity_[]>([]);
  const [events, setEvents] = useState<Activity[]>([]);
  const [error, setError] = useState<{ message: string; stage?: string } | null>(null);
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    const [s, g, p, a] = await Promise.all([request<State>('/api/state'), request<Graph>('/api/graph'), request<Progress>('/api/learner/progress'), request<Activity_[]>('/api/activities')]);
    setState(s); setGraph(g); setProgress(p); setActivities(a);
  }, []);
  useEffect(() => {
    refresh().catch(e => setError({ message: e.message }));
    const source = new EventSource(`${API}/api/events`);
    source.onopen = () => { setConnected(true); refresh().catch(e => setError({ message: e.message })); };
    source.onerror = () => setConnected(false);
    source.onmessage = ({ data }) => {
      const event: Activity = JSON.parse(data);
      setEvents(previous => event.data.reset ? [event] : [...previous.filter(e => e.id !== event.id), event].slice(-100));
      if (event.type === 'error') setError({ message: event.message, stage: event.stage });
      else setError(previous => previous?.stage === event.stage || ['mission.created', 'graph.updated'].includes(event.type) ? null : previous);
      if (refreshOn.includes(event.type)) refresh().catch(e => setError({ message: e.message }));
    };
    return () => source.close();
  }, [refresh]);
  async function action(path: string, body?: unknown) {
    setBusy(true); setError(null);
    try { await request(path, 'POST', body); await refresh(); }
    catch (e) { setError({ message: e instanceof Error ? e.message : 'Request failed' }); }
    finally { setBusy(false); }
  }
  const mission = state?.active_mission;
  return <main>
    <header><div><p className="eyebrow">LEARN. APPLY. ADAPT.</p><h1>ApplyLoop<span>↗</span></h1><p>Turn knowledge into real-world experience.</p></div>{state?.mode === 'demo' && <button disabled={busy} onClick={() => action('/api/demo/reset')}>Reset demo</button>}</header>
    <div className="banner"><span>{state?.services?.band === 'live' ? 'LIVE BAND AGENTS' : 'DEMO AGENTS'}</span> {state?.graph_backend === 'neo4j' ? 'Neo4j knowledge graph' : 'In-memory graph'} · PLAUD {state?.services?.plaud || 'disconnected'} <b className={connected ? 'online' : 'offline'}>{connected ? '● Live event stream' : '○ Connecting to backend…'}</b></div>
    {error && <p className="error" role="alert">{error.message}</p>}
    {!state ? <section className="card"><h2>Connecting to ApplyLoop</h2><p>Start the backend with <code>make api</code> and this screen will connect automatically.</p></section> : <>
      <div className="top-grid">
        <section className="card"><p className="eyebrow">YOUR LEARNING STATE</p><h2>{state.goal}</h2>{state.skills.map(skill => <div className="skill" key={skill.id}><h3>{skill.name}</h3><div className="metric"><span>Knowledge</span><progress max="1" value={skill.knowledge_score} aria-label={`${skill.name} knowledge`} /><b>{percent(skill.knowledge_score)}</b></div><div className="metric application"><span>Application</span><progress max="1" value={skill.application_score} aria-label={`${skill.name} application`} /><b>{percent(skill.application_score)}</b></div></div>)}</section>
        <section className="card context"><p className="eyebrow">TODAY’S OPPORTUNITY</p><h2>{state.context.title}</h2><p>{state.context.location}{state.context.when && ` · ${state.context.when}`}{state.context.with_whom && ` · with ${state.context.with_whom}`}</p><div className="mission"><span className="tag">{mission ? mission.status.toUpperCase() : 'READY TO PRACTICE'}</span><h3>{mission?.title || 'You know it. Now try it.'}</h3><p>{mission?.challenge || 'Find a small practice mission based on what you know and where you’re going.'}</p>{mission && <p className="muted">{mission.reason}</p>}{!mission && state.focus_words.length > 0 && <p className="muted">Learned but not yet used in real life: {state.focus_words.map(w => w.lemma).join(', ')}</p>}</div><button className="primary" disabled={busy || !!mission} onClick={() => action('/api/opportunity')}>Find opportunity ↗</button>{mission?.status === 'assigned' && state.mode === 'demo' && <button className="replay" disabled={busy} onClick={() => action('/api/demo/replay')}>Play prerecorded demo replay</button>}<p className="muted">Record your practice in PLAUD and generate its transcript. The connected bridge sends it for reflection. Demo replay uses prerecorded evidence.</p></section>
      </div>
      {activities.length > 0 && <ActivitiesCard activities={activities} busy={busy} locked={mission?.status === 'assigned'} action={action} />}
      <section className="card"><p className="eyebrow">THE APPLICATION LOOP</p><div className="pipeline">{stages.map(stage => { const latest = [...events].reverse().find(e => e.stage === stage); const status = latest?.status || (stage === 'context' ? 'completed' : 'idle'); return <div key={stage} className={status}><span>{status === 'completed' ? '✓' : status === 'error' ? '!' : status === 'processing' ? '◉' : '○'}</span><b>{stage}</b></div>; })}</div></section>
      {state.next_target && <section className="next-target"><span>↗ NEXT LEARNING TARGET</span><h2>{state.skills.find(s => s.id === state.next_target?.skill_id)?.name}</h2><p>{state.next_target.reason}</p></section>}
      {progress && <KnowledgeCard progress={progress} busy={busy} action={action} />}
      <div className="bottom-grid"><section className="card"><p className="eyebrow">LEARNING GRAPH / {state.graph_backend === 'neo4j' ? 'NEO4J' : 'DEMO MEMORY STORE'}</p><GraphView graph={graph} /></section><section className="card"><p className="eyebrow">AGENT ACTIVITY</p><div className="timeline" aria-live="polite">{events.length === 0 && <p className="muted">Your application story will appear here. Find an opportunity to start.</p>}{[...events].reverse().map(e => <article key={e.id}><time>{new Date(e.created_at).toLocaleTimeString()}</time><div><b>{e.stage}</b><p>{e.message}</p></div></article>)}</div></section></div>
    </>}
    <footer>ApplyLoop · Learn. Apply. Adapt.</footer>
  </main>;
}

function KnowledgeCard({ progress, busy, action }: { progress: Progress; busy: boolean; action: (path: string, body?: unknown) => Promise<void> }) {
  const { level, resume, counts } = progress;
  const lessonTitles = Object.fromEntries(progress.lessons.map(l => [l.id, l.title]));
  const words = [...progress.words].filter(w => w.status !== 'new').sort((a, b) => statusOrder.indexOf(a.status) - statusOrder.indexOf(b.status));
  const post = (suffix: string, body: unknown) => resume && action(`/api/lessons/${resume.lesson_id}/${suffix}`, body);
  return <section className="card knowledge">
    <p className="eyebrow">KNOWLEDGE GRAPH · WHERE YOU ARE</p>
    <div className="knowledge-grid">
      <div>
        <div className="level-badge"><b>{level.current}</b><span>{level.fluent_level ? `Fluent at ${level.fluent_level}` : 'Not yet fluent in real life'}</span></div>
        {level.levels.map(row => <div key={row.code} className="skill">
          <h3>{row.code} · {row.name} {row.reached && '✓'}</h3>
          <div className="metric"><span>Learned</span><progress max="1" value={row.practiced_pct} aria-label={`${row.code} learned`} /><b>{percent(row.practiced_pct)}</b></div>
          <div className="metric application"><span>Fluent</span><progress max="1" value={row.fluent_pct} aria-label={`${row.code} fluent`} /><b>{percent(row.fluent_pct)}</b></div>
        </div>)}
        <p className="muted">{counts.fluent} fluent · {counts.practiced} practiced · {counts.introduced} introduced · {counts.new} new. Fluent = used correctly in 2+ real situations.</p>
      </div>
      <div className="resume">
        <span className="tag">CONTINUE WHERE YOU LEFT OFF</span>
        {resume ? <>
          <h3>{resume.title}</h3>
          <div className="metric"><span>Step {resume.step}/{resume.steps}</span><progress max={resume.steps} value={resume.step} /></div>
          <div className="lesson-actions">
            <button disabled={busy || resume.step >= resume.steps} onClick={() => post('progress', { step: resume.step + 1 })}>Next step</button>
            <button className="primary" disabled={busy} onClick={() => post('complete', { score: 0.9 })}>Pass lesson (90%)</button>
            <button disabled={busy} onClick={() => post('complete', { score: 0.5 })}>Fail (50%)</button>
          </div>
        </> : <p className="muted">No lesson in progress.</p>}
        <ol className="lessons">{progress.lessons.map(l => <li key={l.id} className={`lesson-${l.status}`}>{l.level} · {l.title}<small>{l.status}{l.best_score !== null ? ` · ${percent(l.best_score)}` : ''}</small></li>)}</ol>
      </div>
    </div>
    <div className="chips" aria-label="Vocabulary">{words.map(w => <span key={w.id} className={`chip word-${w.status}`} title={`${w.meaning} · ${lessonTitles[w.lesson_id]} · ${w.real_uses} real uses${w.struggles ? ` · struggled ${w.struggles}×` : ''}`}>{w.lemma}</span>)}</div>
  </section>;
}

function ActivitiesCard({ activities, busy, locked, action }: { activities: Activity_[]; busy: boolean; locked: boolean; action: (path: string, body?: unknown) => Promise<void> }) {
  return <section className="card">
    <p className="eyebrow">YOUR ACTIVITIES · SPEAK SPANISH WHEREVER YOU GO</p>
    <div className="activities">{activities.map(a => <article key={a.id} className={`activity${a.is_current ? ' current' : ''}`}>
      <div className="activity-head"><h3>{a.title}</h3>{a.is_current && <span className="tag">NOW</span>}</div>
      <p className="muted">{a.when} · {a.with_whom}</p>
      <p className="pickup">{a.pick_up}</p>
      {a.can_say.length > 0 && <><b className="mini">You can already say</b><ul className="phrases">{a.can_say.slice(0, 3).map(p => <li key={p.text}>{p.text}<small>{p.meaning}</small></li>)}</ul></>}
      {a.almost.length > 0 && <><b className="mini">Almost — learn one word</b><ul className="phrases almost">{a.almost.slice(0, 2).map(p => <li key={p.text}>{p.text}<small>learn: {p.missing?.join(', ')}</small></li>)}</ul></>}
      {a.practice.length > 0 && <div className="chips">{a.practice.map(w => <span key={w.id} className={`chip word-${w.status}`} title={w.meaning}>{w.lemma}{w.struggles ? ' ⚠' : ''}</span>)}</div>}
      {a.lesson && <p className="muted">{a.lesson.status === 'locked' ? 'Coming up in your course' : 'Before you go'}: <b>{a.lesson.title}</b> teaches {a.lesson.words.slice(0, 3).join(', ')}</p>}
      <button disabled={busy || locked} onClick={() => action(`/api/activities/${a.id}/start`)}>{a.is_current ? 'Practice here again' : "I'm going now"}</button>
    </article>)}</div>
  </section>;
}
