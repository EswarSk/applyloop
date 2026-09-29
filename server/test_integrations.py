"""Runnable integration proof with simulated vendor transport; no accounts or network."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError
from band.runtime.tools import AgentTools

from server import band_client, main, plaud_bridge as bridge
from server.agents.opportunity_agent import live_opportunity
from server.agents.reflection_agent import live_reflection
from server.agents.model import strict_schema
from server.demo_seed import example, SAMPLE_TRANSCRIPT
from server.settings import INTERNAL_API_TOKEN
from server.schemas import Mission

ENV = {'OPENAI_API_KEY': 'test-provider-key',
       'BAND_OPPORTUNITY_AGENT_ID': '00000000-0000-4000-8000-000000000001',
       'BAND_REFLECTION_AGENT_ID': '00000000-0000-4000-8000-000000000002',
       'BAND_OPPORTUNITY_API_KEY': 'test-opportunity-key',
       'BAND_REFLECTION_API_KEY': 'test-reflection-key'}


def reflection_result(payload):
    result = example('reflection')
    result.update(mission_id=payload['mission']['id'], recording_id=payload['recording_id'])
    result['demonstrated'][0]['evidence'] = 'I ordered my food and asked for water in Spanish.'
    result['gaps'][0]['evidence'] = 'I could not understand the question and switched to English.'
    return result


def model_response(request):
    body = json.loads(request.content)
    payload = json.loads(body['messages'][1]['content'])
    result = reflection_result(payload) if 'transcript' in payload else example('mission')
    if 'transcript' not in payload:
        result['context_id'] = payload['context']['id']
    return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result)}}]})


class BandHarness:
    """Exercise native SDK sends and our adapters while replacing only vendor transport."""
    def __init__(self, runtime):
        self.runtime, self.messages, self.events, self.drop_results = runtime, [], [], False
        runtime.room_id = 'test-room'
        runtime.model_client = httpx.AsyncClient(transport=httpx.MockTransport(model_response))
        for kind, adapter in runtime.adapters.items():
            rest = SimpleNamespace(agent_api_messages=SimpleNamespace(), agent_api_events=SimpleNamespace())
            async def send(chat_id, message, request_options, sender=kind):
                self.messages.append(message)
                target_id = message.mentions[0].id
                target = next(k for k, rid in runtime.ids.items() if rid == target_id)
                if not (self.drop_results and '"type": "result"' in message.content):
                    msg = SimpleNamespace(content='@mention ' + message.content, sender_id=runtime.ids[sender], message_type='text')
                    await runtime.adapters[target].on_message(msg, AgentTools(chat_id, runtime.adapters[target].rest),
                        None, None, None, is_session_bootstrap=False, room_id=chat_id)
                return SimpleNamespace(data={'success': True})
            async def event(chat_id, event, request_options):
                self.events.append(event)
                return SimpleNamespace(data={'id': 'event'})
            rest.agent_api_messages.create_agent_chat_message = send
            rest.agent_api_events.create_agent_chat_event = event
            adapter.rest = rest

    async def close(self):
        await self.runtime.model_client.aclose()


class AgentProof(unittest.IsolatedAsyncioTestCase):
    async def test_startup_configuration_and_partial_cleanup(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'Missing live BAND configuration'):
                band_client.BandRuntime()
        with patch.dict(os.environ, {**ENV, 'BAND_REFLECTION_AGENT_ID': ENV['BAND_OPPORTUNITY_AGENT_ID']}):
            with self.assertRaisesRegex(ValueError, 'different IDs'):
                band_client.BandRuntime()
        with patch.dict(os.environ, ENV):
            runtime = band_client.BandRuntime()
            first, second = runtime.agents.values()
            with patch.object(first, 'start', new_callable=AsyncMock), \
                    patch.object(first, 'stop', new_callable=AsyncMock) as stop, \
                    patch.object(second, 'start', new_callable=AsyncMock, side_effect=RuntimeError('Connection failed')):
                with self.assertRaisesRegex(RuntimeError, 'Connection failed'):
                    async with runtime.connect():
                        self.fail('Startup should have failed')
                stop.assert_awaited_once()
                self.assertTrue(runtime.model_client.is_closed)

    async def test_routing_timeouts_and_foreign_messages(self):
        with patch.dict(os.environ, ENV):
            runtime = band_client.BandRuntime()
            harness = BandHarness(runtime)
            try:
                mission = await runtime.invoke('opportunity', example('state'))
                self.assertEqual(mission['status'], 'assigned')
                result = await runtime.invoke('reflection', {'mission': mission, 'recording_id': 'new',
                    'transcript': SAMPLE_TRANSCRIPT, 'skills': example('state')['skills']})
                self.assertEqual(result['recording_id'], 'new')
                self.assertEqual(len(harness.messages), 4)
                self.assertEqual(len(harness.events), 4)
                self.assertNotIn(SAMPLE_TRANSCRIPT, ' '.join(m.content for m in harness.messages))
                future = asyncio.get_running_loop().create_future()
                runtime.pending['guard'] = band_client.Job('opportunity', example('state'), future)
                msg = SimpleNamespace(content=band_client.MARKER + json.dumps({'id': 'guard', 'type': 'request'}),
                                      sender_id='intruder', message_type='text')
                await runtime.adapters['opportunity'].on_message(msg, None, None, None, None,
                    is_session_bootstrap=False, room_id=runtime.room_id)
                self.assertEqual(len(harness.messages), 4)
                msg.sender_id = runtime.ids['reflection']
                await runtime.adapters['opportunity'].on_message(msg, None, None, None, None,
                    is_session_bootstrap=False, room_id='foreign-room')
                self.assertEqual(len(harness.messages), 4)
                runtime.pending.pop('guard'); future.cancel()
                harness.drop_results, runtime.timeout = True, .02
                with self.assertRaisesRegex(RuntimeError, 'timed out'):
                    await runtime.invoke('opportunity', example('state'))
                self.assertFalse(runtime.pending)
            finally:
                await harness.close()

    async def test_model_validation_and_failure(self):
        payload = {'mission': example('mission'), 'recording_id': 'new',
                   'transcript': SAMPLE_TRANSCRIPT, 'skills': example('state')['skills']}
        with patch.dict(os.environ, ENV):
            for mutate in (lambda r: r.update(recording_id='wrong'),
                           lambda r: r.update(success_score=1.1),
                           lambda r: r['next_target'].update(skill_id='unknown'),
                           lambda r: r['demonstrated'][0].update(evidence='fabricated quote')):
                result = reflection_result(payload); mutate(result)
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200,
                        json={'choices': [{'message': {'content': json.dumps(result)}}]}))) as client:
                    with self.assertRaises((ValueError, ValidationError)):
                        await live_reflection(payload, client)
            for mutate in (lambda r: r.update(skill_id='unknown'), lambda r: r.update(context_id='wrong'),
                           lambda r: r.update(status='completed')):
                result = example('mission'); mutate(result)
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200,
                        json={'choices': [{'message': {'content': json.dumps(result)}}]}))) as client:
                    with self.assertRaises(ValueError):
                        await live_opportunity(example('state'), client)
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(401))) as client:
                with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
                    await live_opportunity(example('state'), client)
        schema = strict_schema(Mission.model_json_schema())
        self.assertEqual(set(schema['required']), set(schema['properties']))
        self.assertNotIn('default', schema['properties']['status'])


def files_output(recordings, page=1):
    rows = [f'  {rid:<34}  {title:<36}  {"2026-09-29":<12}  1m20s' for rid, title in recordings.items()]
    return '\nFiles on this page: ' + str(len(rows)) + '\n\n' + '  ID  NAME  DATE  DURATION\n  ' + '─' * 98 + '\n' + '\n'.join(rows) + f'\n\nPage {page}\n'


class BridgeProof(unittest.TestCase):
    def test_full_flow_restart_duplicates_and_mission_guard(self):
        harnesses = []
        @asynccontextmanager
        async def connected(runtime):
            harness = BandHarness(runtime); harnesses.append(harness)
            try:
                yield runtime
            finally:
                await harness.close()
        recordings = {'old': 'Existing recording'}
        ready = False
        def cli(*args):
            if args[0] == 'files':
                return files_output(recordings)
            if ready:
                Path(args[-1]).write_text(SAMPLE_TRANSCRIPT)
            return 'No "transaction" transcript for this recording. Available: (none).'
        with TemporaryDirectory() as directory, patch.dict(os.environ, ENV), \
                patch.object(main, 'DEMO_MODE', True), patch.object(main, 'BAND_MODE', 'live'), \
                patch.object(band_client, 'BAND_MODE', 'live'), patch.object(band_client.BandRuntime, 'connect', connected), \
                patch.object(bridge, 'run_cli', side_effect=cli), \
                TestClient(main.app, headers={'X-Internal-Token': INTERNAL_API_TOKEN}) as client:
            client.post('/api/demo/reset')
            self.assertEqual(client.post('/api/opportunity').status_code, 202)
            path = Path(directory) / 'state.json'
            backend = bridge.Backend(client)
            watcher = bridge.Watcher(path, backend)
            watcher.poll()
            self.assertEqual(watcher.state.seen, ['old'])
            recordings['new'] = 'My Spanish practice'
            watcher.poll()
            self.assertEqual(watcher.state.pending['new'].attempts, 1)
            watcher = bridge.Watcher(path, backend)
            watcher.state.pending['new'].next_attempt = 0
            ready = True
            # Backend commits but response is lost: restart must retry and deduplicate.
            original_submit = backend.submit
            def lost_response(rid, text):
                original_submit(rid, text)
                raise httpx.ReadTimeout('Simulated lost acknowledgement')
            with patch.object(backend, 'submit', side_effect=lost_response):
                watcher.poll()
            self.assertIn('new', watcher.state.pending)
            watcher = bridge.Watcher(path, backend)
            watcher.state.pending['new'].next_attempt = 0
            watcher.poll()
            self.assertEqual(watcher.state.processed, ['new'])
            self.assertFalse(watcher.state.pending)
            state = client.get('/api/state').json()
            self.assertEqual(state['skills'][0]['application_score'], .39)
            self.assertEqual(state['services']['band'], 'live')
            self.assertEqual(state['services']['storage'], 'memory')
            self.assertEqual(sum(n['type'] == 'experience' for n in client.get('/api/graph').json()['nodes']), 1)
            self.assertEqual(len(harnesses[0].messages), 4)
            previous = state['active_mission']['id']
            client.post('/api/demo/reset'); client.post('/api/opportunity')
            response = client.post('/api/internal/plaud/recording', json={'recording_id': 'stale', 'title': 'Old mission'},
                                   headers={'X-Expected-Mission-ID': previous})
            self.assertEqual(response.status_code, 409)
            self.assertNotIn('stale', main.store.recordings)
            self.assertEqual(client.post('/api/internal/plaud/status', json={'status': 'waiting', 'message': 'Ready'},
                                        headers={'X-Internal-Token': 'bad'}).status_code, 401)
            client.post('/api/demo/reset')

    def test_cli_parsing_timeouts_and_transcript_readiness(self):
        self.assertEqual(bridge.parse_files(files_output({'id-1': 'Spanish practice'})), {'id-1': 'Spanish practice'})
        self.assertEqual(bridge.parse_files(files_output({})), {})
        for output in ('Something changed', files_output({'id-1': 'Practice'}).replace('page: 1', 'page: 2')):
            with self.assertRaises(ValueError):
                bridge.parse_files(output)
        full = {f'id-{i}': 'Practice' for i in range(100)}
        with patch.object(bridge, 'run_cli', side_effect=[files_output(full), files_output({'last': 'Last'}, 2)]):
            self.assertEqual(len(bridge.list_recordings()), 101)
        with patch('subprocess.run', return_value=SimpleNamespace(returncode=2, stdout='', stderr='private')) as run:
            with self.assertRaises(bridge.AuthenticationError):
                bridge.run_cli('files')
            self.assertNotIn('shell', run.call_args.kwargs)
            self.assertEqual(run.call_args.kwargs['timeout'], 40)
        with patch('subprocess.run', side_effect=__import__('subprocess').TimeoutExpired(['plaud'], 40)):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                bridge.run_cli('files')
        with patch.object(bridge, 'run_cli', return_value='No transcript yet'):
            self.assertIsNone(bridge.read_transcript('id-1'))

    def test_exhaustion_corruption_and_failed_registration(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {'PLAUD_TRANSCRIPT_MAX_ATTEMPTS': '1'}):
            path = Path(directory) / 'state.json'
            backend = SimpleNamespace(notify=lambda *args: None, register=lambda *args: None,
                                      mission=lambda: {'id': 'mission', 'status': 'assigned'})
            with patch.object(bridge, 'list_recordings', return_value={}):
                watcher = bridge.Watcher(path, backend); watcher.poll()
            with patch.object(bridge, 'list_recordings', return_value={'new': 'Practice'}), \
                    patch.object(bridge, 'read_transcript', return_value=None):
                watcher.poll()
                self.assertTrue(watcher.state.pending['new'].exhausted)
                watcher = bridge.Watcher(path, backend); watcher.retry_failed()
                self.assertFalse(watcher.state.pending['new'].exhausted)
                response = httpx.Response(409, request=httpx.Request('POST', 'http://test/register'))
                with patch.object(backend, 'register', side_effect=httpx.HTTPStatusError('Conflict', request=response.request, response=response)):
                    watcher.poll()
                self.assertFalse(watcher.state.processed)
                self.assertIn('new', bridge.Watcher(path, backend).state.pending)
            path.write_text('{corrupt')
            with self.assertRaises(ValueError):
                bridge.Watcher(path, backend)
            self.assertEqual(path.read_text(), '{corrupt')


if __name__ == '__main__':
    unittest.main()
