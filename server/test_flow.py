"""Small runnable proof of the full demo, trust boundary, and update invariants."""
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from pydantic import ValidationError
from server.main import app
from server.settings import INTERNAL_API_TOKEN
from server.schemas import ReflectionResult
from server.demo_seed import example, SAMPLE_TRANSCRIPT
from server.neo4j_store import DemoStore, application_delta

class DemoProof(unittest.TestCase):
    def setUp(self):
        # Tests remain offline even when a developer configures live adapters in .env.
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
                nodes = client.get('/api/graph').json()['nodes']
                self.assertEqual(sum(n['type'] == 'experience' for n in nodes), 1)
                self.assertTrue(any(n['type'] == 'gap' for n in nodes))
            client.post('/api/demo/reset')
            client.post('/api/opportunity')
            self.assertEqual(client.post('/api/demo/replay').status_code, 200)
            self.assertEqual(client.post('/api/demo/replay').json()['status'], 'duplicate')

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
