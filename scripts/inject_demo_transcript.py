"""Explicit prerecorded replay via the same token-protected bridge endpoints."""
import os
import httpx
from server.demo_seed import SAMPLE_TRANSCRIPT
from server.settings import INTERNAL_API_TOKEN

def replay():
    headers = {'X-Internal-Token': INTERNAL_API_TOKEN}
    with httpx.Client(base_url=os.getenv('API_BASE_URL', 'http://127.0.0.1:8000'), headers=headers, timeout=30) as client:
        for path, payload in [('/api/internal/plaud/recording', {'recording_id': 'demo-replay-001', 'title': 'Prerecorded demo replay — simulated PLAUD'}), ('/api/internal/plaud/transcript', {'recording_id': 'demo-replay-001', 'transcript': SAMPLE_TRANSCRIPT})]:
            response = client.post(path, json=payload)
            response.raise_for_status()
            print(response.json())

if __name__ == '__main__':
    replay()
