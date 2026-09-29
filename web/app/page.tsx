'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Background, Controls, ReactFlow } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { API, request } from '../lib/api';
import type { Activity, Graph, State } from '../lib/types';

const stages = ['context', 'opportunity', 'mission', 'plaud', 'reflection', 'graph', 'adapt'];
const percent = (n: number) => `${Math.round(n * 100)}%`;

export default function Home() {
  const [state, setState] = useState<State | null>(null);
  const [graph, setGraph] = useState<Graph>({ nodes: [], edges: [] });
  const [events, setEvents] = useState<Activity[]>([]);
  const [error, setError] = useState('');
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    const [s, g] = await Promise.all([request<State>('/api/state'), request<Graph>('/api/graph')]);
    setState(s); setGraph(g);
  }, []);
  useEffect(() => {
    refresh().catch(e => setError(e.message));
    const source = new EventSource(`${API}/api/events`);
    source.onopen = () => { setConnected(true); refresh().catch(e => setError(e.message)); };
    source.onerror = () => setConnected(false);
    source.onmessage = ({ data }) => {
      const event: Activity = JSON.parse(data);
      setEvents(previous => event.data.reset ? [event] : [...previous.filter(e => e.id !== event.id), event].slice(-100));
      if (event.type === 'error') setError(event.message);
      if (['context.added', 'mission.created', 'graph.updated', 'learning_path.adapted'].includes(event.type)) refresh().catch(e => setError(e.message));
    };
    return () => source.close();
  }, [refresh]);
  async function action(path: string) {
    setBusy(true); setError('');
    try { await request(path, 'POST'); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Request failed'); }
    finally { setBusy(false); }
  }
  const nodes = useMemo(() => graph.nodes.map((node, index) => ({
    ...node, type: 'default', position: { x: (index % 3) * 270, y: Math.floor(index / 3) * 130 },
    data: { ...node.data, label: <div><small>{node.type}</small><strong>{node.data.label}</strong>{node.data.knowledge !== undefined && <span>K {percent(node.data.knowledge)} · A {percent(node.data.application || 0)}</span>}</div> },
    className: `graph-${node.type}`,
  })), [graph]);
  const edges = useMemo(() => graph.edges.map(edge => ({ ...edge, animated: edge.label === 'REVEALED' || edge.label === 'DEMONSTRATED' })), [graph]);
  const mission = state?.active_mission;
  return <main>
    <header><div><p className="eyebrow">LEARN. APPLY. ADAPT.</p><h1>ApplyLoop<span>↗</span></h1><p>Turn knowledge into real-world experience.</p></div><button disabled={busy} onClick={() => action('/api/demo/reset')}>Reset demo</button></header>
    <div className="banner"><span>DEMO / SIMULATED SERVICES</span> In-memory graph · fixture agents · prerecorded replay <b className={connected ? 'online' : 'offline'}>{connected ? '● Live event stream' : '○ Connecting to backend…'}</b></div>
    {error && <p className="error" role="alert">{error}</p>}
    {!state ? <section className="card"><h2>Connecting to ApplyLoop</h2><p>Start the backend with <code>make api</code> and this screen will connect automatically.</p></section> : <>
      <div className="top-grid">
        <section className="card"><p className="eyebrow">YOUR LEARNING STATE</p><h2>{state.goal}</h2>{state.skills.map(skill => <div className="skill" key={skill.id}><h3>{skill.name}</h3><div className="metric"><span>Knowledge</span><progress max="1" value={skill.knowledge_score} aria-label={`${skill.name} knowledge`} /><b>{percent(skill.knowledge_score)}</b></div><div className="metric application"><span>Application</span><progress max="1" value={skill.application_score} aria-label={`${skill.name} application`} /><b>{percent(skill.application_score)}</b></div></div>)}</section>
        <section className="card context"><p className="eyebrow">TODAY’S OPPORTUNITY</p><h2>{state.context.title}</h2><p>{state.context.location} {state.context.starts_at && '· 7:00 PM'}</p><div className="mission"><span className="tag">{mission ? mission.status.toUpperCase() : 'READY TO PRACTICE'}</span><h3>{mission?.title || 'You know it. Now try it.'}</h3><p>{mission?.challenge || 'Find a small practice mission based on what you know and where you’re going.'}</p>{mission && <p className="muted">{mission.reason}</p>}</div><button className="primary" disabled={busy || !!mission} onClick={() => action('/api/opportunity')}>Find opportunity ↗</button>{mission?.status === 'assigned' && <button className="replay" disabled={busy} onClick={() => action('/api/demo/replay')}>Play prerecorded demo replay</button>}<p className="muted">Replay simulates the evidence loop. Live PLAUD requires the bridge workstream.</p></section>
      </div>
      <section className="card"><p className="eyebrow">THE APPLICATION LOOP</p><div className="pipeline">{stages.map(stage => { const latest = [...events].reverse().find(e => e.stage === stage); const status = latest?.status || (stage === 'context' ? 'completed' : 'idle'); return <div key={stage} className={status}><span>{status === 'completed' ? '✓' : status === 'error' ? '!' : status === 'processing' ? '◉' : '○'}</span><b>{stage}</b></div>; })}</div></section>
      {state.next_target && <section className="next-target"><span>↗ NEXT LEARNING TARGET</span><h2>{state.skills.find(s => s.id === state.next_target?.skill_id)?.name}</h2><p>{state.next_target.reason}</p></section>}
      <div className="bottom-grid"><section className="card"><p className="eyebrow">LEARNING GRAPH / DEMO MEMORY STORE</p><div className="graph" aria-label="Learning relationships"><ReactFlow key={graph.nodes.length} nodes={nodes} edges={edges} fitView nodesDraggable nodesConnectable={false} minZoom={0.2}><Background /><Controls /></ReactFlow></div></section><section className="card"><p className="eyebrow">AGENT ACTIVITY</p><div className="timeline" aria-live="polite">{events.length === 0 && <p className="muted">Your application story will appear here. Find an opportunity to start.</p>}{[...events].reverse().map(e => <article key={e.id}><time>{new Date(e.created_at).toLocaleTimeString()}</time><div><b>{e.stage}</b><p>{e.message}</p></div></article>)}</div></section></div>
    </>}
    <footer>ApplyLoop · Hackathon starter · Real integrations owned by your team</footer>
  </main>;
}
