"""PLAUD owner's seam. Backend handoff works; live CLI parsing is intentionally pending.
Verify `plaud today` and `plaud transcript <id>` against the installed official CLI.
Never assume JSON support or guess which output represents a ready transcript.
"""
import os
import httpx
from server.settings import INTERNAL_API_TOKEN


def submit_recording(recording_id: str, title: str, transcript: str):
    """Only mark a recording processed after both calls succeed. Retrying is safe."""
    with httpx.Client(base_url=os.getenv('API_BASE_URL', 'http://127.0.0.1:8000'),
                      headers={'X-Internal-Token': INTERNAL_API_TOKEN}, timeout=30) as client:
        response = client.post('/api/internal/plaud/recording', json={'recording_id': recording_id, 'title': title})
        response.raise_for_status()
        response = client.post('/api/internal/plaud/transcript', json={'recording_id': recording_id, 'transcript': transcript})
        response.raise_for_status()
        return response.json()

if __name__ == '__main__':
    raise SystemExit('Live watcher not implemented yet. Follow the PLAUD task in docs/TEAM_TASKS.md; use make replay for an explicitly labeled replay.')
