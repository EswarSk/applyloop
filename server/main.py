import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from uuid import uuid4
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from server import band_client
from server.demo_seed import SAMPLE_TRANSCRIPT
from server.event_bus import EventBus
from server.neo4j_store import DemoStore
from server.schemas import ContextInput, RecordingInput, TranscriptInput
from server.settings import DEMO_MODE, INTERNAL_API_TOKEN, WEB_ORIGIN

store, bus = DemoStore(), EventBus()
# ponytail: serialize one demo learner; use DB transactions for multi-worker/live mode.
lock = asyncio.Lock()
pending_opportunity = None

@asynccontextmanager
async def lifespan(app):
    if not DEMO_MODE:
        raise RuntimeError('Live mode requires the Neo4j and BAND adapters. See docs/TEAM_TASKS.md.')
    yield

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
        await bus.publish('context.added', 'context', 'completed', 'Demo reset — simulated services', {'reset': True})
    return store.state()

@app.post('/api/context')
async def context(payload: ContextInput):
    async with lock:
        if store.data['active_mission']:
            raise HTTPException(409, 'Reset before changing a mission context')
        store.data['context'] = {'id': f'context-{uuid4().hex[:12]}', **payload.model_dump()}
        await bus.publish('context.added', 'context', 'completed', payload.title)
    return store.state()['context']

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
        if pending_opportunity or store.data['active_mission']:
            raise HTTPException(409, 'Reset before creating another demo mission')
        pending_opportunity = f'opp-{uuid4().hex[:12]}'
        request_id = pending_opportunity
        background.add_task(create_opportunity, request_id)
    return {'request_id': request_id, 'status': 'processing'}

async def process_transcript(payload):
    if payload.recording_id in store.experiences:
        return {'status': 'duplicate'}
    recording = store.recordings.get(payload.recording_id)
    mission = store.data['active_mission']
    if not recording or not mission or recording['mission_id'] != mission['id']:
        raise HTTPException(404, 'Unknown recording; register it against the active mission first')
    if mission['status'] != 'assigned':
        raise HTTPException(409, 'Mission already completed')
    await bus.publish('plaud.transcript.ready', 'plaud', 'completed', 'Transcript received (demo reflection uses fixed fixture)')
    await bus.publish('reflection.started', 'reflection', 'processing', 'Simulated reflection — no live BAND/model call')
    try:
        result = await band_client.reflection(mission, payload.recording_id, payload.transcript)
        await bus.publish('reflection.completed', 'reflection', 'completed', 'Demo reflection validated')
        await bus.publish('graph.updating', 'graph', 'processing', 'Applying deterministic score update')
        store.apply(result, payload.transcript)
        await bus.publish('graph.updated', 'graph', 'completed', 'In-memory demo graph updated: application +14%')
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
        rid = 'demo-replay-001'
        if rid in store.experiences:
            return {'status': 'duplicate'}
        try:
            store.record(rid, 'Prerecorded demo replay — simulated PLAUD')
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        await bus.publish('plaud.recording.detected', 'plaud', 'completed', 'Prerecorded replay — not a live PLAUD recording')
        return await process_transcript(TranscriptInput(recording_id=rid, transcript=SAMPLE_TRANSCRIPT))

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
