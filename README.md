# ApplyLoop

A shareable hackathon starter for turning learning into real-world practice.
**The complete demo and live BAND/PLAUD flow are implemented. Set `NEO4J_URI` to persist the learner knowledge graph in Neo4j.**

## Run in three terminals

Requires Python 3.11+ and Node.js 20.9+.

```sh
make setup
```

```sh
make api
```

```sh
make web
```

Open http://localhost:3000. Click **Find opportunity**, then **Play prerecorded demo replay**.
Watch the activity pipeline, graph, application score **25% → 39%**, and next learning target.
Reset and run again. No external accounts or secrets are needed in demo mode.

`make replay` also exercises the exact internal recording/transcript endpoints the real bridge will use.
`make check` runs backend checks and TypeScript validation. `cd web && npm run build` checks the production frontend.
Dependencies are pinned in `server/requirements.lock.txt` and `web/package-lock.json`.
`make web` loads root `.env` with Node; Next.js exposes only `NEXT_PUBLIC_*` variables to the browser.
Backend OpenAPI documentation: http://localhost:8000/docs.

## Neo4j learner knowledge graph

The graph tracks, per learner: **every word learned and its status** (new → introduced → practiced → fluent),
**the CEFR level** (A1, A2) and whether they are fluent at it, **lessons passed/attempted**, and **where they stopped**
(the resume point). A word only becomes *fluent* after it is used correctly in real recordings, in 2+ different contexts,
not just after a quiz. That knowledge-vs-application gap is what ApplyLoop exists to close.

Without `NEO4J_URI` the same model runs in memory. To use Neo4j (a free Aura instance works):

```sh
# .env
NEO4J_URI=neo4j+s://<id>.databases.neo4j.io
NEO4J_PASSWORD=<password>
```

```sh
make api          # initializes an empty graph; preserves existing progress on restart
# Optional destructive demo reset: make seed-graph deletes all :ApplyLoop nodes.
# Tests require a SEPARATE database (never use your app URI):
NEO4J_TEST_URI=bolt://localhost:17687 NEO4J_TEST_PASSWORD=<test-password> make test-graph
```

The API seeds an empty graph on startup, and **Reset demo** reseeds it. See [docs/NEO4J.md](docs/NEO4J.md)
for the graph model and ready-to-paste Neo4j Browser queries for the pitch.

## Live BAND and PLAUD

1. Register two remote agents at BAND: an opportunity agent and a reflection agent. They run locally in this backend; BAND routes their requests and replies.
2. Set `BAND_MODE=live`, `OPENAI_API_KEY`, and both `BAND_*_AGENT_ID` / `BAND_*_API_KEY` pairs in `.env`. `BAND_ROOM_ID` is optional; startup logs the room ID for reuse.
3. Set `NEO4J_URI`, `NEO4J_PASSWORD`, and optionally `NEO4J_DATABASE`. Set `DEMO_MODE=false` to disable reset/replay controls and require persistent storage. Live BAND can also run with `DEMO_MODE=true` for local replay checks.
4. Run `make plaud-install`, then `plaud login` in your terminal. Start `make api`, `make web`, and `make plaud` in three terminals.
5. Choose an activity, click **Find opportunity**, then make a new PLAUD recording. Sync it and generate its transcript in PLAUD. The bridge retries while the transcript is pending; `make plaud-retry` resumes exhausted deliveries.
6. Reflection validates transcript excerpts and curriculum IDs before atomically saving the experience, scores, vocabulary evidence and next target. After completion, the next opportunity is generated automatically from saved activities, proficiency, vocabulary, and the last feedback. Unrelated recordings are excluded without completing the mission or changing progress.

The bridge saves its cursor and pending deliveries in `.plaud_bridge_state.json`. Keep this file across restarts; duplicates do not count twice. On first start, existing PLAUD recordings are baselined rather than assigned to a new mission. Pending recordings retain their original mission binding.

## Share and split

- `server/neo4j_store.py`: persistent graph transactions and constraints.
- `server/memory_store.py`: offline equivalent with the same learning rules.
- `server/learning_rules.py`, `server/data/curriculum_es.json`: deterministic scores and Spanish curriculum.
- `server/band_client.py`, `server/agents/`: native BAND routing and validated model output.
- `server/plaud_bridge.py`: CLI detection, transcript retries and durable delivery state.
- `web/`: dashboard, activities, vocabulary, graph and SSE updates.
- `contracts/`: API documentation, examples and generated validation schemas.

## Current limits

Demo agents use fixed fixtures; the replay is explicitly prerecorded. Live agents analyze the supplied transcript; the backend copies original excerpts as evidence and owns all score/status changes. Service labels show actual BAND, storage and bridge modes.

Saved restaurant, Spanish-class and dance-class routines stand in for future calendar/location integrations. One backend process and one learner per database. Memory storage loses progress on restart; Neo4j preserves it. Seeded lessons and vocabulary are illustrative starter data, not imported personal history. Startup fails on invalid constraints or a different learner's existing graph rather than wiping it. SSE history is in memory; reconnect refetches persisted state.

Run on localhost. Internal endpoints require `X-Internal-Token`; replace the example token before sharing access. Never commit `.env`, PLAUD authentication or bridge state. Changing API/frontend ports requires matching `WEB_ORIGIN`, `API_BASE_URL` and `NEXT_PUBLIC_API_BASE_URL`.
