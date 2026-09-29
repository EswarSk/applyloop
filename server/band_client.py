"""Two local remote agents; BAND routes requests/replies, backend owns mutations."""
import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
from band import Agent
from band.core import SimpleAdapter
from band.client.rest import aclose_rest_client
from band.runtime.tools import AgentTools
from band.runtime.types import SessionConfig

from server.settings import BAND_MODE
from server.agents.opportunity_agent import demo_opportunity, live_opportunity
from server.agents.reflection_agent import demo_reflection, live_reflection
from server.schemas import Mission, ReflectionResult

logger = logging.getLogger(__name__)
runtime = None
MARKER = 'APPLYLOOP/1\n'


@dataclass
class Job:
    kind: str
    payload: dict
    future: asyncio.Future
    reply: dict | None = None


class ApplyLoopAdapter(SimpleAdapter):
    def __init__(self, owner, kind):
        super().__init__()
        self.owner, self.kind, self.rest = owner, kind, None

    async def on_started(self, agent_name, agent_description):
        await super().on_started(agent_name, agent_description)
        self.rest = self.build_rest_client()

    async def cleanup_all(self):
        if self.rest:
            await aclose_rest_client(self.rest)
            self.rest = None

    async def on_message(self, msg, tools, history, participants_msg, contacts_msg,
                         *, is_session_bootstrap, room_id):
        peer = 'reflection' if self.kind == 'opportunity' else 'opportunity'
        if (room_id != self.owner.room_id or msg.sender_id != self.owner.ids[peer]
                or msg.message_type != 'text' or MARKER not in msg.content):
            return
        try:
            packet = json.loads(msg.content.split(MARKER, 1)[1])
            job = self.owner.pending.get(packet.get('id'))
        except (ValueError, AttributeError, TypeError):
            return
        # Only application-created requests can spend tokens or complete futures.
        if not job or job.future.done():
            return
        if packet.get('type') == 'result' and job.kind == peer:
            if packet != job.reply:
                return
            if packet.get('error'):
                job.future.set_exception(RuntimeError(packet['error']))
            else:
                job.future.set_result(packet['result'])
        elif packet.get('type') == 'request' and job.kind == self.kind:
            if job.reply is None:
                reply = {'id': packet['id'], 'type': 'result'}
                try:
                    await tools.send_event(content=f'ApplyLoop {self.kind}: analyzing supplied evidence/context', message_type='task')
                    result = (await live_opportunity(job.payload, self.owner.model_client)
                              if self.kind == 'opportunity'
                              else await live_reflection(job.payload, self.owner.model_client))
                    reply['result'] = result.model_dump()
                    await tools.send_event(content=f'ApplyLoop {self.kind}: structured result validated', message_type='task')
                except Exception as exc:
                    # Validation errors can echo transcript text; report only the category.
                    reply['error'] = f'BAND {self.kind} failed ({type(exc).__name__}); check credentials, model output, and connectivity'
                    if type(exc) is ValueError and str(exc).startswith(('Reflection ', 'Irrelevant recording ')):
                        reply['error'] = str(exc)
                    logger.warning('%s', reply['error'])
                job.reply = reply
            await tools.send_message(MARKER + json.dumps(job.reply), mentions=[{'id': msg.sender_id}])


class BandRuntime:
    def __init__(self):
        required = ['OPENAI_API_KEY', 'BAND_OPPORTUNITY_AGENT_ID', 'BAND_OPPORTUNITY_API_KEY',
                    'BAND_REFLECTION_AGENT_ID', 'BAND_REFLECTION_API_KEY']
        missing = [key for key in required if not os.getenv(key)]
        if missing:
            raise RuntimeError('Missing live BAND configuration: ' + ', '.join(missing))
        self.ids = {kind: str(UUID(os.environ[f'BAND_{kind.upper()}_AGENT_ID'])) for kind in ('opportunity', 'reflection')}
        if len(set(self.ids.values())) != 2:
            raise ValueError('BAND agents must use different IDs')
        self.timeout = float(os.getenv('BAND_REQUEST_TIMEOUT_SECONDS', '120'))
        if not 0 < self.timeout <= 600:
            raise ValueError('BAND_REQUEST_TIMEOUT_SECONDS must be >0 and <=600')
        self.room_id = os.getenv('BAND_ROOM_ID') or None
        if self.room_id:
            self.room_id = str(UUID(self.room_id))
        self.pending = {}
        self.adapters = {kind: ApplyLoopAdapter(self, kind) for kind in self.ids}
        self.agents = {kind: Agent.create(adapter=self.adapters[kind], agent_id=agent_id,
            api_key=os.environ[f'BAND_{kind.upper()}_API_KEY'],
            session_config=SessionConfig(max_cycle_seconds=self.timeout)) for kind, agent_id in self.ids.items()}

    @asynccontextmanager
    async def connect(self):
        async with AsyncExitStack() as stack:
            self.model_client = await stack.enter_async_context(httpx.AsyncClient(timeout=min(self.timeout, 90)))
            for agent in self.agents.values():
                await asyncio.wait_for(agent.start(), self.timeout)
                stack.push_async_callback(agent.stop)
            tools = AgentTools(self.room_id or '', self.adapters['opportunity'].rest, agent_id=self.ids['opportunity'])
            if not self.room_id:
                self.room_id = await tools.create_chatroom()
                tools.room_id = self.room_id
            await tools.add_participant(self.ids['reflection'])
            # Verifies membership/visibility with the second identity too.
            await AgentTools(self.room_id, self.adapters['reflection'].rest).get_participants()
            logger.warning('ApplyLoop BAND room ID: %s', self.room_id)
            try:
                yield self
            finally:
                for job in self.pending.values():
                    job.future.cancel()
                self.pending.clear()

    async def invoke(self, kind, payload):
        sender = 'reflection' if kind == 'opportunity' else 'opportunity'
        request_id = uuid4().hex
        job = Job(kind, payload, asyncio.get_running_loop().create_future())
        self.pending[request_id] = job
        try:
            async with asyncio.timeout(self.timeout):
                tools = AgentTools(self.room_id, self.adapters[sender].rest, agent_id=self.ids[sender])
                await tools.send_message(MARKER + json.dumps({'id': request_id, 'type': 'request'}),
                                         mentions=[{'id': self.ids[kind]}])
                return await job.future
        except TimeoutError as exc:
            raise RuntimeError(f'BAND {kind} timed out; check agent connectivity and room membership') from exc
        finally:
            self.pending.pop(request_id, None)
            if not job.future.done():
                job.future.cancel()


@asynccontextmanager
async def lifecycle():
    global runtime
    if BAND_MODE == 'demo':
        yield
        return
    live = BandRuntime()
    async with live.connect():
        runtime = live
        try:
            yield
        finally:
            runtime = None


async def opportunity(state):
    if BAND_MODE == 'demo':
        return await demo_opportunity(state)
    if runtime is None:
        raise RuntimeError('Live BAND runtime is not connected')
    return Mission.model_validate(await runtime.invoke('opportunity', state))


async def reflection(mission, recording_id, transcript, skills=None, words=None, learner=None):
    if BAND_MODE == 'demo':
        return await demo_reflection(mission, recording_id, transcript)
    if runtime is None:
        raise RuntimeError('Live BAND runtime is not connected')
    return ReflectionResult.model_validate(await runtime.invoke('reflection', {
        'mission': mission, 'recording_id': recording_id, 'transcript': transcript, 'skills': skills or [], 'words': words or [], 'learner': learner or {}}))
