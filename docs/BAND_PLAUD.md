# BAND + PLAUD implementation and runbook

Integrated on `feat/neo4j-band-integration` with the Neo4j work from PR #2. Earlier authenticated BAND/PLAUD verification used memory storage. The combined flow now passes against real local Neo4j using simulated vendor transport; Aura, both BAND keys, model responses and PLAUD transcript access were authenticated locally. The active app uses the combined integration.

## Completed plan

1. Verify interfaces: BAND Python SDK 3.2.1 and official PLAUD CLI 0.3.14.
2. Run two SDK remote agents inside FastAPI; route application-created requests and replies through a BAND room.
3. Generate validated missions and transcript-grounded reflections with structured model output.
4. Watch PLAUD recordings automatically, register them, wait for transcripts, and deliver through the existing internal endpoints.
5. Persist discoveries before delivery, recover after restart, deduplicate acknowledgements, and prevent reassignment after mission changes.
6. Report actual adapter status in the API/UI; preserve explicitly labeled replay.
7. Prove the complete local flow, invalid outputs, auth failures, timeouts, retry exhaustion, lost acknowledgements, and restart recovery.

## Configure BAND later

Run `make setup`. Create two **External/Remote Agents** under the same BAND account:

- ApplyLoop Opportunity
- ApplyLoop Reflection

Put their UUIDs and keys, plus your model-provider key, in the root `.env`:

```dotenv
DEMO_MODE=true
BAND_MODE=live
OPENAI_API_KEY=<provider key>
OPENAI_MODEL=gpt-4o-mini
BAND_OPPORTUNITY_AGENT_ID=<opportunity UUID>
BAND_OPPORTUNITY_API_KEY=<opportunity key>
BAND_REFLECTION_AGENT_ID=<reflection UUID>
BAND_REFLECTION_API_KEY=<reflection key>
INTERNAL_API_TOKEN=<your random local shared token>
```

Keep `.env` local. `NEO4J_URI` selects persistent Neo4j storage; an empty URI uses memory. `DEMO_MODE=true` enables demo reset/replay; `DEMO_MODE=false` requires Neo4j and hides those controls. `BAND_MODE=demo` runs the offline fixtures. Live BAND never silently falls back to fixtures. Missing credentials or invalid IDs fail startup.

Start `make api` and `make web` in separate terminals. API startup connects both agents and creates a room, logging its ID. Open BAND to inspect their request/reply messages and action events. To reuse an existing room, set `BAND_ROOM_ID` to its UUID and ensure both agents are participants. Use one API worker; the SDK permits one active connection per agent.

Click **Find opportunity**. BAND routes a request to the Opportunity agent. It selects a supplied skill/context and returns an assigned mission. The app, rather than the model, assigns the mission ID.

The Reflection agent receives the registered mission, transcript, recording ID, existing skills and curriculum words. The model selects numbered transcript excerpts for demonstrations, gaps and word evidence; the backend copies the original excerpts into the existing reflection contract. This preserves quotes across languages without asking the model to reproduce text or timestamps. Excerpts follow utterance/sentence boundaries, with long excerpts split at the existing 2,000-character evidence limit. Unsupported observations may be empty; missing evidence is never itself a skill gap. Unknown references, mismatched IDs, nonfinite/out-of-range scores, refusals, and malformed results fail before any graph mutation.

The agents share the API process and its pending request registry. BAND carries correlated request/reply messages; full transcripts stay in the process and go to the model provider, while validated reflection quotes/results are visible in the BAND room. Human room messages cannot initiate a job or mutate the graph. No private model reasoning is posted. Splitting these agents onto different hosts would require a durable job transport; this implementation intentionally keeps the single-process architecture.

## Configure PLAUD later

```sh
make plaud-install
plaud login
plaud files
```

Sign in through the CLI's browser flow. Tokens remain in `~/.plaud/tokens.json`, outside this repository. Enable cloud sync and ensure transcription is generated in PLAUD; the bridge retrieves existing transcripts and does not start transcription itself.

With API/web running:

```sh
make plaud
```

The **first run** saves all existing recording IDs as a baseline. Create an assigned mission in the UI, then record and sync a **new** recording with PLAUD. Keep recordings relevant to the mission; this single-learner app binds evidence to the mission active at discovery. Recordings discovered without an assigned mission are ignored.

The watcher registers new evidence immediately, waits for a transcript, and submits it automatically. It reports "Waiting for PLAUD transcript" only while the CLI actually has no transcript; reflection/delivery failures retain their error status. There is no upload or transcript-copy step. It uses paginated `plaud files`, including across midnight; `plaud today` only checks the latest 50 records. CLI output is parsed against the pinned renderer, with incomplete/unrecognized output rejected. `plaud transcript <id> --output <temporary file>` distinguishes actual transcript text from CLI readiness notices.

State is stored atomically in `.plaud_bridge_state.json` with private file permissions. Pending work and completed IDs survive restart. A file lock prevents two watchers from using the same state file. Completion is recorded only after the backend returns `completed` or `duplicate`.

## Recovery and limitations

| Situation | Behavior / action |
| --- | --- |
| Transcript still being generated | Retry with `PLAUD_TRANSCRIPT_RETRY_SECONDS`, up to `PLAUD_TRANSCRIPT_MAX_ATTEMPTS` (defaults: 4 seconds, 45 attempts). |
| Retry limit reached | Pending recording is retained. Stop the watcher and run `make plaud-retry` once PLAUD finishes transcription. |
| Network/BAND/backend failure | Pending delivery is retained and retried; successful backend mutations deduplicate by recording ID. |
| Authentication failure | Watcher stops with a useful error. Run `plaud login` or fix the shared internal token, then restart. |
| Mission completed/changed/reset before delivery | Previously registered evidence retains its original binding; already committed evidence can still be acknowledged after switching activities. Unregistered stale evidence or incomplete evidence for a completed mission returns 409. Evidence is retained and paused for review; the watcher stays connected for future recordings. It is never attached to a new mission. Inspect the old task before using `make plaud-retry`; don't delete state to force reassignment. |
| API restarts with memory storage | The API loses missions/recording bindings; watcher persistence cannot restore them. Set `NEO4J_URI` to preserve these bindings across API restarts. |
| Corrupt or older bridge state | Startup fails without replacing the file. Preserve it for recovery; the supported schema is version 1. |
| CLI output changes | Install the supported 0.3.14 version; the watcher refuses unfamiliar output. |
| Large account | Each poll scans the full account. Increase `PLAUD_POLL_SECONDS` if slow. CLI pagination is capped at 100,000 records and names are truncated by the CLI. |
| Bridge exits without a final status event | UI connection status reflects the last received report, not a heartbeat. Check the watcher terminal if stale. |

`BAND_REQUEST_TIMEOUT_SECONDS` defaults to 120 and covers each room request/reply. Model calls have a maximum 90-second HTTP timeout; PLAUD subprocess commands time out after 40 seconds. Bridge HTTP calls allow 180 seconds for reflection/graph completion.

## Neo4j integration

API startup verifies the store and required uniqueness constraints, initializes only an empty graph, then connects BAND. Shutdown closes both. Failed startup closes the driver and never silently falls back to memory. Changing `LEARNER_ID` on another learner's graph fails without resetting data.

The recording body remains `{recording_id,title}`. `X-Expected-Mission-ID` asserts the existing binding or, for new evidence, the current mission. Atomic reflection updates save scores, vocabulary, evidence and next target together. Each relevant reflection queues the next opportunity using saved activities, current word/level progress and feedback. Irrelevant recordings are acknowledged without creating an experience or ending the mission. The last relevant reflection is saved on the Experience for restart-safe feedback. HTTP 404 transcript retrievals are paused and retained for review rather than reported as recurring generic failures.

## Checks and real acceptance

```sh
# Offline checks:
make check
# Optional real database proof; only a dedicated disposable database:
NEO4J_TEST_URI=bolt://localhost:17687 NEO4J_TEST_PASSWORD=<test-password> make test-graph
cd web && npm run build
```

`server/test_integrations.py` exercises native SDK message/event sends with simulated BAND transport, mocked model HTTP responses, the verified PLAUD renderer format, actual FastAPI endpoints, and the current store. These prove the code path, not external account/device access.

After credentials and Neo4j are available: generate a mission in the UI, sync a physical PLAUD recording, verify both agents' BAND activity, see transcript-grounded evidence/next target in the UI and a durable graph update, then retry delivery and restart the watcher. There must be exactly one experience per recording. Scores depend on the real reflection; 25% →39% is the fixture expectation, not a promised live result.

Authoritative interfaces verified September 29, 2026:

- https://docs.band.ai/getting-started/connect-remote-agent
- https://docs.band.ai/integrations/sdks/tutorials/creating-framework-integrations.md
- https://docs.plaud.ai/plaud-mcp-cli/cli
- Installed `band-sdk==3.2.1` source and `@plaud-ai/cli@0.3.14` renderer/help.
