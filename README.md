# ApplyLoop

A shareable hackathon starter for turning learning into real-world practice.
**The complete local demo runs today. Live BAND, Neo4j, and PLAUD integrations are team tasks, not completed integrations.**

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
`make web` loads root `.env`; when invoking Next.js directly, put frontend variables in `web/.env.local`.
Backend OpenAPI documentation: http://localhost:8000/docs.

## Share and split

Share this repository or its ZIP with the team. Start with [the one-hour task board](docs/TEAM_TASKS.md).
Each person has a file ownership boundary, branch name, acceptance check, and merge order.
Do not edit another workstream's files without coordinating with the integrator.

- `web/`: frontend dashboard, React Flow graph, and SSE listener.
- `server/main.py`, `schemas.py`, `event_bus.py`: backend orchestration and validated boundaries.
- `server/neo4j_store.py`: deterministic demo graph; Neo4j owner's replacement point.
- `server/band_client.py`, `server/agents/`: BAND owner's replacement point and demo fixtures.
- `server/plaud_bridge.py`: tested HTTP handoff; PLAUD owner adds CLI detection/retries.
- `contracts/`: frozen API, runnable examples, and generated JSON Schema.
- `docs/IMPLEMENTATION_GUIDE.md`: supplied design reference; see task board for the tighter one-hour scope.

## Honest demo and current limits

Demo uses **in-memory storage, fixed agent fixtures, and an explicit prerecorded replay**.
Reflection fixtures do not analyze arbitrary transcript text. No external-service activity is claimed as live.
`DEMO_MODE=false` refuses startup until live storage and agent adapters are implemented.
One backend process and one demo learner only; restarting loses state. Run only on localhost until hardening.
Internal endpoints require `X-Internal-Token`; public reset/replay endpoints are intentionally local demo controls.
Change the example token before exposing a backend. Never commit `.env` or PLAUD authentication.

The guide's 2–3 hour live integration target is compressed into a one-hour plan by shipping the working demo first.
Finish actual sponsor integrations in parallel, prove them, and record a clean demo before deployment.
