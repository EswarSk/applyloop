# One-hour team plan

**BAND + PLAUD code is now implemented on `feat/band-plaud`.** See [BAND_PLAUD.md](BAND_PLAUD.md) for startup, pending account/device verification, and the Neo4j merge boundary. The original work assignments below remain a reference for the team split.

## Before splitting — minutes 0–5

Integrator shares this repo, assigns people, and runs `make setup`. Everyone agrees to [API.md](../contracts/API.md).
Copy `.env.example` to `.env`; keep credentials local. Use one branch per workstream.
For four people, combine integrator + backend. For three, combine BAND + PLAUD and backend + integration.

| Owner / branch | Owns these files | Task / acceptance |
|---|---|---|
| Frontend / `feat/frontend` | `web/` | Polish the working dashboard. Keep one EventSource. Verify reset, 25→39, new experience/evidence/gap, mobile view, reconnect. Do not change API payloads. |
| Backend + Neo4j / `feat/backend-graph` | `server/neo4j_store.py`, `server/demo_seed.py`; coordinate startup change in `main.py` | Add live Neo4j store with same methods, one driver verified on startup, unique IDs, transactionally atomic reflection + recording dedupe. Scope reset to demo-owned nodes; never delete unrelated graph data. Seed full guide graph, preserve score math and immutable evidence. Prove reset →25%, reflection →39%, duplicate unchanged against real Neo4j. |
| BAND / `feat/band-agents` | `server/band_client.py`, `server/agents/`, own dependency file if needed | Verify current BAND SDK/remote-agent docs; configure two real agents and credentials. Implement `opportunity(state) → Mission` and `reflection(mission, recording_id, transcript) → ReflectionResult`. Validate output. Agent owns reasoning, backend owns mutation. Provide observed BAND activity and run sample transcript; unknown IDs and invalid scores must fail. Never silently use demo fixtures in live mode. |
| PLAUD / `feat/plaud-bridge` | `server/plaud_bridge.py`, `scripts/inject_demo_transcript.py` | Run `plaud login`, capture actual `plaud today` / `plaud transcript <id>` output. Add conservative parser using that observed format. Snapshot baseline; poll only new IDs, detect once, retry not-ready transcripts with bounded attempts. Use argv subprocess calls, timeout, no shell. Persist processed IDs atomically only after successful backend submission; make pending detections recoverable after restart. Check token auth, useful errors, and real synced recording reaching backend without upload. |
| Integrator / `feat/demo-deploy` | `contracts/`, `.env.example`, `README.md`, `Makefile`, `server/main.py`, `server/settings.py`, `server/event_bus.py`, `server/schemas.py`, `server/test_flow.py` | Freeze contracts, review small merges, coordinate adapter selection in startup, run checks + live flow twice. Keep simulation labels until corresponding services are real. Record video; deployment only after local success. |

## Minutes 5–35 — parallel build

Frontend already works against demo API; do not block on service accounts.
Backend and BAND can exercise fixtures in `contracts/examples/`. PLAUD uses token-protected endpoints.
If credentials or the CLI are unavailable, report the blocker by minute 15 and keep the clearly labeled replay usable.
Live mode startup should be enabled only after both live store and BAND implementations exist.

## Minutes 35–50 — merge and prove

1. Integrator merges backend/graph; run backend checks and seed real graph.
2. Merge BAND; prove validated mission and transcript-grounded reflection.
3. Merge PLAUD; prove one new synced recording drives the same endpoints.
4. Merge UI polish; run typecheck and production build.
5. Reset → opportunity → evidence → reflection → graph → adaptation, twice.

Use `make check` after each merge. Keep one worker until storage/event concurrency is revisited.
Do not let agent adapters mutate memory/Neo4j, change score math, or bypass validation.
If a service fails, publish an `error` activity; label replay explicitly instead of pretending it is live.

## Minutes 50–60 — record and hand off

Record a browser-first walkthrough with simulation/live status clearly stated.
Show 82% knowledge /25% application, mission, evidence path, 39% application, gap, next target.
A one-hour success means the complete demo works and any live claims are verified; unavailable sponsor accounts cannot be solved by scaffolding.

## Interface ownership

- Opportunity consumes a snapshot of state and returns a Pydantic `Mission`.
- Reflection consumes the registered mission, recording ID, and transcript, returning `ReflectionResult`.
- Store validates referenced IDs, owns dedupe and deterministic score mutation, and returns JSON snapshots.
- Bridge registers a recording before submitting its transcript; retry with the same ID is safe.
- Backend binds each recording to the active mission at registration. Do not accept client-selected mission IDs.
- Live Neo4j transaction must commit evidence, experience, gap, score, and mission completion together.
