# ApplyLoop API v1 — frozen starter contract

Base: `http://localhost:8000`. JSON names match the supplied guide. Examples live in `examples/`.
`schemas.json` is generated from backend validation models; `/openapi.json` is the complete runtime schema.

| Method / path | Request | Response |
|---|---|---|
| GET `/health` | — | `{"status":"ok"}` |
| GET `/api/state` | — | `state.json`: learner, scores, context, mission, next_target, mode |
| GET `/api/graph` | — | `{nodes, edges}`; nodes carry semantic `type`; UI maps to React Flow default nodes and owns positions |
| POST `/api/demo/reset` | — | demo only (404 otherwise); seeded state; clears recordings, experiences, activity, pending opportunity; publishes context.added with `data.reset=true` |
| POST `/api/context` | `{title,type,location}` | context with generated ID; 409 while a mission is assigned |
| POST `/api/opportunity` | — | 202 `{request_id,status:"processing"}`; mission arrives via SSE; 409 if any active mission exists/pending (select an activity after completion) |
| POST `/api/internal/plaud/recording` | `{recording_id,title}` | `{status:"detected"}` or `"duplicate"`; binds to active mission |
| POST `/api/internal/plaud/transcript` | `{recording_id,transcript}` | `{status:"completed",reflection}`, `{status:"irrelevant",reflection}` or `{status:"duplicate"}`; relevant completion queues the next opportunity; unknown recording 404 |
| POST `/api/internal/plaud/status` | `{status,message}`; status is `waiting`, `transcript_waiting`, or `error` | `{status:"accepted"}`; publishes bridge activity |
| POST `/api/demo/replay` | — | demo only (404 otherwise); prerecorded transcript via same reflection/update path; create mission first |
| GET `/api/activities` | — | per activity (Spanish class, restaurant, dance class): `pick_up` (where you left off there), `last_visit`, `can_say` / `almost` phrases, `practice` words, `lesson` to do before going |
| POST `/api/activities/{id}/start` | — | "I'm going now": makes the activity the mission context; archives a completed mission. 404 unknown, 409 mission in progress |
| GET `/api/learner/progress` | — | level (current, fluent_level, per-level learned/fluent %), resume point, lessons with status, words with status, counts |
| POST `/api/lessons/{id}/progress` | `{step}` | progress; saves the resume point, introduces the lesson's words. 404 unknown, 409 locked / step past end |
| POST `/api/lessons/{id}/complete` | `{score}` | `{passed, progress}`; score ≥ .7 passes: words → practiced, resume → next lesson. Fail resets to step 0 |
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

## Learner knowledge graph (vocabulary, level, resume point)

`ReflectionResult.word_evidence` (optional, default `[]`): `{word_id, outcome, evidence}` with outcome
`used_correctly | used_incorrectly | not_understood`. Unknown word IDs reject the whole reflection (nothing changes).
Word status: `new → introduced` (lesson started) `→ practiced` (lesson passed or one real use) `→ fluent`
(correct real-world use in ≥2 recordings across ≥2 distinct contexts). Struggles never demote a word.
Level: highest consecutive CEFR level with ≥80% of its words practiced; `fluent_level` needs ≥50% fluent.
State adds `graph_backend` (`neo4j` | `memory`) and `focus_words` (practiced-but-not-fluent words the current context needs).
New event stage `learn` (types `lesson.progress`, `lesson.completed`) and event `vocabulary.updated` (stage `graph`).
Rules live in `server/learning_rules.py`; agents never set statuses, scores or levels.
Activities: `(:Learner)-[:DOES]->(:Activity:Context)-[:IS_A]->(:Scenario)`; demo missions and replays are chosen per activity
(skill). Replay recording IDs are `replay-<mission id>`, so each mission has at most one replay.

State includes `opportunity_pending` and `last_feedback` (last relevant reflection, nullable for older experiences). Agents receive saved activities and full proficiency/word progress. Reflection first decides `relevant` with `relevance_reason`; excluded evidence never changes proficiency or completes the mission. Relevant progress computes word statuses and CEFR levels with deterministic rules; the model never invents a level.
