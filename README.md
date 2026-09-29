# ApplyLoop

A shareable hackathon starter for turning learning into real-world practice.
**The complete local demo runs today. The Neo4j learner knowledge graph is implemented (set `NEO4J_URI`); live BAND and PLAUD integrations are still team tasks.**

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
`make web` copies the `NEXT_PUBLIC_*` lines from root `.env` into `web/.env.local` (no secrets); when invoking Next.js directly, put frontend variables there.
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
make seed-graph   # reset + seed :ApplyLoop nodes only (curriculum, demo learner)
make test-graph   # run the store tests against your Neo4j (resets :ApplyLoop nodes)
make api          # the banner now says "Live Neo4j knowledge graph"
```

The API seeds an empty graph on startup, and **Reset demo** reseeds it. See [docs/NEO4J.md](docs/NEO4J.md)
for the graph model and ready-to-paste Neo4j Browser queries for the pitch.

## Share and split

Share this repository or its ZIP with the team. Start with [the one-hour task board](docs/TEAM_TASKS.md).
Each person has a file ownership boundary, branch name, acceptance check, and merge order.
Do not edit another workstream's files without coordinating with the integrator.

- `web/`: frontend dashboard, React Flow graph, and SSE listener.
- `server/main.py`, `schemas.py`, `event_bus.py`: backend orchestration and validated boundaries.
- `server/neo4j_store.py`: Neo4j knowledge graph store (all Cypher, one transaction per mutation).
- `server/memory_store.py`: in-memory store with the same interface, used when `NEO4J_URI` is empty.
- `server/learning_rules.py`, `server/data/curriculum_es.json`: fluency/level rules and the Spanish A1–A2 curriculum.
- `server/band_client.py`, `server/agents/`: BAND owner's replacement point and demo fixtures.
- `server/plaud_bridge.py`: tested HTTP handoff; PLAUD owner adds CLI detection/retries.
- `contracts/`: frozen API, runnable examples, and generated JSON Schema.
- `docs/IMPLEMENTATION_GUIDE.md`: supplied design reference; see task board for the tighter one-hour scope.

## Honest demo and current limits

Demo uses **fixed agent fixtures and an explicit prerecorded replay**; storage is Neo4j when `NEO4J_URI` is set, otherwise in-memory.
Reflection fixtures do not analyze arbitrary transcript text. No external-service activity is claimed as live.
`DEMO_MODE=false` refuses startup until live storage and agent adapters are implemented.
One backend process and one demo learner only; restarting loses state. Run only on localhost until hardening.
Internal endpoints require `X-Internal-Token`; public reset/replay endpoints are intentionally local demo controls.
Change the example token before exposing a backend. Never commit `.env` or PLAUD authentication.

The guide's 2–3 hour live integration target is compressed into a one-hour plan by shipping the working demo first.
Finish actual sponsor integrations in parallel, prove them, and record a clean demo before deployment.
