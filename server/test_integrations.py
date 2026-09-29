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
from server.agents.reflection_agent import live_reflection, transcript_excerpts
from server.agents.model import strict_schema
from server.demo_seed import example, SAMPLE_TRANSCRIPT
from server.settings import INTERNAL_API_TOKEN
from server.schemas import Mission
from server.learning_rules import curriculum
from server.memory_store import DemoStore

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
    excerpts = payload['transcript'] if isinstance(payload['transcript'], list) else [
        {'evidence_id': i, 'text': text} for i, text in enumerate(transcript_excerpts(payload['transcript']))]
    for item in result['word_evidence']:
        item['evidence'] = result['demonstrated'][0]['evidence'] if item['outcome'] == 'used_correctly' else result['gaps'][0]['evidence']
    for item in result['demonstrated'] + result['gaps'] + result['word_evidence']:
        quote = item.pop('evidence')
        item['evidence_id'] = next(e['evidence_id'] for e in excerpts if quote in e['text'])
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
                    'transcript': SAMPLE_TRANSCRIPT, 'skills': example('state')['skills'], 'words': curriculum()['words']})
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
                   'transcript': SAMPLE_TRANSCRIPT, 'skills': example('state')['skills'], 'words': curriculum()['words']}
        with patch.dict(os.environ, ENV):
            for mutate in (lambda r: r.update(recording_id='wrong'),
                           lambda r: r.update(success_score=1.1),
                           lambda r: r['next_target'].update(skill_id='unknown'),
                           lambda r: r['demonstrated'][0].update(evidence_id=999999),
                           lambda r: r['word_evidence'][0].update(word_id='unknown'),
                           lambda r: r['word_evidence'][0].update(evidence_id=999999)):
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

    async def test_insufficient_transcript_allows_no_observations(self):
        payload = {'mission': example('mission'), 'recording_id': 'test-recording',
                   'transcript': '[00:00 - 00:03] Speaker 1: Testing the recorder.',
                   'skills': example('state')['skills'], 'words': curriculum()['words']}
        result = {'mission_id': payload['mission']['id'], 'recording_id': payload['recording_id'],
                  'relevant': False, 'relevance_reason': 'Recorder test, not Spanish practice.',
                  'success_score': 0, 'demonstrated': [], 'gaps': [],
                  'next_target': {'skill_id': payload['mission']['skill_id'],
                                  'reason': 'Record a relevant practice attempt.'}}
        def respond(request):
            prompt = json.loads(request.content)['messages'][0]['content']
            self.assertIn('demonstrated=[] and gaps=[]', prompt)
            schema = json.loads(request.content)['response_format']['json_schema']['schema']
            self.assertEqual(schema['properties']['mission_id']['enum'], [payload['mission']['id']])
            self.assertEqual(schema['properties']['recording_id']['enum'], [payload['recording_id']])
            self.assertEqual(schema['$defs']['ObservationSelection']['properties']['evidence_id']['enum'], [0])
            self.assertEqual(set(schema['$defs']['NextTarget']['properties']['skill_id']['enum']), {s['id'] for s in payload['skills']})
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result)}}]})
        with patch.dict(os.environ, ENV):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                reflection = await live_reflection(payload, client)
        self.assertFalse(reflection.relevant)
        self.assertEqual(reflection.success_score, 0)
        self.assertEqual(reflection.demonstrated + reflection.gaps, [])

    async def test_multilingual_evidence_is_copied_from_source(self):
        transcript = '[00:00 - 00:03] Speaker 1: मुझे समझ नहीं आया।'
        payload = {'mission': example('mission'), 'recording_id': 'multilingual',
                   'transcript': transcript, 'skills': example('state')['skills'], 'words': curriculum()['words']}
        result = {'mission_id': payload['mission']['id'], 'recording_id': payload['recording_id'],
                  'success_score': .2, 'demonstrated': [], 'gaps': [{
                      'skill_id': payload['mission']['skill_id'], 'name': 'Understanding the response',
                      'evidence_id': 0, 'confidence': .8}],
                  'next_target': {'skill_id': payload['mission']['skill_id'], 'reason': 'Practice listening.'}}
        def respond(request):
            body = json.loads(request.content)
            self.assertEqual(json.loads(body['messages'][1]['content'])['transcript'], [
                {'evidence_id': 0, 'text': transcript}])
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result)}}]})
        with patch.dict(os.environ, ENV):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                reflection = await live_reflection(payload, client)
        self.assertEqual(reflection.gaps[0].evidence, transcript)
        long_line = 'अ' * 2100
        self.assertEqual(''.join(transcript_excerpts(long_line)), long_line)
        self.assertTrue(all(len(e) <= 2000 for e in transcript_excerpts(long_line)))


def files_output(recordings, page=1):
    rows = [f'  {rid:<34}  {title:<36}  {"2026-09-29":<12}  1m20s' for rid, title in recordings.items()]
    return '\nFiles on this page: ' + str(len(rows)) + '\n\n' + '  ID  NAME  DATE  DURATION\n  ' + '─' * 98 + '\n' + '\n'.join(rows) + f'\n\nPage {page}\n'


class BridgeProof(unittest.TestCase):
    def make_store(self):
        return DemoStore()

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
                patch.object(main, 'store', self.make_store()), patch.object(main, 'DEMO_MODE', True), patch.object(main, 'BAND_MODE', 'live'), \
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
            previous = watcher.state.pending['new'].mission_id
            original_submit = backend.submit
            def lost_response(rid, text):
                original_submit(rid, text)
                raise httpx.ReadTimeout('Simulated lost acknowledgement')
            with patch.object(backend, 'submit', side_effect=lost_response):
                watcher.poll()
            self.assertIn('new', watcher.state.pending)
            # A new activity and backend restart must not prevent acknowledgement of committed evidence.
            self.assertEqual(client.get('/api/state').json()['active_mission']['status'], 'assigned')
            self.assertNotEqual(client.get('/api/state').json()['active_mission']['id'], previous)
            self.verify_persistence(client)
            watcher = bridge.Watcher(path, backend)
            watcher.state.pending['new'].next_attempt = 0
            watcher.poll()
            self.assertEqual(watcher.state.processed, ['new'])
            self.assertFalse(watcher.state.pending)
            state = client.get('/api/state').json()
            self.assertEqual(state['skills'][0]['application_score'], .39)
            self.assertEqual(state['services']['band'], 'live')
            self.assertEqual(state['services']['storage'], main.store.backend)
            words = {w['id']: w for w in client.get('/api/learner/progress').json()['words']}
            self.assertEqual(words['quiero']['real_uses'], 2)
            self.verify_persistence(client)
            self.assertEqual(sum(n['type'] == 'experience' for n in client.get('/api/graph').json()['nodes']), 1)
            self.assertEqual(len(harnesses[0].messages), 6)
            self.assertEqual(client.get('/api/state').json()['active_mission']['status'], 'assigned')
            self.assertNotEqual(client.get('/api/state').json()['active_mission']['id'], previous)
            self.assertEqual(sum(n['type'] == 'experience' for n in client.get('/api/graph').json()['nodes']), 1)
            client.post('/api/opportunity')
            response = client.post('/api/internal/plaud/recording', json={'recording_id': 'stale', 'title': 'Old mission'},
                                   headers={'X-Expected-Mission-ID': previous})
            self.assertEqual(response.status_code, 409)
            self.assertIsNone(main.store.recording('stale'))
            self.assertEqual(client.post('/api/internal/plaud/status', json={'status': 'waiting', 'message': 'Ready'},
                                        headers={'X-Internal-Token': 'bad'}).status_code, 401)
            client.post('/api/demo/reset')

    def verify_persistence(self, client):
        pass

    def test_many_recordings_filter_without_finishing_mission(self):
        with patch.object(main, 'store', self.make_store()), patch.object(main, 'DEMO_MODE', True), \
                patch.object(main, 'BAND_MODE', 'demo'), patch.object(band_client, 'BAND_MODE', 'demo'), \
                TestClient(main.app, headers={'X-Internal-Token': INTERNAL_API_TOKEN}) as client:
            client.post('/api/demo/reset'); client.post('/api/opportunity')
            before = client.get('/api/state').json()
            rid = 'unrelated-recording'
            client.post('/api/internal/plaud/recording', json={'recording_id': rid, 'title': 'Unrelated meeting'})
            from server.schemas import ReflectionResult
            irrelevant = ReflectionResult.model_validate({**example('reflection'), 'recording_id': rid,
                'mission_id': before['active_mission']['id'], 'relevant': False,
                'relevance_reason': 'No Spanish practice in this meeting.', 'success_score': 0,
                'demonstrated': [], 'gaps': [], 'word_evidence': []})
            payload = {'recording_id': rid, 'transcript': 'We discussed unrelated project deadlines.'}
            with patch.object(band_client, 'reflection', return_value=irrelevant) as reflection_call:
                self.assertEqual(client.post('/api/internal/plaud/transcript', json=payload).json()['status'], 'irrelevant')
                self.assertEqual(client.post('/api/internal/plaud/transcript', json=payload).json()['status'], 'irrelevant')
                self.assertEqual(reflection_call.call_count, 1)
            after = client.get('/api/state').json()
            self.assertEqual(before['skills'], after['skills'])
            self.assertEqual(before['active_mission'], after['active_mission'])
            self.assertEqual(sum(n['type'] == 'experience' for n in client.get('/api/graph').json()['nodes']), 0)
            self.assertEqual(client.post('/api/demo/replay').json()['status'], 'completed')
            after = client.get('/api/state').json()
            self.assertNotEqual(after['active_mission']['id'], before['active_mission']['id'])
            self.assertEqual(sum(n['type'] == 'experience' for n in client.get('/api/graph').json()['nodes']), 1)
            client.post('/api/demo/reset')

    def test_unavailable_recording_is_retained_without_delivery_error(self):
        with patch('subprocess.run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='HTTP 404')):
            with self.assertRaises(bridge.RecordingUnavailableError):
                bridge.run_cli('transcript', 'missing')
        notices = []
        backend = SimpleNamespace(register=lambda *a: None, notify=lambda *a: notices.append(a))
        with TemporaryDirectory() as directory:
            watcher = bridge.Watcher(Path(directory) / 'state.json', backend)
            watcher.state = bridge.BridgeState(seen=['missing'], pending={
                'missing': bridge.PendingRecording(title='Missing', mission_id='original')})
            with patch.object(bridge, 'list_recordings', return_value={}), \
                    patch.object(bridge, 'read_transcript', side_effect=bridge.RecordingUnavailableError('HTTP 404')):
                watcher.poll()
            self.assertTrue(watcher.state.pending['missing'].exhausted)
            self.assertEqual(watcher.state.pending['missing'].mission_id, 'original')
            self.assertEqual(notices[-1][0], 'waiting')

    def test_reflection_failure_retains_transcript_and_plaud_connection(self):
        notices = []
        response = httpx.Response(502, request=httpx.Request('POST', 'http://test/transcript'))
        error = httpx.HTTPStatusError('Reflection failed', request=response.request, response=response)
        def fail_submit(*args):
            raise error
        backend = SimpleNamespace(register=lambda *a: None, notify=lambda *a: notices.append(a),
                                  submit=fail_submit)
        with TemporaryDirectory() as directory:
            watcher = bridge.Watcher(Path(directory) / 'state.json', backend)
            watcher.state = bridge.BridgeState(seen=['new'], pending={
                'new': bridge.PendingRecording(title='Practice', mission_id='original')})
            with patch.object(bridge, 'list_recordings', return_value={}), \
                    patch.object(bridge, 'read_transcript', return_value=SAMPLE_TRANSCRIPT):
                watcher.poll()
            self.assertIn('new', watcher.state.pending)
            self.assertFalse(watcher.state.processed)
            self.assertEqual(notices[-1][0], 'transcript_waiting')

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
            notices = []
            backend = SimpleNamespace(notify=lambda *args: notices.append(args), register=lambda *args: None,
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
                self.assertTrue(watcher.state.pending['new'].exhausted)
                self.assertEqual(notices[-1][0], 'waiting')
            path.write_text('{corrupt')
            with self.assertRaises(ValueError):
                bridge.Watcher(path, backend)
            self.assertEqual(path.read_text(), '{corrupt')

    def test_ready_transcript_does_not_report_waiting(self):
        notices = []
        backend = SimpleNamespace(notify=lambda *args: notices.append(args),
                                  register=lambda *args: None, submit=lambda *args: None)
        with TemporaryDirectory() as directory:
            watcher = bridge.Watcher(Path(directory) / 'state.json', backend)
            watcher.state = bridge.BridgeState(seen=['new'], pending={
                'new': bridge.PendingRecording(title='Practice', mission_id='mission')})
            with patch.object(bridge, 'list_recordings', return_value={}), \
                    patch.object(bridge, 'read_transcript', return_value=SAMPLE_TRANSCRIPT):
                watcher.poll()
            self.assertEqual(watcher.state.processed, ['new'])
            self.assertEqual([status for status, message in notices], ['waiting'])


@unittest.skipUnless(os.getenv('NEO4J_TEST_URI'), 'requires a dedicated Neo4j test database')
class Neo4jBridgeProof(BridgeProof):
    def make_store(self):
        from server.test_graph_store import test_neo4j_store
        store = test_neo4j_store()
        return store

    def verify_persistence(self, client):
        # Reopen the actual driver/store, as the backend does after a restart.
        original = client.get('/api/state').json()
        main.store.close()
        main.store = self.make_store()
        main.store.connect()
        self.assertEqual(client.get('/api/state').json(), original)
        self.assertTrue(main.store.has_experience('new'))


if __name__ == '__main__':
    unittest.main()
