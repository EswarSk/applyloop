import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from uuid import uuid4
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from server import band_client
from server.agents.reflection_agent import demo_transcript
from server.demo_seed import SAMPLE_TRANSCRIPT
from server.event_bus import EventBus
from server.learning_rules import application_delta, curriculum
from server.memory_store import DemoStore
from server.neo4j_store import Neo4jStore
from server.schemas import ContextInput, LessonCompleteInput, LessonProgressInput, RecordingInput, TranscriptInput
from server.settings import (DEMO_MODE, INTERNAL_API_TOKEN, LEARNER_ID, NEO4J_DATABASE, NEO4J_PASSWORD, NEO4J_URI,
                             NEO4J_USERNAME, WEB_ORIGIN)

# NEO4J_URI set -> real Neo4j knowledge graph; otherwise the in-memory store (same interface and rules).
store = Neo4jStore(NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE, LEARNER_ID) if NEO4J_URI else DemoStore()
bus = EventBus()
GRAPH_LABEL = 'Neo4j graph' if store.backend == 'neo4j' else 'In-memory demo graph'
# ponytail: serialize one demo learner; use DB transactions for multi-worker/live mode.
lock = asyncio.Lock()
pending_opportunity = None

@asynccontextmanager
async def lifespan(app):
    if not DEMO_MODE:
        raise RuntimeError('Live mode requires the BAND agent adapters. See docs/TEAM_TASKS.md.')
    store.connect()  # Neo4j: verify connectivity, create constraints, seed if empty
    try:
        yield
    finally:
        store.close()

app = FastAPI(title='ApplyLoop', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[WEB_ORIGIN], allow_methods=['GET', 'POST'], allow_headers=['Content-Type', 'X-Internal-Token'])

async def internal_token(x_internal_token: str = Header(default='')):
    if not INTERNAL_API_TOKEN or not secrets.compare_digest(x_internal_token, INTERNAL_API_TOKEN):
        raise HTTPException(401, 'Invalid internal token')

@app.get('/health')
async def health():
    return {'status': 'ok'}

@app.get('/api/state')
async def state():
    return store.state()

@app.get('/api/graph')
async def graph():
    return store.graph()

@app.post('/api/demo/reset')
async def reset():
    global pending_opportunity
    async with lock:
        pending_opportunity = None
        store.reset()
        bus.clear()
        await bus.publish('context.added', 'context', 'completed', f'Demo reset — {GRAPH_LABEL} reseeded, simulated agents', {'reset': True})
    return store.state()

@app.post('/api/context')
async def context(payload: ContextInput):
    async with lock:
        try:
            context = store.set_context({'id': f'context-{uuid4().hex[:12]}', **payload.model_dump()})
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        await bus.publish('context.added', 'context', 'completed', payload.title)
    return context

async def create_opportunity(request_id):
    global pending_opportunity
    async with lock:
        if pending_opportunity != request_id:
            return
        try:
            await bus.publish('opportunity.started', 'opportunity', 'processing', 'Demo agent matching skill to context')
            mission = await band_client.opportunity(store.state())
            store.assign(mission)
            await bus.publish('opportunity.completed', 'opportunity', 'completed', 'Demo opportunity found')
            await bus.publish('mission.created', 'mission', 'completed', mission.challenge)
            await bus.publish('plaud.waiting', 'plaud', 'processing', 'Waiting for evidence — replay available in demo mode')
        except Exception as exc:
            await bus.publish('error', 'opportunity', 'error', str(exc))
        finally:
            pending_opportunity = None

@app.post('/api/opportunity', status_code=202)
async def opportunity(background: BackgroundTasks):
    global pending_opportunity
    async with lock:
        if pending_opportunity or store.active_mission():
            raise HTTPException(409, 'Reset before creating another demo mission')
        pending_opportunity = f'opp-{uuid4().hex[:12]}'
        request_id = pending_opportunity
        background.add_task(create_opportunity, request_id)
    return {'request_id': request_id, 'status': 'processing'}

async def process_transcript(payload):
    if store.has_experience(payload.recording_id):
        return {'status': 'duplicate'}
    recording = store.recording(payload.recording_id)
    mission = store.active_mission()
    if not recording or not mission or recording['mission_id'] != mission['id']:
        raise HTTPException(404, 'Unknown recording; register it against the active mission first')
    if mission['status'] != 'assigned':
        raise HTTPException(409, 'Mission already completed')
    await bus.publish('plaud.transcript.ready', 'plaud', 'completed', 'Transcript received (demo reflection uses fixed fixture)')
    await bus.publish('reflection.started', 'reflection', 'processing', 'Simulated reflection — no live BAND/model call')
    try:
        result = await band_client.reflection(mission, payload.recording_id, payload.transcript)
        await bus.publish('reflection.completed', 'reflection', 'completed', 'Demo reflection validated')
        await bus.publish('graph.updating', 'graph', 'processing', 'Applying deterministic score + vocabulary update')
        vocab = store.apply(result, payload.transcript)
        delta = round(application_delta(result.success_score) * 100)
        await bus.publish('graph.updated', 'graph', 'completed', f'{GRAPH_LABEL} updated: application +{delta}%')
        if vocab:
            await bus.publish('vocabulary.updated', 'graph', 'completed', vocabulary_message(vocab), vocab)
        await bus.publish('learning_path.adapted', 'adapt', 'completed', result.next_target.reason)
    except Exception as exc:
        await bus.publish('error', 'reflection', 'error', str(exc))
        raise HTTPException(502, str(exc)) from exc
    return {'status': 'completed', 'reflection': result.model_dump()}

@app.post('/api/internal/plaud/recording', dependencies=[Depends(internal_token)])
async def recording(payload: RecordingInput):
    async with lock:
        try:
            created = store.record(payload.recording_id, payload.title)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if created:
            await bus.publish('plaud.recording.detected', 'plaud', 'completed', payload.title, {'recording_id': payload.recording_id})
        return {'status': 'detected' if created else 'duplicate'}

@app.post('/api/internal/plaud/transcript', dependencies=[Depends(internal_token)])
async def transcript(payload: TranscriptInput):
    async with lock:
        return await process_transcript(payload)

@app.post('/api/demo/replay')
async def replay():
    if not DEMO_MODE:
        raise HTTPException(404)
    async with lock:
        mission = store.active_mission()
        if not mission:
            raise HTTPException(409, 'Find an opportunity first: the replay is evidence for a mission')
        rid = f"replay-{mission['id']}"
        if store.has_experience(rid):
            return {'status': 'duplicate'}
        title = f"Prerecorded replay at {store.state()['context']['title']} — simulated PLAUD"
        try:
            store.record(rid, title)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        await bus.publish('plaud.recording.detected', 'plaud', 'completed', f'{title} — not a live PLAUD recording')
        transcript = demo_transcript(mission['skill_id'], SAMPLE_TRANSCRIPT)
        return await process_transcript(TranscriptInput(recording_id=rid, transcript=transcript))

def vocabulary_message(vocab):
    lemmas = {w['id']: w['lemma'] for w in curriculum()['words']}
    names = lambda ids: ', '.join(lemmas[i] for i in ids)
    parts = []
    if vocab['newly_fluent']:
        parts.append(f"now fluent: {names(vocab['newly_fluent'])}")
    if vocab['words_struggled']:
        parts.append(f"struggled with: {names(vocab['words_struggled'])}")
    if vocab['words_used'] and not vocab['newly_fluent']:
        parts.append(f"used: {names(vocab['words_used'])}")
    return 'Vocabulary — ' + ('; '.join(parts) or 'no word evidence')

# ---------- activities: Spanish class, restaurant, dance class ----------
@app.get('/api/activities')
async def activities():
    """Per activity: where you left off there, what you can already say, what to practice or learn next."""
    return store.activities()

@app.post('/api/activities/{activity_id}/start')
async def start_activity(activity_id: str):
    """'I'm heading there now' — makes this activity the context for the next mission."""
    async with lock:
        if pending_opportunity:
            raise HTTPException(409, 'An opportunity is being prepared; try again in a moment')
        try:
            context = store.start_activity(activity_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        await bus.publish('context.added', 'context', 'completed', f"Heading to {context['title']}")
    return context

# ---------- learner knowledge graph: level, words, lessons, resume point ----------
@app.get('/api/learner/progress')
async def learner_progress():
    return store.progress()

async def lesson_action(fn, *args):
    try:
        return fn(*args)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

@app.post('/api/lessons/{lesson_id}/progress')
async def lesson_progress(lesson_id: str, payload: LessonProgressInput):
    """Save where the learner stopped (resume point). Starting a lesson introduces its words."""
    async with lock:
        progress = await lesson_action(store.save_step, lesson_id, payload.step)
        resume = progress['resume']
        await bus.publish('lesson.progress', 'learn', 'completed', f"Saved resume point: {resume['title']} · step {resume['step']}/{resume['steps']}")
        return progress

@app.post('/api/lessons/{lesson_id}/complete')
async def lesson_complete(lesson_id: str, payload: LessonCompleteInput):
    """Pass (score >= 0.7) marks the lesson's words practiced and moves the resume point to the next lesson."""
    async with lock:
        outcome = await lesson_action(store.complete_lesson, lesson_id, payload.score)
        title = next(l['title'] for l in outcome['progress']['lessons'] if l['id'] == lesson_id)
        verdict = 'passed' if outcome['passed'] else 'not passed yet — resume point reset to step 0'
        await bus.publish('lesson.completed', 'learn', 'completed', f"{title}: {round(payload.score * 100)}% {verdict}. Level {outcome['progress']['level']['current']}")
        return outcome

@app.get('/api/events')
async def events():
    async def stream():
        queue = asyncio.Queue(maxsize=100)
        bus.subscribers.add(queue)
        try:
            for event in list(bus.history):
                yield f'id: {event["id"]}\ndata: {json.dumps(event)}\n\n'
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f'id: {event["id"]}\ndata: {json.dumps(event)}\n\n'
                except asyncio.TimeoutError:
                    yield ': heartbeat\n\n'
        finally:
            bus.subscribers.discard(queue)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
