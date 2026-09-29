import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import uuid4
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from server import band_client
from server.agents.reflection_agent import demo_transcript
from server.demo_seed import SAMPLE_TRANSCRIPT
from server.event_bus import EventBus
from server.learning_rules import reflection_delta, curriculum
from server.memory_store import DemoStore
from server.neo4j_store import Neo4jStore
from server.schemas import BridgeStatusInput, ContextInput, LessonCompleteInput, LessonProgressInput, RecordingInput, TranscriptInput
from server.settings import (BAND_MODE, DEMO_MODE, INTERNAL_API_TOKEN, LEARNER_ID, NEO4J_DATABASE, NEO4J_PASSWORD, NEO4J_URI,
                             NEO4J_USERNAME, WEB_ORIGIN)

# NEO4J_URI set -> real Neo4j knowledge graph; otherwise the in-memory store (same interface and rules).
store = Neo4jStore(NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE, LEARNER_ID) if NEO4J_URI else DemoStore()
bus = EventBus()
# ponytail: serialize one demo learner; use DB transactions for multi-worker/live mode.
lock = asyncio.Lock()
pending_opportunity = None
plaud_connected = False

@asynccontextmanager
async def lifespan(app):
    if not DEMO_MODE and not NEO4J_URI:
        raise RuntimeError('Live storage requires NEO4J_URI and NEO4J_PASSWORD')
    try:
        store.connect()
        async with band_client.lifecycle():
            background = BackgroundTasks()
            mission = store.active_mission()
            if mission and mission['status'] == 'completed':
                queue_opportunity(background)
            startup_job = asyncio.create_task(background())
            try:
                yield
            finally:
                startup_job.cancel()
                await asyncio.gather(startup_job, return_exceptions=True)
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
    return {**store.state(), 'opportunity_pending': pending_opportunity is not None,
            'last_feedback': store.feedback(), 'services': {'band': BAND_MODE, 'storage': store.backend,
                                        'plaud': 'connected' if plaud_connected else 'disconnected'}}

@app.get('/api/graph')
async def graph():
    return store.graph()

@app.post('/api/demo/reset')
async def reset():
    if not DEMO_MODE:
        raise HTTPException(404)
    global pending_opportunity
    async with lock:
        pending_opportunity = None
        store.reset()
        bus.clear()
        await bus.publish('context.added', 'context', 'completed', f'Demo learner reset — {store.backend} graph reseeded', {'reset': True})
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

def learner_snapshot():
    # Saved activities stand in for future calendar/location inputs.
    return {**store.state(), 'progress': store.progress(), 'activities': store.activities(),
            'current_time': datetime.now().astimezone().isoformat()}


def queue_opportunity(background):
    global pending_opportunity
    if not pending_opportunity:
        pending_opportunity = f'opp-{uuid4().hex[:12]}'
        background.add_task(create_opportunity, pending_opportunity)
    return pending_opportunity


async def create_opportunity(request_id):
    global pending_opportunity
    async with lock:
        if pending_opportunity != request_id:
            return
        try:
            await bus.publish('opportunity.started', 'opportunity', 'processing', 'BAND matching skill to context' if BAND_MODE == 'live' else 'Demo agent matching skill to context')
            snapshot = learner_snapshot()
            mission = await band_client.opportunity(snapshot)
            previous = snapshot['active_mission']
            if mission.context_id != snapshot['context']['id'] or previous:
                if mission.context_id in {a['id'] for a in snapshot['activities']}:
                    context = store.start_activity(mission.context_id)
                else:
                    context = store.set_context(snapshot['context'])
                await bus.publish('context.added', 'context', 'completed', f"Next practice context: {context['title']}")
            store.assign(mission)
            await bus.publish('opportunity.completed', 'opportunity', 'completed', 'BAND opportunity validated' if BAND_MODE == 'live' else 'Demo opportunity found')
            await bus.publish('mission.created', 'mission', 'completed', mission.challenge)
            await bus.publish('plaud.waiting', 'plaud', 'processing', 'Waiting for a new PLAUD recording; start make plaud')
        except Exception as exc:
            await bus.publish('error', 'opportunity', 'error', str(exc))
        finally:
            pending_opportunity = None

@app.post('/api/opportunity', status_code=202)
async def opportunity(background: BackgroundTasks):
    global pending_opportunity
    async with lock:
        mission = store.active_mission()
        if pending_opportunity or mission and mission['status'] == 'assigned':
            raise HTTPException(409, 'An opportunity is being prepared or a mission is already assigned')
        request_id = queue_opportunity(background)
    return {'request_id': request_id, 'status': 'processing'}

async def process_transcript(payload, background):
    if store.has_experience(payload.recording_id):
        return {'status': 'duplicate'}
    recording = store.recording(payload.recording_id)
    if recording and recording.get('status') == 'irrelevant':
        return {'status': 'irrelevant'}
    mission = store.active_mission()
    if not recording or not mission or recording['mission_id'] != mission['id']:
        raise HTTPException(404, 'Unknown recording; register it against the active mission first')
    if mission['status'] != 'assigned':
        raise HTTPException(409, 'Mission already completed')
    await bus.publish('plaud.transcript.ready', 'plaud', 'completed', 'Transcript received' if BAND_MODE == 'live' else 'Transcript received (demo reflection uses fixed fixture)')
    await bus.publish('reflection.started', 'reflection', 'processing', 'BAND analyzing transcript evidence' if BAND_MODE == 'live' else 'Simulated reflection — no live BAND/model call')
    try:
        result = await band_client.reflection(mission, payload.recording_id, payload.transcript,
                                              store.state()['skills'], curriculum()['words'], learner=learner_snapshot())
        if not result.relevant:
            store.dismiss_recording(payload.recording_id, result.relevance_reason)
            await bus.publish('reflection.filtered', 'reflection', 'completed',
                              'Recording excluded from learning progress: ' + result.relevance_reason)
            await bus.publish('plaud.waiting', 'plaud', 'processing', 'Waiting for relevant practice evidence; the assigned mission remains active')
            return {'status': 'irrelevant', 'reflection': result.model_dump()}
        await bus.publish('reflection.completed', 'reflection', 'completed', 'BAND reflection validated' if BAND_MODE == 'live' else 'Demo reflection validated')
        await bus.publish('graph.updating', 'graph', 'processing', 'Applying deterministic score + vocabulary update')
        vocab = store.apply(result, payload.transcript)
        delta = round(reflection_delta(result) * 100)
        await bus.publish('graph.updated', 'graph', 'completed', f'{store.backend} graph updated: application +{delta}%')
        if vocab:
            await bus.publish('vocabulary.updated', 'graph', 'completed', vocabulary_message(vocab), vocab)
        await bus.publish('learning_path.adapted', 'adapt', 'completed', result.next_target.reason)
    except Exception as exc:
        await bus.publish('error', 'reflection', 'error', str(exc))
        raise HTTPException(502, str(exc)) from exc
    queue_opportunity(background)
    return {'status': 'completed', 'reflection': result.model_dump()}

@app.post('/api/internal/plaud/recording', dependencies=[Depends(internal_token)])
async def recording(payload: RecordingInput, x_expected_mission_id: str | None = Header(default=None)):
    global plaud_connected
    async with lock:
        if x_expected_mission_id is not None:
            mission = store.active_mission()
            registered = store.recording(payload.recording_id)
            if (registered and registered['mission_id'] != x_expected_mission_id
                    or not registered and (not mission or mission['id'] != x_expected_mission_id)):
                raise HTTPException(409, 'Recording belongs to a different mission; pending delivery retained')
        try:
            created = store.record(payload.recording_id, payload.title)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if created:
            plaud_connected = x_expected_mission_id is not None or plaud_connected
            await bus.publish('plaud.recording.detected', 'plaud', 'completed', payload.title, {'recording_id': payload.recording_id})
        return {'status': 'detected' if created else 'duplicate'}

@app.post('/api/internal/plaud/status', dependencies=[Depends(internal_token)])
async def bridge_status(payload: BridgeStatusInput):
    global plaud_connected
    plaud_connected = payload.status != 'error'
    event = {'waiting': 'plaud.waiting', 'transcript_waiting': 'plaud.transcript.waiting', 'error': 'error'}[payload.status]
    await bus.publish(event, 'plaud', 'error' if payload.status == 'error' else 'processing', payload.message)
    return {'status': 'accepted'}

@app.post('/api/internal/plaud/transcript', dependencies=[Depends(internal_token)])
async def transcript(payload: TranscriptInput, background: BackgroundTasks):
    async with lock:
        return await process_transcript(payload, background)

@app.post('/api/demo/replay')
async def replay(background: BackgroundTasks):
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
        return await process_transcript(TranscriptInput(recording_id=rid, transcript=transcript), background)

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
