"""Small runnable proof of the full demo, trust boundary, and update invariants."""
import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from pydantic import ValidationError
from server import main
from server.main import app
from server.settings import INTERNAL_API_TOKEN
from server.schemas import ReflectionResult
from server.demo_seed import example, SAMPLE_TRANSCRIPT
from server.neo4j_store import DemoStore, application_delta

class DemoProof(unittest.TestCase):
    def setUp(self):
        # Tests remain offline even when a developer configures live adapters in .env.
        self.enterContext(patch.object(main, 'store', DemoStore()))
        for target, value in [('server.main.DEMO_MODE', True), ('server.main.BAND_MODE', 'demo'),
                              ('server.band_client.BAND_MODE', 'demo')]:
            self.enterContext(patch(target, value))

    def test_demo_twice_and_boundaries(self):
        with TestClient(app) as client:
            for _ in range(2):
                self.assertEqual(client.get('/health').json(), {'status': 'ok'})
                state = client.post('/api/demo/reset').json()
                self.assertEqual(state['skills'][0]['application_score'], .25)
                self.assertEqual(client.post('/api/demo/replay').status_code, 409)
                self.assertEqual(client.post('/api/opportunity').status_code, 202)
                self.assertEqual(client.post('/api/opportunity').status_code, 409)
                self.assertEqual(client.get('/api/state').json()['active_mission']['status'], 'assigned')
                headers = {'X-Internal-Token': INTERNAL_API_TOKEN}
                data = {'recording_id': 'proof', 'title': 'Proof replay'}
                self.assertEqual(client.post('/api/internal/plaud/recording', json=data).status_code, 401)
                payload = {'recording_id': 'unknown', 'transcript': SAMPLE_TRANSCRIPT}
                self.assertEqual(client.post('/api/internal/plaud/transcript', json=payload, headers=headers).status_code, 404)
                self.assertEqual(client.post('/api/internal/plaud/recording', json=data, headers=headers).json()['status'], 'detected')
                self.assertEqual(client.post('/api/internal/plaud/recording', json=data, headers=headers).json()['status'], 'duplicate')
                payload['recording_id'] = 'proof'
                self.assertEqual(client.post('/api/internal/plaud/transcript', json=payload, headers=headers).json()['status'], 'completed')
                self.assertEqual(client.post('/api/internal/plaud/transcript', json=payload, headers=headers).json()['status'], 'duplicate')
                self.assertEqual(client.post('/api/internal/plaud/recording', json=data, headers=headers).json()['status'], 'duplicate')
                self.assertEqual(client.post('/api/opportunity').status_code, 409)
                state = client.get('/api/state').json()
                self.assertEqual(state['skills'][0]['application_score'], .39)
                self.assertEqual(state['next_target']['skill_id'], 'follow-up-questions')
                self.assertEqual(state['active_mission']['status'], 'assigned')
                self.assertFalse(state['opportunity_pending'])
                self.assertEqual(state['last_feedback']['recording_id'], 'proof')
                nodes = client.get('/api/graph').json()['nodes']
                self.assertEqual(sum(n['type'] == 'experience' for n in nodes), 1)
                self.assertTrue(any(n['type'] == 'gap' for n in nodes))
            client.post('/api/demo/reset')
            client.post('/api/opportunity')
            self.assertEqual(client.post('/api/demo/replay').status_code, 200)
            next_id = client.get('/api/state').json()['active_mission']['id']
            self.assertEqual(client.post('/api/demo/replay').json()['status'], 'completed')
            self.assertNotEqual(client.get('/api/state').json()['active_mission']['id'], next_id)
            self.assertEqual(sum(n['type'] == 'experience' for n in client.get('/api/graph').json()['nodes']), 2)

    def test_context_and_feedback_drive_the_next_opportunity(self):
        from server.schemas import Mission
        snapshots = []
        async def choose(snapshot):
            snapshots.append(snapshot)
            activity = 'dance-001' if snapshot['active_mission'] else snapshot['context']['id']
            return Mission.model_validate({**example('mission'), 'id': f'mission-proof-{len(snapshots)}',
                'context_id': activity, 'skill_id': 'restaurant-ordering' if len(snapshots) == 1 else 'dance-small-talk'})
        with patch('server.band_client.opportunity', side_effect=choose), TestClient(app) as client:
            client.post('/api/demo/reset'); client.post('/api/opportunity')
            client.post('/api/demo/replay')
            self.assertEqual(len(snapshots), 2)
            self.assertEqual(len(snapshots[1]['activities']), 3)
            self.assertEqual(snapshots[1]['next_target']['skill_id'], 'follow-up-questions')
            words = {w['id']: w for w in snapshots[1]['progress']['words']}
            self.assertEqual(words['quiero']['status'], 'fluent')
            state = client.get('/api/state').json()
            self.assertEqual(state['context']['id'], 'dance-001')
            self.assertEqual(state['active_mission']['skill_id'], 'dance-small-talk')
            self.assertEqual(state['last_feedback']['mission_id'], 'mission-proof-1')
            client.post('/api/demo/reset')

    def test_live_storage_and_demo_controls(self):
        with patch.object(main, 'DEMO_MODE', False), patch.object(main, 'NEO4J_URI', ''):
            with self.assertRaisesRegex(RuntimeError, 'Live storage requires'):
                with TestClient(app):
                    self.fail('Startup should fail without persistent storage')
        with patch.object(main, 'DEMO_MODE', False), patch.object(main, 'NEO4J_URI', 'test-configured'):
            with TestClient(app) as client:
                self.assertEqual(client.post('/api/demo/reset').status_code, 404)
                self.assertEqual(client.post('/api/demo/replay').status_code, 404)

    def test_score_and_reference_invariants(self):
        self.assertEqual([application_delta(x) for x in [.8, .6, .4, .39]], [.15, .14, .08, .03])
        store = DemoStore()
        from server.schemas import Mission
        store.assign(Mission.model_validate(example('mission')))
        store.record('plaud-recording-id', 'Evidence')
        result = ReflectionResult.model_validate(example('reflection'))
        store.data['skills'][0]['application_score'] = .99
        bad = result.model_copy(update={'mission_id': 'unknown'})
        with self.assertRaises(ValueError):
            store.apply(bad, SAMPLE_TRANSCRIPT)
        self.assertEqual(store.data['skills'][0]['application_score'], .99)
        store.apply(result, SAMPLE_TRANSCRIPT)
        self.assertEqual(store.data['skills'][0]['application_score'], 1)
        self.assertFalse(store.apply(result, SAMPLE_TRANSCRIPT))
        for score in [-.1, 1.1, float('nan')]:
            with self.assertRaises(ValidationError):
                ReflectionResult.model_validate({**example('reflection'), 'success_score': score})

if __name__ == '__main__':
    unittest.main()
