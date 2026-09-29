"""Recoverable PLAUD CLI 0.3.14 watcher. No upload or transcript copying."""
import argparse
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from filelock import FileLock, Timeout as FileLockTimeout
import httpx
from pydantic import Field
from server.schemas import Identifier, Payload, RecordingInput, Text, TranscriptInput
from server.settings import INTERNAL_API_TOKEN, ROOT

logger = logging.getLogger(__name__)
CLI_VERSION = '0.3.14'


class RecordingUnavailableError(RuntimeError):
    pass


class AuthenticationError(RuntimeError):
    pass


def run_cli(*args):
    try:
        result = subprocess.run(['plaud', *args], capture_output=True, text=True,
            timeout=40, check=False, env={**os.environ, 'NO_COLOR': '1', 'FORCE_COLOR': '0'})
    except FileNotFoundError as exc:
        raise RuntimeError(f'Install the supported CLI: npm install -g @plaud-ai/cli@{CLI_VERSION}') from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('PLAUD command timed out') from exc
    if result.returncode == 2:
        raise AuthenticationError('PLAUD authentication expired or missing; run plaud login')
    if result.returncode and args[0] == 'transcript' and re.search(r'\b404\b|NOT_FOUND', result.stderr):
        raise RecordingUnavailableError('PLAUD could not find this recording (HTTP 404)')
    if result.returncode:
        # CLI errors can include account details; don't relay raw stderr.
        raise RuntimeError(f'PLAUD command failed (exit {result.returncode}); check network and CLI login')
    return result.stdout


def parse_files(output):
    """Fail closed on output changes; observed from the pinned CLI's renderer."""
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    if len(lines) < 4 or not re.fullmatch(r'Files on this page: \d+', lines[0]):
        raise ValueError('Unsupported PLAUD files output; expected CLI 0.3.14')
    count = int(lines[0].rsplit(' ', 1)[1])
    if lines[1].split() != ['ID', 'NAME', 'DATE', 'DURATION'] or set(lines[2].strip()) != {'─'}:
        raise ValueError('Unsupported PLAUD listing header')
    if not re.fullmatch(r'Page \d+', lines[-1]):
        raise ValueError('Incomplete PLAUD listing')
    recordings = {}
    for line in lines[3:-1]:
        match = re.fullmatch(r'  ([A-Za-z0-9][A-Za-z0-9_-]{0,199})\s{2,}(.*?)\s{2,}(\d{4}-\d{2}-\d{2}|-)\s{2,}(-|\d+s|\d+m\d{2}s|\d+h\d{2}m)', line)
        if not match:
            raise ValueError('Unsupported PLAUD recording row')
        rid, title = match.group(1, 2)
        RecordingInput(recording_id=rid, title=title or 'PLAUD recording')
        if rid in recordings:
            raise ValueError('Duplicate ID in PLAUD page')
        recordings[rid] = title or 'PLAUD recording'
    if len(recordings) != count or count > 100:
        raise ValueError('Incomplete PLAUD listing')
    return recordings


def list_recordings():
    # files is paginated; today silently caps at 50 and misses recordings across midnight.
    # ponytail: full account scan per poll; use a cursor API when account volume makes polling slow.
    recordings = {}
    for page in range(1, 1001):
        output = run_cli('files', '--page', str(page), '--page-size', '100')
        batch = parse_files(output)
        if f'Page {page}' != output.strip().splitlines()[-1].strip():
            raise ValueError('PLAUD returned an unexpected page')
        recordings.update(batch)
        if len(batch) < 100:
            return recordings
    raise RuntimeError('PLAUD listing exceeds 100,000 recordings; narrow the account before watching')


def read_transcript(recording_id):
    with tempfile.TemporaryDirectory(prefix='applyloop-transcript-') as directory:
        path = Path(directory) / 'transcript.txt'
        run_cli('transcript', recording_id, '--output', str(path))
        # The CLI exits 0 without creating the file when transcription isn't ready.
        if not path.exists():
            return None
        if path.stat().st_size > 400000:
            raise ValueError('PLAUD transcript exceeds the supported size')
        text = path.read_text().strip()
        if len(text) < 10:
            return None
        return TranscriptInput(recording_id=recording_id, transcript=text).transcript


class Backend:
    def __init__(self, client):
        self.client = client

    def post(self, path, payload, headers=None):
        response = self.client.post(path, json=payload, headers=headers)
        if response.status_code == 401:
            raise AuthenticationError('Backend rejected INTERNAL_API_TOKEN; check bridge/backend configuration')
        response.raise_for_status()
        return response.json()

    def register(self, rid, title, mission_id=None):
        headers = {'X-Expected-Mission-ID': mission_id} if mission_id else None
        result = self.post('/api/internal/plaud/recording', {'recording_id': rid, 'title': title}, headers)
        if result.get('status') not in {'detected', 'duplicate'}:
            raise ValueError('Backend did not acknowledge recording registration')

    def submit(self, rid, text):
        payload = TranscriptInput(recording_id=rid, transcript=text)
        result = self.post('/api/internal/plaud/transcript', payload.model_dump())
        if result.get('status') not in {'completed', 'duplicate', 'irrelevant'}:
            raise ValueError('Backend did not acknowledge transcript completion')
        return result

    def mission(self):
        response = self.client.get('/api/state')
        response.raise_for_status()
        return response.json()['active_mission']

    def notify(self, status, message):
        self.post('/api/internal/plaud/status', {'status': status, 'message': message})


def backend_client():
    return httpx.Client(base_url=os.getenv('API_BASE_URL', 'http://127.0.0.1:8000'),
        headers={'X-Internal-Token': INTERNAL_API_TOKEN}, timeout=180)


def submit_recording(recording_id: str, title: str, transcript: str):
    """Replay-compatible handoff; successful completion is the only acknowledgement."""
    with backend_client() as client:
        backend = Backend(client)
        backend.register(recording_id, title)
        return backend.submit(recording_id, transcript)


class PendingRecording(Payload):
    title: Text
    mission_id: Identifier
    attempts: int = Field(default=0, ge=0)
    next_attempt: float = Field(default=0, ge=0, allow_inf_nan=False)
    exhausted: bool = False


class BridgeState(Payload):
    version: int = Field(default=1, ge=1, le=1)
    seen: list[Identifier] = Field(default_factory=list)
    processed: list[Identifier] = Field(default_factory=list)
    pending: dict[Identifier, PendingRecording] = Field(default_factory=dict)


class Watcher:
    def __init__(self, path, backend):
        self.path, self.backend = Path(path), backend
        self.retry_seconds = float(os.getenv('PLAUD_TRANSCRIPT_RETRY_SECONDS', '4'))
        self.max_attempts = int(os.getenv('PLAUD_TRANSCRIPT_MAX_ATTEMPTS', '45'))
        if not 0 < self.retry_seconds <= 3600 or not 0 < self.max_attempts <= 10000:
            raise ValueError('PLAUD retry seconds/attempts must be positive and within supported limits')
        self.state = BridgeState.model_validate_json(self.path.read_text()) if self.path.exists() else None
        if self.state and (set(self.state.pending) & set(self.state.processed)
                           or not (set(self.state.pending) | set(self.state.processed)) <= set(self.state.seen)):
            raise ValueError('Inconsistent bridge state; preserve it for recovery')

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + '.')
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(self.state.model_dump_json(indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def retry_failed(self):
        if self.state:
            for pending in self.state.pending.values():
                pending.exhausted, pending.attempts, pending.next_attempt = False, 0, 0
            self.save()

    def poll(self):
        # Pending work survives listing outages; a failed listing never changes the baseline.
        try:
            recordings = list_recordings()
        except AuthenticationError:
            raise
        except (RuntimeError, ValueError) as exc:
            if self.state is None:
                raise
            recordings = {}
            self.backend.notify('error', f'PLAUD listing unavailable ({type(exc).__name__}); retrying')
        if self.state is None:
            self.state = BridgeState(seen=sorted(recordings))
            self.save()
            self.backend.notify('waiting', 'PLAUD connected; waiting for a new synced recording')
            return
        new_ids = set(recordings) - set(self.state.seen)
        if not new_ids and not any(not p.exhausted for p in self.state.pending.values()):
            retained = len(self.state.pending)
            self.backend.notify('waiting', f'PLAUD connected; waiting for relevant practice evidence. {retained} earlier recordings retained for review.' if retained
                                else 'PLAUD connected; waiting for a new practice recording.')
        if new_ids:
            mission = self.backend.mission()
            for rid in sorted(new_ids):
                self.state.seen.append(rid)
                if mission and mission['status'] == 'assigned':
                    self.state.pending[rid] = PendingRecording(title=recordings[rid], mission_id=mission['id'])
                else:
                    logger.info('Ignored recording %s: no assigned mission at discovery', rid)
            self.save()
        for rid, pending in list(self.state.pending.items()):
            if pending.exhausted or time.time() < pending.next_attempt:
                continue
            try:
                # Registration retries assert the original mission; never bind to a later one.
                self.backend.register(rid, pending.title, pending.mission_id)
                pending.attempts += 1
                pending.next_attempt = time.time() + self.retry_seconds
                self.save()
                text = read_transcript(rid)
                if text is None:
                    self.backend.notify('transcript_waiting', 'Waiting for PLAUD transcript; generate transcription in PLAUD and sync it to the cloud')
                    if pending.attempts >= self.max_attempts:
                        pending.exhausted = True
                        self.backend.notify('error', 'PLAUD transcript retry limit reached; use make plaud-retry after transcription is ready')
                    self.save()
                    continue
                outcome = self.backend.submit(rid, text)
                self.state.processed.append(rid)
                del self.state.pending[rid]
                self.save()
                self.backend.notify('waiting', 'Unrelated recording excluded; waiting for relevant practice evidence.' if outcome and outcome.get('status') == 'irrelevant'
                                    else 'PLAUD recording processed; the next opportunity is prepared automatically. Waiting for a new recording.')
            except RecordingUnavailableError:
                pending.exhausted = True
                self.save()
                self.backend.notify('waiting', 'PLAUD connected; an unavailable recording (HTTP 404) is retained for review. Waiting for a new recording.')
            except AuthenticationError:
                raise
            except (RuntimeError, ValueError, httpx.HTTPError) as exc:
                pending.next_attempt = time.time() + self.retry_seconds
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 409:
                    # A completed/changed mission cannot accept this recording. Retain it for review.
                    pending.exhausted = True
                    self.save()
                    self.backend.notify('waiting', 'PLAUD connected; an additional recording is retained for a completed or changed mission. Review before retrying.')
                    continue
                # A ready transcript may be retried indefinitely on backend delivery failures.
                if not isinstance(exc, httpx.HTTPError) and pending.attempts >= self.max_attempts:
                    pending.exhausted = True
                self.save()
                self.backend.notify('error', f'PLAUD delivery pending ({type(exc).__name__}); check mission, BAND, and connectivity')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=ROOT / '.plaud_bridge_state.json')
    parser.add_argument('--once', action='store_true', help='Run one poll (first run only establishes baseline)')
    parser.add_argument('--retry-failed', action='store_true', help='Resume retained recordings after retry exhaustion')
    args = parser.parse_args()
    poll_seconds = float(os.getenv('PLAUD_POLL_SECONDS', '3'))
    if not 0 < poll_seconds <= 3600:
        raise SystemExit('PLAUD_POLL_SECONDS must be >0 and <=3600')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(args.state) + '.lock', timeout=0), backend_client() as client:
            if run_cli('version').splitlines()[0] != f'plaud {CLI_VERSION}':
                raise RuntimeError(f'Unsupported CLI; install @plaud-ai/cli@{CLI_VERSION}')
            backend = Backend(client)
            watcher = Watcher(args.state, backend)
            if args.retry_failed:
                watcher.retry_failed()
            while True:
                try:
                    watcher.poll()
                except AuthenticationError as exc:
                    try:
                        backend.notify('error', str(exc))
                    except httpx.HTTPError:
                        pass
                    raise
                except httpx.HTTPError as exc:
                    logger.warning('Backend unavailable (%s); pending work retained', type(exc).__name__)
                if args.once:
                    break
                time.sleep(poll_seconds)
    except KeyboardInterrupt:
        logger.info('Watcher stopped; pending work retained')
    except FileLockTimeout:
        raise SystemExit('Another PLAUD watcher already owns this state file') from None
    except Exception as exc:
        # Keep CLI output, private titles, transcripts, and secrets out of logs.
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        raise SystemExit(f'PLAUD bridge stopped: {message}') from None


if __name__ == '__main__':
    main()
