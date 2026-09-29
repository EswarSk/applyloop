# ApplyLoop API v1 — frozen starter contract

Base: `http://localhost:8000`. JSON names match the supplied guide. Examples live in `examples/`.
`schemas.json` is generated from backend validation models; `/openapi.json` is the complete runtime schema.

| Method / path | Request | Response |
|---|---|---|
| GET `/health` | — | `{"status":"ok"}` |
| GET `/api/state` | — | `state.json`: learner, scores, context, mission, next_target, mode |
| GET `/api/graph` | — | `{nodes, edges}`; nodes carry semantic `type`; UI maps to React Flow default nodes and owns positions |
| POST `/api/demo/reset` | — | seeded state; clears recordings, experiences, activity, pending opportunity; publishes context.added with `data.reset=true` |
| POST `/api/context` | `{title,type,location}` | context with generated ID; 409 if any mission exists |
| POST `/api/opportunity` | — | 202 `{request_id,status:"processing"}`; mission arrives via SSE; 409 if any mission exists/pending (reset between demo runs) |
| POST `/api/internal/plaud/recording` | `{recording_id,title}` | `{status:"detected"}` or `"duplicate"`; binds to active mission |
| POST `/api/internal/plaud/transcript` | `{recording_id,transcript}` | `{status:"completed",reflection}` or `{status:"duplicate"}`; unknown recording 404 |
| POST `/api/internal/plaud/status` | `{status,message}`; status is `waiting`, `transcript_waiting`, or `error` | `{status:"accepted"}`; publishes bridge activity |
| POST `/api/demo/replay` | — | explicitly labeled demo fixture via same reflection/update path; create mission first |
| GET `/api/events` | — | SSE `id:` and `data:` JSON Activity, keepalive every 15s |

Internal endpoints require `X-Internal-Token` matching `.env` `INTERNAL_API_TOKEN`; invalid token is 401.
Recording registration optionally accepts `X-Expected-Mission-ID`; a mismatched active/registered mission returns 409 without changing its binding.
Malformed fields / scores outside [0,1] are 422. Errors use `{"detail": ...}`.
No arbitrary mission association: recordings bind to the active mission; reflection IDs must match that binding.
IDs max 200 chars, labels/evidence max 2000, transcripts 10–100000 chars. Unknown skills are rejected.
Duplicate recording/transcript is safe. Application delta: ≥.8 +.15, ≥.6 +.14, ≥.4 +.08, otherwise +.03; cap 1.
Demo reflection is a fixed saved result and is never claimed to analyze arbitrary transcripts.

State adds `context`, `next_target` (nullable), and `mode` to the guide's learner payload.
`GET /api/state` also adds `services`: `band` (`demo`/`live`), `storage` (`memory`/`neo4j`), and `plaud` (`connected`/`disconnected`, based on the last bridge report).
Graph example in guide shows a post-reflection subset; backend returns the whole current graph.
Context `starts_at` is optional when added via API. Mission status is assigned/completed.
Allowed event stages: context, opportunity, mission, plaud, reflection, graph, adapt.
Event status: processing/completed/error. Event types follow section 7.5 of the guide.

SSE replays the last 100 events on reconnect; deduplicate by event ID. History is in memory, not a durable log.
Clients refetch state/graph on connection and relevant events. Reset event discards prior UI activity.
Single process / single learner demo only. For multi-worker deployment, add durable event coordination.
