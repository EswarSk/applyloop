# ApplyLoop — Hackathon Technical Implementation Guide

**Goal:** Build a presentable, locally runnable, optionally deployable demo where a real-world learning opportunity is generated, a PLAUD recording is automatically detected, BAND agents convert the transcript into structured learning feedback, and Neo4j visibly updates the learner/application graph.

**Hackathon build target:** 2–3 hours, 3–5 people working in parallel.

**Non-goals for the first build:** Native PLAUD SDK, Google Calendar OAuth, Maps/location integration, user authentication, vector database, generic course platform, multiple learning domains, background mobile app.

---

## 1. What we are building

ApplyLoop closes the gap between **learning something** and **actually applying it**.

Core loop:

```text
Learner state in Neo4j
        ↓
Real-world context/event
        ↓
BAND Opportunity Agent
        ↓
Practice Mission
        ↓
Real-world attempt captured with PLAUD
        ↓
PLAUD recording syncs to account
        ↓
Local PLAUD Bridge detects new recording automatically
        ↓
Transcript becomes available
        ↓
BAND Reflection Agent
        ↓
Deterministic Neo4j update
        ↓
UI animates graph changes + next learning target
```

### The demo story

1. Learner is studying conversational Spanish.
2. Neo4j says **Restaurant Ordering: knowledge 82%, applied confidence 25%**.
3. The app has a demo context: **Dinner at a Mexican restaurant**.
4. User clicks **Find an opportunity**.
5. BAND Opportunity Agent creates a mission: *Order your meal in Spanish and respond to one follow-up question.*
6. User completes or simulates the real-world attempt and records a short reflection with a PLAUD device.
7. No upload/copy-paste is done in the app.
8. PLAUD Bridge notices a new recording once it reaches the PLAUD account and obtains the transcript using the official CLI.
9. BAND Reflection Agent extracts demonstrated skills and gaps.
10. Backend updates Neo4j.
11. UI visibly changes: **Application 25% → 39%**, adds *Follow-up questions* as a gap, and proposes the next learning target.

---

## 2. Architecture

```text
                                  ┌────────────────────────┐
                                  │   ApplyLoop Web UI     │
                                  │ Next.js + React Flow   │
                                  └───────────┬────────────┘
                                              │ REST + SSE
                                              ▼
                                  ┌────────────────────────┐
                                  │   FastAPI Backend      │
                                  │ API + event publisher  │
                                  └──────┬────────┬────────┘
                                         │        │
                              ┌──────────▼───┐ ┌──▼───────────┐
                              │    BAND      │ │    Neo4j     │
                              │ remote agents│ │    Aura      │
                              └──────────────┘ └──────────────┘
                                         ▲
                                         │ transcript/reflection
                              ┌──────────┴─────────┐
                              │ Local PLAUD Bridge │
                              │ Python subprocess  │
                              └──────────▲─────────┘
                                         │ plaud CLI
                                         ▼
                                  PLAUD account
                                         ▲
                                         │ sync
                                  Physical PLAUD
```

### Why this architecture

- **BAND:** visible multi-agent orchestration; remote agents run in our environment and connect through BAND.
- **Neo4j:** represents relationships between learner, skills, contexts, missions, experiences, evidence, and gaps.
- **PLAUD:** provides real-world evidence after the learner leaves the learning app.
- **SSE:** makes every backend transition visible immediately in the browser, which is critical for the demo video.
- **Local bridge:** lets us avoid native PLAUD development while keeping the PLAUD step automatic.

---

## 3. Team rules before anyone codes

### 3.1 Freeze interfaces first — 10 minutes maximum

Create the repository and commit the contracts in this document **before splitting work**. All teammates build against the same JSON payloads.

### 3.2 One branch per workstream

Recommended branches:

```text
main
feat/frontend
feat/backend-graph
feat/band-agents
feat/plaud-bridge
feat/demo-deploy        # optional fifth person
```

Avoid editing the same files across branches. Merge through small pull requests or direct reviewed merges if time is limited.

### 3.3 One integrator owns `main`

The integrator should not build a major subsystem. Their job is:

- freeze API contracts;
- maintain `.env.example`;
- resolve merges;
- run end-to-end tests;
- keep the demo state reproducible;
- record the final video.

### 3.4 Use mocks from minute one

Frontend should never wait for BAND, PLAUD, or Neo4j. It should start against static JSON matching the contracts below. Backend should expose a `DEMO_MODE=true` fallback so integration can continue if one external service fails.

---

## 4. Work split

| Person | Owns | Must deliver | Does not touch |
|---|---|---|---|
| A — Integrator | contracts, main branch, demo | end-to-end run, `.env.example`, merge order, video | detailed UI or agent prompts |
| B — Frontend | Next.js UI | dashboard, pipeline animation, React Flow graph, SSE listener | Neo4j queries, PLAUD CLI |
| C — Backend/Neo4j | FastAPI + graph | API endpoints, seed data, deterministic graph updates | visual design, BAND internals |
| D — BAND | agents | Opportunity + Reflection remote agents, structured outputs | PLAUD polling, UI |
| E — PLAUD/Deploy | bridge + optional deploy | automatic recording detection, transcript handoff, deploy if time | agent reasoning |

### If only 3 people

- Person 1: Frontend
- Person 2: FastAPI + Neo4j
- Person 3: BAND + PLAUD Bridge + integration

### If only 2 people

- Person 1: UI + backend contracts + integration
- Person 2: Neo4j + BAND + PLAUD Bridge

---

## 5. Repository layout

```text
applyloop/
├── README.md
├── .env.example
├── Makefile
├── contracts/
│   ├── API.md
│   └── examples/
│       ├── state.json
│       ├── graph.json
│       ├── activity.json
│       └── reflection.json
│
├── web/
│   ├── app/
│   │   └── page.tsx
│   ├── components/
│   │   ├── LearningState.tsx
│   │   ├── ContextCard.tsx
│   │   ├── MissionCard.tsx
│   │   ├── ApplicationLoop.tsx
│   │   ├── PlaudStatus.tsx
│   │   ├── AgentActivity.tsx
│   │   └── LearningGraph.tsx
│   └── lib/
│       ├── api.ts
│       └── types.ts
│
├── server/
│   ├── main.py
│   ├── settings.py
│   ├── schemas.py
│   ├── event_bus.py
│   ├── neo4j_store.py
│   ├── demo_seed.py
│   ├── band_client.py
│   ├── plaud_bridge.py
│   └── agents/
│       ├── opportunity_agent.py
│       └── reflection_agent.py
│
└── scripts/
    ├── seed.sh
    ├── reset_demo.sh
    └── smoke_test.sh
```

---

## 6. Environment variables

Create `.env.example` immediately:

```bash
# Application
APP_ENV=local
DEMO_MODE=false
WEB_ORIGIN=http://localhost:3000
API_BASE_URL=http://localhost:8000

# Model provider used by BAND remote agents
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini

# Neo4j Aura
NEO4J_URI=neo4j+s://...
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=

# BAND — create two Remote Agents
BAND_OPPORTUNITY_AGENT_ID=
BAND_OPPORTUNITY_API_KEY=
BAND_REFLECTION_AGENT_ID=
BAND_REFLECTION_API_KEY=

# PLAUD bridge
PLAUD_POLL_SECONDS=3
PLAUD_TRANSCRIPT_RETRY_SECONDS=4
PLAUD_TRANSCRIPT_MAX_ATTEMPTS=45
```

**Never commit real keys.** Each teammate copies `.env.example` to `.env` and fills only the values needed for their subsystem.

---

## 7. Shared data contracts

These contracts are the most important collaboration artifact. Do not change them without telling the whole team.

### 7.1 Learner state

```json
{
  "learner_id": "demo",
  "goal": "Conversational Spanish",
  "skills": [
    {
      "id": "restaurant-ordering",
      "name": "Restaurant ordering",
      "knowledge_score": 0.82,
      "application_score": 0.25
    },
    {
      "id": "follow-up-questions",
      "name": "Restaurant follow-up questions",
      "knowledge_score": 0.61,
      "application_score": 0.18
    }
  ],
  "active_mission": null
}
```

### 7.2 Context event

```json
{
  "id": "dinner-001",
  "title": "Dinner at Mexican restaurant",
  "type": "restaurant",
  "location": "San Francisco",
  "starts_at": "2026-09-29T19:00:00-07:00"
}
```

For the hackathon, this is seeded/demo input. Calendar/location integrations are future providers using the same structure.

### 7.3 Mission

```json
{
  "id": "mission-001",
  "skill_id": "restaurant-ordering",
  "context_id": "dinner-001",
  "title": "Use restaurant Spanish tonight",
  "challenge": "Order your entree in Spanish and respond to one follow-up question.",
  "reason": "Your knowledge is high, but real-world application is low and tonight's context matches this skill.",
  "confidence": 0.94,
  "status": "assigned"
}
```

### 7.4 Reflection result — BAND output

The Reflection Agent returns **structured analysis only**. It does not directly mutate Neo4j.

```json
{
  "mission_id": "mission-001",
  "recording_id": "plaud-recording-id",
  "success_score": 0.72,
  "demonstrated": [
    {
      "skill_id": "restaurant-ordering",
      "evidence": "Learner reports successfully ordering the meal in Spanish.",
      "confidence": 0.93
    }
  ],
  "gaps": [
    {
      "skill_id": "follow-up-questions",
      "name": "Restaurant follow-up questions",
      "evidence": "Learner could not understand the salsa-choice question.",
      "confidence": 0.91
    }
  ],
  "next_target": {
    "skill_id": "follow-up-questions",
    "reason": "The real-world attempt revealed a listening gap during an unexpected follow-up question."
  }
}
```

### 7.5 Activity event — SSE

```json
{
  "id": "evt-123",
  "type": "plaud.recording.detected",
  "stage": "plaud",
  "status": "completed",
  "message": "New PLAUD recording detected",
  "created_at": "2026-09-29T12:10:04-07:00",
  "data": {
    "recording_id": "..."
  }
}
```

Allowed demo event types:

```text
context.added
opportunity.started
opportunity.completed
mission.created
plaud.waiting
plaud.recording.detected
plaud.transcript.waiting
plaud.transcript.ready
reflection.started
reflection.completed
graph.updating
graph.updated
learning_path.adapted
error
```

---

## 8. API contract

### `GET /health`

Returns:

```json
{"status":"ok"}
```

### `POST /api/demo/reset`

Resets Neo4j to deterministic demo state and clears active in-memory events.

### `GET /api/state`

Returns learner state, skills, context, and current mission.

### `POST /api/context`

Request:

```json
{
  "title": "Dinner at Mexican restaurant",
  "type": "restaurant",
  "location": "San Francisco"
}
```

### `POST /api/opportunity`

Starts the Opportunity Agent. Return quickly:

```json
{
  "request_id": "opp-001",
  "status": "processing"
}
```

Progress is shown through SSE. When complete, `GET /api/state` includes the mission.

### `POST /api/internal/plaud/recording`

Called only by the local PLAUD Bridge.

```json
{
  "recording_id": "...",
  "title": "..."
}
```

### `POST /api/internal/plaud/transcript`

```json
{
  "recording_id": "...",
  "transcript": "I managed to order..."
}
```

Backend stores evidence and triggers Reflection Agent.

### `GET /api/graph`

React Flow friendly response:

```json
{
  "nodes": [
    {"id":"learner","type":"learner","data":{"label":"You"}},
    {"id":"restaurant-ordering","type":"skill","data":{"label":"Restaurant ordering","knowledge":0.82,"application":0.39}}
  ],
  "edges": [
    {"id":"e1","source":"learner","target":"restaurant-ordering","label":"APPLIED"}
  ]
}
```

### `GET /api/events`

SSE endpoint. Frontend maintains a single `EventSource` connection and updates pipeline state as events arrive.

---

## 9. Neo4j graph model

### Nodes

```text
:Learner
:Goal
:Skill
:ContextEvent
:Mission
:Experience
:Evidence
:LearningGap
```

### Relationships

```text
(:Learner)-[:PURSUING]->(:Goal)
(:Goal)-[:REQUIRES]->(:Skill)
(:Learner)-[:LEARNING {knowledge_score, application_score}]->(:Skill)
(:Learner)-[:HAS_CONTEXT]->(:ContextEvent)
(:Mission)-[:TARGETS]->(:Skill)
(:Mission)-[:TRIGGERED_BY]->(:ContextEvent)
(:Experience)-[:COMPLETED]->(:Mission)
(:Experience)-[:EVIDENCED_BY]->(:Evidence)
(:Experience)-[:DEMONSTRATED]->(:Skill)
(:Experience)-[:REVEALED]->(:LearningGap)
(:LearningGap)-[:ABOUT]->(:Skill)
```

### Seed state

```cypher
MERGE (u:Learner {id:'demo'})
SET u.name = 'Demo Learner'

MERGE (g:Goal {id:'conversational-spanish'})
SET g.name = 'Conversational Spanish'
MERGE (u)-[:PURSUING]->(g)

MERGE (ordering:Skill {id:'restaurant-ordering'})
SET ordering.name = 'Restaurant ordering'
MERGE (followup:Skill {id:'follow-up-questions'})
SET followup.name = 'Restaurant follow-up questions'

MERGE (g)-[:REQUIRES]->(ordering)
MERGE (g)-[:REQUIRES]->(followup)

MERGE (u)-[a:LEARNING]->(ordering)
SET a.knowledge_score = 0.82,
    a.application_score = 0.25

MERGE (u)-[b:LEARNING]->(followup)
SET b.knowledge_score = 0.61,
    b.application_score = 0.18

MERGE (ctx:ContextEvent {id:'dinner-001'})
SET ctx.title='Dinner at Mexican restaurant',
    ctx.type='restaurant',
    ctx.location='San Francisco',
    ctx.status='upcoming'
MERGE (u)-[:HAS_CONTEXT]->(ctx)
```

### Deterministic update rule

Do not let the LLM choose database math directly. Backend calculates the score change.

For demo:

```text
success_score >= .80 → +.15 application score
success_score >= .60 → +.14 application score
success_score >= .40 → +.08 application score
otherwise            → +.03 application score
```

Cap at `1.0`.

With the demo reflection `0.72`, Restaurant Ordering becomes **0.25 → 0.39**.

---

## 10. Backend implementation — Person C

### Step 1 — Bootstrap

```bash
mkdir -p server
cd server
python -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn neo4j pydantic-settings python-dotenv httpx
```

### Step 2 — Neo4j singleton

Use one `GraphDatabase.driver(...)` instance for the application. Run `driver.verify_connectivity()` during startup so a bad URI/password fails immediately.

### Step 3 — Implement seed/reset first

The first working backend endpoint should be:

```text
POST /api/demo/reset
```

This guarantees the demo can always be restored to a known state.

### Step 4 — Implement read endpoints

Implement:

```text
GET /api/state
GET /api/graph
```

Frontend can now stop using mock JSON.

### Step 5 — Implement event bus

For a single-process hackathon backend, use an in-memory list + `asyncio.Queue` subscribers.

Interface:

```python
await event_bus.publish(
    type="graph.updated",
    stage="graph",
    status="completed",
    message="Learning graph updated",
    data={...},
)
```

`GET /api/events` streams these messages as `text/event-stream`.

### Step 6 — Internal PLAUD endpoints

Implement `/api/internal/plaud/recording` and `/api/internal/plaud/transcript`. Protect only with a simple local token if needed; do not spend time on full auth.

### Step 7 — Reflection update transaction

Flow:

```text
ReflectionResult
     ↓ validate
Create Evidence
     ↓
Create Experience
     ↓
Create/merge LearningGap
     ↓
Update application_score deterministically
     ↓
Mark Mission completed
     ↓
publish graph.updated
     ↓
publish learning_path.adapted
```

### Definition of done

Person C is done when this works without BAND/PLAUD:

```bash
curl -X POST localhost:8000/api/demo/reset
curl localhost:8000/api/state
curl localhost:8000/api/graph
```

and a hardcoded ReflectionResult can update the graph from 25% to 39%.

---

## 11. Frontend implementation — Person B

### Step 1 — Bootstrap

```bash
npx create-next-app@latest web --ts --eslint --app
cd web
npm install @xyflow/react
```

No component library is required unless the teammate already knows one well.

### Step 2 — Build the static demo first

The first screen must look complete using `contracts/examples/*.json`.

Sections:

1. Header: **ApplyLoop — Turn knowledge into experience**
2. Learning State
3. Today's Context
4. Opportunity/Mission Card
5. Application Loop pipeline
6. Learning Graph
7. Agent Activity timeline

### Step 3 — Application Loop component

Stages:

```text
Context
Opportunity
Mission
PLAUD
Reflection
Graph
Adapt
```

States:

```text
idle       → empty circle
processing → animated pulse/spinner
completed  → checkmark
error      → warning
```

Do not make the demo dependent on terminal logs. All important activity must appear here.

### Step 4 — React Flow graph

Initial graph should visually show:

```text
You
 └─ LEARNING → Restaurant Ordering
                K:82% A:25%

Conversational Spanish
 └─ REQUIRES → Follow-up Questions
```

After reflection:

```text
Experience #1
 ├─ EVIDENCED_BY → PLAUD
 ├─ DEMONSTRATED → Restaurant Ordering
 └─ REVEALED → Follow-up Questions
```

A changed application score and new gap node should animate/highlight for several seconds.

### Step 5 — SSE integration

```ts
const es = new EventSource(`${API}/api/events`)
es.onmessage = (event) => {
  const activity = JSON.parse(event.data)
  // update pipeline + activity log
  // refetch /api/state or /api/graph on graph.updated
}
```

### Step 6 — Demo controls

Visible controls only:

```text
[Reset Demo]
[Find Opportunity]
```

Do **not** add Upload Recording. PLAUD evidence is automatic.

### Definition of done

Frontend is done when it can run fully using mocks and play the complete visual story even before real services are connected.

---

## 12. BAND agents — Person D

BAND remote agents remain on our infrastructure. BAND handles room identity/routing while our code owns tools, model, and logic.

### Step 1 — BAND setup

Create two **Remote Agents** in BAND:

```text
ApplyLoop Opportunity
ApplyLoop Reflection
```

Save each Agent UUID and API key immediately.

Install:

```bash
uv init
uv add "band-sdk[langgraph]" langchain-openai
```

### Step 2 — Opportunity Agent tools

Expose only these tools:

```text
get_learning_state()
get_upcoming_context()
create_mission(...)
```

Prompt responsibility:

```text
Find exactly one skill where:
- knowledge is meaningfully higher than application;
- an upcoming context naturally affords practice;
- the mission is safe, specific, and short.
```

Expected structured mission is the contract from section 7.3.

### Step 3 — Reflection Agent tools

Expose:

```text
get_active_mission()
submit_reflection_result(...)
```

The reflection agent receives the PLAUD transcript and must answer only from transcript evidence.

It identifies:

- what was demonstrated;
- what failed/was difficult;
- confidence for each conclusion;
- next learning target.

The agent **must not** calculate the final application score or execute arbitrary Cypher.

### Step 4 — BAND visibility

Use BAND events/messages for useful observable actions, for example:

```text
Read learner state
Matched skill to restaurant context
Created practice mission
Analyzing PLAUD evidence
Detected demonstrated skill
Detected learning gap
Submitted structured reflection
```

The web app should mirror these as activity items via our backend event bus. Do not expose private chain-of-thought; only actions/results.

### Step 5 — Integration strategy

Fastest option:

```text
FastAPI → invoke/notify agent
Agent tools → call FastAPI internal endpoints
```

If direct invocation becomes time-consuming, keep BAND visible as the agent runtime/room and let the integrator manually trigger a BAND mention during the first integration. The final recorded demo should still show the web UI as the primary surface.

### Definition of done

- Opportunity agent creates the exact Mission JSON.
- Reflection agent turns the sample transcript into the exact ReflectionResult JSON.
- Both are visible in BAND.

---

## 13. PLAUD Bridge — Person E

### Important constraint

For personal account data, PLAUD currently provides CLI/MCP rather than a public general-purpose API. The CLI is specifically documented for scripting and can list recordings and retrieve transcripts. PLAUD Embedded is the later production path for direct device/transcription integration.

### Step 1 — Install and authenticate

Requires Node.js 20+.

```bash
npm install -g @plaud-ai/cli
plaud login
plaud files
```

Useful commands:

```bash
plaud files
plaud today
plaud recent
plaud transcript <recording-id>
plaud summary <recording-id>
plaud audio <recording-id>
```

### Step 2 — Establish baseline

When `plaud_bridge.py` starts:

1. run `plaud today`;
2. collect all currently visible IDs;
3. store them in `seen_recording_ids`;
4. POST `plaud.waiting` activity to backend.

This prevents old recordings from being mistaken for the new demo recording.

### Step 3 — Poll for new recording

Every `PLAUD_POLL_SECONDS`:

```text
plaud today
    ↓
parse recording IDs
    ↓
new ID not in baseline?
    ↓ yes
POST /api/internal/plaud/recording
```

**Do not assume undocumented JSON output.** Parse the displayed ID column conservatively and test it against the installed CLI version before the demo.

### Step 4 — Poll transcript readiness

After detection:

```text
plaud transcript <id>
```

Retry until meaningful transcript text appears or max attempts is reached.

When ready:

```text
POST /api/internal/plaud/transcript
```

### Step 5 — Avoid duplicate processing

Keep a local state file:

```text
.plaud_bridge_state.json
```

Example:

```json
{
  "processed": ["id1", "id2"]
}
```

Only a recording discovered after bridge startup should drive the live demo.

### Step 6 — Demo safety fallback

Create a fallback script:

```bash
python scripts/inject_demo_transcript.py
```

It should call the exact same `/api/internal/plaud/transcript` endpoint with the prerecorded sample transcript. Use only if syncing/transcription latency threatens the judging demo. Do not pretend fallback text was live PLAUD evidence; clearly label it as a replay if used.

### Definition of done

Start bridge → create/sync a new PLAUD recording → bridge detects it without user file upload → transcript reaches FastAPI automatically.

---

## 14. Integrator sequence — Person A

Do not merge everything at once. Use this order.

### Checkpoint 1 — Contracts and static UI

Required:

```text
contracts committed
frontend runs with mocks
backend /health works
```

### Checkpoint 2 — Backend + Neo4j

Merge backend/graph branch.

Test:

```text
Reset → state shows 82% / 25%
Hardcoded reflection → graph shows 82% / 39%
```

### Checkpoint 3 — Frontend talks to backend

Replace mock state/graph with real endpoints. SSE remains mocked if necessary.

### Checkpoint 4 — BAND opportunity

`Find Opportunity` must create a mission and update the UI.

### Checkpoint 5 — PLAUD Bridge

New recording must trigger:

```text
PLAUD waiting
→ recording detected
→ transcript ready
```

### Checkpoint 6 — BAND reflection

Transcript triggers Reflection Agent and produces structured ReflectionResult.

### Checkpoint 7 — Neo4j update + visual animation

Final required chain:

```text
transcript.ready
→ reflection.started
→ reflection.completed
→ graph.updating
→ graph.updated
→ learning_path.adapted
```

### Checkpoint 8 — Full reset

Run the whole story twice from a clean state. If it works only once, the demo is not done.

---

## 15. End-to-end sequence diagram

```text
User         Web UI       FastAPI       BAND Opp.       PLAUD Bridge     BAND Reflect.      Neo4j
 |              |             |              |                |                |                |
 | Find Opp. -->|             |              |                |                |                |
 |              | POST opp. ->|              |                |                |                |
 |              |             |----------->  |                |                |                |
 |              |             |              | read state ------------------------------->     |
 |              |             |              | create mission ---------------------------->    |
 |              |<--- SSE mission.created ---|                |                |                |
 |              |             |              |                |                |                |
 | record with physical PLAUD |              |                |                |                |
 |              |             |              |                | poll account   |                |
 |              |             |<------ new recording ---------|                |                |
 |              |<---- SSE plaud.recording.detected ----------|                |                |
 |              |             |              |                | get transcript |                |
 |              |             |<--------- transcript ---------|                |                |
 |              |             |--------------------------------------------->  |                |
 |              |<--- SSE reflection.started ---------------------------------|                |
 |              |             |<--------- ReflectionResult -------------------|                |
 |              |             |------------------------------------------------------------->   |
 |              |<-------------------------- SSE graph.updated ------------------------------- |
 |              | GET graph ->|------------------------------------------------------------->   |
 |              |<------------| updated nodes/edges                                              |
```

---

## 16. Visual demo requirements

The browser must tell the story without opening terminals.

### Top left — learning state

```text
Restaurant Ordering
Knowledge      82%
Application    25%
```

### Top right — current context and mission

```text
Dinner at Mexican restaurant
7:00 PM

Opportunity Found
Order your entree in Spanish and respond to one follow-up question.
```

### Center — live application pipeline

```text
Context → Opportunity → Mission → PLAUD → Reflection → Graph → Adapt
   ✓           ✓          ✓        ◉         ○          ○       ○
```

### Bottom left — learning graph

React Flow shows nodes/relationships and visibly adds the experience/evidence/gap after reflection.

### Bottom right — agent activity

Examples:

```text
12:01:02  Opportunity Agent  Read learner graph
12:01:03  Opportunity Agent  Matched restaurant context
12:01:04  Mission            Practice mission created
12:02:42  PLAUD              New recording detected
12:02:49  PLAUD              Transcript ready
12:02:50  Reflection Agent   Analyzing experience
12:02:52  Reflection Agent   Gap: follow-up questions
12:02:53  Neo4j              Learning graph updated
12:02:54  ApplyLoop          Next target adapted
```

---

## 17. Demo recording script

### 0:00–0:10 — Problem

> Most learning tools track what you've studied. ApplyLoop tracks whether you can actually use it.

Show knowledge 82%, application 25%.

### 0:10–0:25 — Opportunity

Click **Find Opportunity**.

BAND creates mission from learner state + dinner context.

### 0:25–0:40 — Real world

Hold up physical PLAUD device.

> Learning happens inside apps, but application happens outside them. PLAUD gives us evidence from that real-world moment.

Record a 15–20 second reflection:

> I ordered my food and asked for water in Spanish. But when the waiter asked whether I wanted red or green salsa, I couldn't understand the question and switched to English.

### 0:40–1:00 — Automatic detection

Return focus to ApplyLoop. Do not upload or paste anything.

UI moves through:

```text
PLAUD recording detected
Transcript ready
Reflection Agent analyzing
```

### 1:00–1:15 — Adaptation

Graph changes:

```text
Application 25% → 39%
New gap: Follow-up questions
Next target: Rapid restaurant choice questions
```

### Close

> ApplyLoop turns learning into a continuous loop: learn, find an opportunity, apply it in the real world, capture evidence, and adapt what you learn next.

---

## 18. Testing checklist

### Backend

- [ ] `/health` returns 200
- [ ] reset is idempotent
- [ ] state query returns seeded scores
- [ ] graph endpoint returns React Flow structure
- [ ] duplicate transcript does not create duplicate Experience
- [ ] scores are capped at 1.0
- [ ] unknown mission/recording returns useful error

### BAND

- [ ] Opportunity Agent calls only approved tools
- [ ] Opportunity output validates against Mission schema
- [ ] Reflection output validates against ReflectionResult schema
- [ ] Reflection cites transcript evidence rather than inventing details
- [ ] agent failure publishes visible error activity

### PLAUD

- [ ] `plaud login` persists auth
- [ ] `plaud today` lists expected recordings
- [ ] startup baseline ignores old recordings
- [ ] new recording detected once
- [ ] transcript retry handles temporary unavailability
- [ ] processed ID persisted

### Frontend

- [ ] empty/idle state looks intentional
- [ ] activity events update without refresh
- [ ] graph refreshes on `graph.updated`
- [ ] changed node visibly highlights
- [ ] reset restores 25% application state
- [ ] no terminal is necessary to understand the demo

### Full flow

- [ ] run reset
- [ ] find opportunity
- [ ] mission visible
- [ ] record/sync PLAUD
- [ ] recording detected automatically
- [ ] transcript available
- [ ] reflection result produced
- [ ] Neo4j updated
- [ ] graph animation visible
- [ ] next target visible
- [ ] repeat successfully a second time after reset

---

## 19. Failure handling during judging

| Failure | What UI should show | Recovery |
|---|---|---|
| BAND unavailable | `Opportunity Agent unavailable` | use cached demo mission only if clearly identified |
| Neo4j unavailable | `Learning graph connection failed` | restart Aura credentials; do not fake graph state |
| PLAUD sync delayed | `Waiting for PLAUD sync/transcript…` | use prerecorded replay endpoint if time-critical and label it |
| PLAUD CLI auth expired | `PLAUD connection needs login` | run `plaud login` |
| Reflection model fails | `Reflection retrying…` | retry once; then use validated saved ReflectionResult for replay |
| SSE disconnects | small reconnect status | browser auto-reconnects EventSource |

### Demo rule

Record one clean successful demo video **before** attempting deployment or extra features.

---

## 20. Optional deployment after the local demo works

### Deployable split

```text
Vercel / web host
    └── Next.js UI

Cloud/DuploCloud/other host
    └── FastAPI + BAND remote agents

Neo4j Aura
    └── graph database

Presenter's laptop
    └── PLAUD Bridge + authenticated PLAUD CLI
```

The local bridge points to the deployed backend:

```bash
API_BASE_URL=https://api.example.com
```

This is a reasonable production direction because PLAUD credentials/account access remain on the user's machine while shared orchestration/database services can be deployed.

### Do not deploy until

- local full loop works twice;
- clean video is recorded;
- demo reset works;
- environment variables are documented.

---

## 21. Two-hour execution plan

### 0–10 min — all together

- create repo;
- copy contracts;
- create branches;
- create `.env.example`;
- decide integrator;
- confirm BAND/Neo4j/PLAUD accounts work.

### 10–60 min — parallel

- Frontend: static full UI from mocks.
- Backend: FastAPI + Neo4j seed/state/graph/update.
- BAND: two agents against local stub tools.
- PLAUD: CLI auth + baseline watcher + transcript extraction.

### 60–80 min — first integration

- frontend ↔ FastAPI;
- Neo4j real graph;
- event stream;
- hardcoded reflection proves graph animation.

### 80–100 min — external integrations

- BAND opportunity flow;
- PLAUD bridge → transcript;
- BAND reflection → backend update.

### 100–115 min — harden

- reset;
- dedupe;
- retry handling;
- clean labels;
- run full loop twice.

### 115–120 min — record

Record the successful local demo. Any deployment/extra feature work happens only after the recording exists.

---

## 22. Definition of done

The MVP is complete only when a viewer can watch one browser window and see:

1. learner knowledge vs application;
2. real-world context;
3. BAND-generated mission;
4. waiting for PLAUD evidence;
5. automatic PLAUD recording detection;
6. transcript-driven reflection;
7. Neo4j graph mutation;
8. updated application score;
9. newly discovered gap;
10. adapted next learning target.

If the demo requires manually uploading a recording, copying a transcript, opening Neo4j Browser, or explaining terminal output, the product is not finished yet.

---

## 23. Current implementation references

These links were checked for the hackathon implementation plan on September 29, 2026.

- BAND — Connect Any Agent: https://docs.band.ai/getting-started/connect-remote-agent
- BAND — Agents and remote-agent model: https://docs.band.ai/core-concepts/agents
- BAND — SDK overview: https://docs.band.ai/integrations/sdks/overview
- PLAUD CLI: https://support.plaud.ai/hc/en-us/articles/57751026815257-Plaud-CLI
- PLAUD API access / Embedded distinction: https://support.plaud.ai/hc/en-us/articles/60726890231449-How-can-I-get-API-access-to-my-Plaud-data
- Neo4j Python driver: https://neo4j.com/docs/python-manual/current/
- Neo4j connection/Aura: https://neo4j.com/docs/python-manual/current/connect/
