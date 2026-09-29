"""Explicit prerecorded replay via the same token-protected bridge endpoints."""
from server.demo_seed import SAMPLE_TRANSCRIPT
from server.plaud_bridge import submit_recording

def replay():
    print(submit_recording('demo-replay-001', 'Prerecorded demo replay — simulated PLAUD', SAMPLE_TRANSCRIPT))

if __name__ == '__main__':
    replay()
