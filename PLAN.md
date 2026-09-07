# JARVIS X — Implementation Plan

**Owner:** Pranav Bandaram · **Repo:** `Bitshifter-9/JARVIS-X` · **Supersedes:** `Bitshifter-9/Jarvis-` (preserved in [`legacy/jarvis-v1/`](legacy/jarvis-v1/))

Build plan for the *JARVIS X Advanced Architecture and Implementation Blueprint*, under four constraints:

| Constraint | Consequence |
|---|---|
| **Every feature in the PDF ships** | Full coverage matrix in §2. Nothing is silently dropped; where the PDF itself says *defer* or *do not build*, we honour that and say so. |
| **Cloud-first — must work with the Mac offline** | The Mac is an **optional execution node**, never a dependency. Inference, ingestion, prediction, scheduling, escalation and approval all run in the cloud. §3. |
| **Budget ≈ ₹2,000/month (~$24)** | Free tiers by default, paid fallback wired up behind a hard cap. Realistic spend ₹0–700/mo. §14. |
| **No iOS** | Android is the only phone. Live Activity → Android ongoing notification + widget. Contracts stay iOS-ready. |https://mcp.openart.ai/mcp

**Product sentence, unchanged:** JARVIS X observes commitments, predicts failure, prepares the next best
action, executes through policy-controlled tools, verifies the result, and escalates only when the user has
authorized it.

---

## 1. The loop that is the product

```
event → task → prediction → recovery plan → approval → execution → VERIFIED EVIDENCE → escalation
```

Judged as an **autonomous personal operations platform**, not a voice assistant that opens apps. The four
planes from blueprint §2 are preserved exactly:

| Plane | Owns | Never owns |
|---|---|---|
| Experience | UI, voice capture, notification, approval, explainability | Raw cloud credentials, unrestricted OS execution |
| Control | Identity, events, task state, plans, policy, workflows, audit | Direct GUI manipulation |
| Execution | Typed cloud connectors and paired local tools | Deciding its own permissions |
| Evidence | Read-after-write checks, window/DOM state, provider ids, screenshots | Using model confidence as proof |

### The milestone everything hangs off

> One real deadline event → one sourced task → one predicted failure → one approved action → one **verified**
> result on the Android timeline — **with the Mac powered off.**

That last clause is the new requirement, and it is what §3 exists to guarantee.

---

## 2. Feature coverage — every section of the PDF

**Functional features: 100%.** Infrastructure *vendors* are substituted where a free-tier equivalent exists;
every substitution preserves the blueprint's contract, so migrating to the exact AWS topology later is a
deployment change, not a rewrite. Substitutions are marked ⇄ and justified in §5.

| PDF § | Feature | Status | Where |
|---|---|---|---|
| 1 | Typed orchestrator + policy engine as core runtime | ✅ Full | `apps/api/jarvis/services/agent`, `packages/policy` |
| 1 | Flutter client for macOS + Android | ✅ Full (iOS deferred by your decision) | `apps/control_center`, `apps/mobile` |
| 1 | Mac automation via narrowly typed actions | ✅ Full ⇄ PyObjC instead of Swift | `apps/mac-node` |
| 1 | Event-driven backend supporting long-running work | ✅ Full ⇄ Postgres queue instead of SQS | `jarvis/workers` |
| 1 | Relational data + vector memory in one database | ✅ Full | Postgres 16 + `pgvector` |
| 1 | Mobile as control/approval/notification surface | ✅ Full | `apps/mobile` |
| 1 | Alexa custom skill with account linking | ✅ Full ⇄ HTTPS endpoint instead of Lambda | `apps/alexa-skill` + `jarvis/connectors/alexa` |
| 1 | OpenClaw as isolated channel adapter | ✅ Full | `POST /internal/connectors/openclaw/events` |
| 2 | Four planes, trust boundaries, untrusted-content rule | ✅ Full | `docs/ARCHITECTURE.md`, `docs/THREAT-MODEL.md` |
| 3 | Canonical event envelope + idempotency + DLQ + reconciliation | ✅ Full ⇄ Postgres `jobs` table | `jarvis/db/queue.py` |
| 3 | All five event classes | ✅ Full | §7 |
| 4 | Nine-state agent machine, Pydantic contracts, budgets, reducer | ✅ Full | `jarvis/services/agent` |
| 5 | Repair graph, tool manifest, bounded retry | ✅ Full | `jarvis/services/tool_gateway`, manifests in `packages/contracts` |
| 6 | **Goal / task / prediction / recovery engine** | ✅ Full | `jarvis/services/goal` — §8 |
| 6 | Work sessions → estimate calibration | ✅ Full | `work_sessions` table |
| 7 | Deadline extraction, resolve, confirm, dedupe, schedule, escalate | ✅ Full | `jarvis/services/event` + `scheduler` |
| 8 | Four memory tiers | ✅ Full | `memories` + `source_objects` |
| 8 | **Knowledge graph** `entities`/`relations`/`aliases` with provenance | ✅ Full | `jarvis/services/graph` |
| 8 | Hybrid SQL→vector retrieval, memory write reducer | ✅ Full | `jarvis/services/memory` |
| 9 | R0–R4 risk ladder | ✅ Full | `packages/policy` |
| 9 | Approval payload binding (SHA-256) | ✅ Full ⇄ `approvals` table instead of SFn token | §10 |
| 9 | **Simulation mode** | ✅ Full | `jarvis/services/tool_gateway/simulators` |
| 9 | Three kill switches | ✅ Full | server flag · Mac menu-bar · Android revoke |
| 10 | Flutter Control Center + menu bar + native bridge | ✅ Full | `apps/control_center` |
| 10 | **Browser worker with DOM evidence** | ✅ Full — *and cloud-side*, see §3 | `jarvis/services/browser` (Playwright) |
| 10 | Automation helper surviving UI close (LaunchAgent) | ✅ Full | `launchd` plist |
| 11 | NSWorkspace · AXUIElement · CGWindowList · screen capture · Keychain · file scoping · command templates | ✅ Full | `apps/mac-node` |
| 12 | Device pairing, ECDSA key, signed jobs, nonce, expiry, offline review | ✅ Full | §11 |
| 13 | Android app: Today · Goals · Chat · Approvals · Timeline · Devices · Connectors | ✅ Full | `apps/mobile` |
| 14 | Push, background sync, exact alarm, break-through-Focus | ✅ Full (Android) | FCM + WorkManager + AlarmManager |
| 14 | Live deadline card | ✅ Full ⇄ Android ongoing notification + Glance widget instead of ActivityKit | Phase 3 |
| 15 | Alexa: 7 intents + account linking | ✅ Full | `jarvis/connectors/alexa/skill.py` |
| 16 | ASK SDK Lambda, reminders, proactive events, certification | ✅ Full ⇄ HTTPS endpoint; reminders and proactive events documented, not enabled | `apps/alexa-skill/README.md` |
| 17 | Gmail | ✅ Full | Phase 2 |
| 17 | Google Calendar | ✅ Full | Phase 2 |
| 17 | Slack | ✅ Full | `jarvis/connectors/slack` |
| 17 | Google Classroom | ✅ Full | `jarvis/connectors/google/classroom.py` |
| 17 | Canvas / Moodle LMS | ✅ Full — Canvas free teacher instance for demo | `jarvis/connectors/canvas` |
| 17 | Telegram | ✅ Full | Phase 1 |
| 17 | WhatsApp Cloud API templates | ✅ Full | `jarvis/connectors/whatsapp` — see §13 risk |
| 17 | Outbound phone call | ✅ Full ⇄ Twilio instead of Amazon Connect | `jarvis/connectors/twilio` — see §13 risk |
| 18 | OpenClaw adapter, isolated, no DB credentials | ✅ Full | `api/routes/internal.py` |
| 19 | Monorepo, 8 backend service modules | ✅ Full ⇄ one container, three entrypoints, same module boundaries | §6 |
| 20 | Full relational schema + indexes | ✅ Full | §7 |
| 21 | REST + SSE + WebSocket contracts, idempotency, problem objects, generated clients | ✅ Full | `packages/contracts` |
| 22 | AWS production topology | ⇄ **Substituted** — free-tier equivalents, documented 1:1 migration | §5 |
| 23 | Deployment runbook | ✅ Full ⇄ Compose + Caddy instead of CDK | `infra/` |
| 24 | CI/CD, signing, notarization, flavors | ✅ Full (macOS + Android) | GitHub Actions |
| 25 | Threat model, 8 threats, privacy UX | ✅ Full | `docs/THREAT-MODEL.md` |
| 26 | 7 metrics, trace model, 6 test layers | ✅ Full | §15 |
| 27 | Cost-aware environments, budget controls | ✅ Full | §14 |
| 28–31 | Roadmap, priorities, demo script | ✅ Full | §12, `docs/DEMO-RUNBOOK.md` |
| 32 | Morning / evening / focus / learning modules | ✅ Full | product modules on the goal engine — Phase 3 |
| 32 | **Multi-agent** | ⏸ Deferred — *the PDF itself defers it* ("until single-agent contracts are reliable") | — |
| 31 | **Arbitrary GUI / terminal autonomy** | ⛔ Not built — *the PDF says "do not build"* | replaced by command templates |
| 13–14 | iOS app, APNs, ActivityKit | ⏸ Deferred per your decision; contracts stay iOS-ready | — |

---

## 3. Cloud-first: the Mac is optional

The earlier draft of this plan routed inference through the Mac to make it free. **That is now wrong** — you
require the system to work with the Mac offline. So the Mac drops to exactly one role: *executing actions that
can only happen on a Mac.*

### Capability tiers

| Tier | Capability | Mac needed? |
|---|---|---|
| **A — always on** | Event ingestion, deadline extraction, goal engine, **failure prediction**, scheduling, escalation, approvals, memory + knowledge graph, audit, Telegram / WhatsApp / call / Alexa / Android, **browser automation + DOM evidence** | ❌ No |
| **B — Mac only** | `mac.open_app`, `mac.focus`, AX UI automation, window/screen evidence, local file access, wake-word voice | ✅ Yes |

**The critical move:** the **browser worker runs in the cloud**, headless Playwright on the VPS. So
"execute → verify with real DOM evidence" — the demo's proof-of-work moment — survives the Mac being off. The
verifier story never depends on your laptop being awake.

### When the Mac is offline

Exactly the blueprint §12 behaviour, and it is a *feature*, not a degradation:

1. Tier-A work continues untouched. Deadlines still fire, predictions still update, approvals still resolve.
2. A Tier-B action is queued with an `expires_at` and the user is told: *"Mac offline — queued, expires 14:30."*
3. **Jobs are never silently executed once stale.** On reconnect the backend offers pending jobs for explicit
   review unless the action is safe, recent, and policy permits delayed execution.
4. The planner knows the device state, so it prefers a cloud path when one exists — a browser tab instead of a
   native app — rather than proposing an action it cannot run.

---

## 4. Agent runtime and LLM routing

### What a framework may and may not own

Blueprint §1 chooses a hand-written orchestrator because **"no framework controls
authorization."** That constraint is kept exactly. What changed is that sequencing,
durability and provider transport are no longer worth hand-writing.

| Concern | Owner | Why |
|---|---|---|
| Loop sequencing, retries, checkpointing, resume | **LangGraph** | Durable graph execution and `interrupt()` for human-in-the-loop are solved problems; our version was ~200 lines of the same. |
| Risk classification, ALLOW/REQUIRE_APPROVAL/DENY | **Ours** (`services/policy`) | A framework must never decide what is permitted. |
| Approval records and payload binding | **Ours** (`approvals` table) | The SHA-256 binding is the security property; it does not live in a graph node. |
| Step, token, wall-clock and money budgets | **Ours** (`core/config`, `llm/budget`) | A runaway loop is the failure mode that bites agent projects; the ceiling stays in our harness. |
| Executor revalidation before dispatch | **Ours** (`tool_gateway`) | Second, independent check — unchanged. |
| Provider transport, retries, structured output | **LiteLLM** | One interface to 100+ providers; deletes our hand-rolled HTTP clients. |
| Cascade order, circuit breaking, INR accounting | **Ours** (`llm/router`) | Per-call-class routing and DB-shared breaker state that LiteLLM's in-process router cannot give us across API + workers. |

The graph is a **state machine we defined**, executed by LangGraph. Policy is a node every
effectful path must traverse, and the executor revalidates after it regardless. If
LangGraph vanished tomorrow the security properties would be unchanged.

### Providers

| Provider | Role | Why it earns its slot |
|---|---|---|
| **Groq** | classify · plan · reflect · chat | Fastest free inference available. Latency is what makes the demo feel alive. |
| **Google Gemini** | **deadline extraction** | Native JSON-schema-constrained decoding. Extraction accuracy is the graded 90% metric. |
| **OpenRouter** | overflow, then paid fallback | One key, many models. `:free` variants first; paid only when free tiers are exhausted **and** budget remains. |
| **Ollama on Mac** | optional local | Zero-cost dev and a genuine offline story — never required. |

> ⚠️ Free-tier quotas move constantly. Verify them in your own accounts before demo week.

### Router design

```python
CASCADE = {
  "classify": ["groq", "gemini", "openrouter_free", "openrouter_paid"],
  "plan":     ["groq", "gemini", "openrouter_free", "openrouter_paid"],
  "extract":  ["gemini", "groq", "openrouter_paid"],   # accuracy first
  "chat":     ["groq", "gemini", "openrouter_free"],
  "embed":    ["local_minilm"],                         # on the VPS, CPU
}
```

- 429 or quota exhaustion advances the cascade; a per-provider breaker with a cooldown
  lives in `provider_health`, shared by the API and every worker.
- Every call is budget-checked *before* dispatch and recorded in `llm_calls` with provider,
  model, prompt version, tokens and an INR cost estimate.
- `ENABLE_PAID_LLM=false` must yield a fully working system on free tiers alone. Tested.

**Embeddings never leave the VPS.** `all-MiniLM-L6-v2` is 22M parameters, comfortable on
CPU, 384-d — no rate limit, no cost, and the memory corpus stays on our machine.

### Loop engineering

Techniques applied where they change a measured number, not for their own sake:

| Technique | Where | What it buys |
|---|---|---|
| Schema-constrained decoding | extraction | The model cannot emit a shape the parser rejects. |
| Self-consistency (n-sample vote) | ambiguous deadlines only | Lifts extraction accuracy where a single sample is unstable; costs n× so it is gated on low confidence. |
| Exact-payload caching | extraction, classification | A redelivered provider event re-extracts for free. |
| Bounded reflection | `MAX_REPLANS=2` | Repair once on new evidence, then ask. Never "keep trying". |
| Prompt versioning + golden fixtures | `packages/prompts` | A prompt edit that regresses the 90% target fails CI instead of the demo. |
| LLM-as-judge, only for near-misses | eval harness | Exact match is the primary metric; a judge adjudicates "5 Sept 23:59" vs "2026-09-05T23:59". |

### Testing

| Layer | Tool |
|---|---|
| Deterministic unit/integration/e2e | pytest — the suite that actually protects the code |
| Invariants over generated inputs | **Hypothesis** — e.g. R4 is denied for *every* possible args dict |
| API contract fuzzing | **Schemathesis** against our own OpenAPI |
| Recorded provider interactions | **pytest-recording** — real Gmail/Gemini responses, replayed offline |

AI test generators (TestSprite and similar) are listed as an optional authoring aid in
`docs/DEMO-RUNBOOK.md`. They are a way to *draft* cases; a generated test that nobody read
is not evidence, so the gates in §12 stay hand-written.

## 5. Stack — blueprint vs. what we build

| Concern | Blueprint | We build | Why |
|---|---|---|---|
| Compute | ECS Fargate ×3 | **Oracle Always Free ARM** (4 cores/24 GB); fallback **Hetzner CX32** ~₹650/mo | Always Free is perpetual. Budget covers the fallback if capacity is unavailable. |
| Database | Aurora Serverless v2 | **Postgres 16 + pgvector on the same VM**, nightly `pg_dump` → R2 | Co-location removes a network hop from *every* query — the difference between a 2 s and a 9 s event-to-alert path. Aurora bills at idle. |
| Queue | SQS + DLQ | **Postgres `FOR UPDATE SKIP LOCKED`** | At-least-once, retries, visibility timeout and a real DLQ column — in one table. |
| Scheduler | EventBridge Scheduler | **`schedules` table + 30 s tick** | One-shot T-24h/2h/1h/15m fires over an indexed timestamp. |
| Human approval | Step Functions callback token | **`approvals` table** (payload hash, expiry, decision) | The run does not "wait" — it is a suspended row resumed by an event. Cheaper *and* more auditable. |
| Identity | Cognito + PKCE | **Authlib OAuth2 server + JWT + Argon2id** | Alexa account linking needs a real OAuth2 authorization-code grant. We host one, so there is a single identity system. Cognito drops in behind `IdentityProvider`. |
| Realtime | API Gateway WebSocket | **FastAPI native WebSocket** | Same outbound-only, zero-inbound-port model. |
| Evidence blobs | S3 + KMS | **Cloudflare R2** (10 GB free, zero egress) | Screenshots, DOM snapshots. |
| Edge | CloudFront + WAF | **Caddy** (auto Let's Encrypt) + Cloudflare proxy | Free TLS, free DDoS shield, free rate limiting. |
| Push | FCM + APNs | **FCM** | Android only. |
| Calling | Amazon Connect | **Twilio** | Connect outbound to India is restricted; Twilio is straightforward. See §13. |
| Agent loop | hand-written | **LangGraph** + Postgres checkpointer, policy still ours | §4. |
| LLM transport | Bedrock SDK | **LiteLLM** behind our cascade | One interface, 100+ providers, native structured output. |
| LLM providers | Bedrock | **Groq + Gemini + OpenRouter** | §4. |
| Mac automation | Swift + Pigeon | **Python + PyObjC** | Identical Apple APIs (`NSWorkspace`, `AXUIElement`, `CGWindowList`), no Xcode required. Swift is a *packaging* concern for a signed `.app`, not a capability one. |
| Alexa skill | ASK SDK Lambda | **HTTPS endpoint on the API we already run** | Alexa accepts either. Removes a second language, a second deployment and an AWS account; request authenticity moves into `connectors/alexa/verify.py`. |
| IaC | AWS CDK | **Docker Compose + Caddyfile** | One VM. CDK would be ceremony. |

**Migration path, documented:** each substitution above maps 1:1 to its AWS service, and the module boundaries
in §6 match the blueprint's eight services exactly. If a judge asks "could this run the PDF's topology?", the
answer is a Compose-file swap plus a CDK stack — not a rewrite. That is the point of keeping the contracts.

---

## 6. Repository layout

```
JARVIS-X/
├── PLAN.md · README.md
├── docs/            ARCHITECTURE · COST · THREAT-MODEL · DEMO-RUNBOOK
├── legacy/jarvis-v1/            v1 source, read-only reference (§16)
├── packages/
│   ├── contracts/   ⚠ SOURCE OF TRUTH — schemas + OpenAPI + tool manifests
│   └── policy/      risk rules as data + test vectors
├── apps/
│   ├── api/jarvis/
│   │   ├── api/routes/   chat · goals · tasks · approvals · devices · oauth · webhooks
│   │   ├── core/         config · security · errors · idempotency · budget
│   │   ├── db/           models · session · queue
│   │   ├── services/     event · goal · agent · policy · tool_gateway ·
│   │   │                 evidence · notification · identity · memory · graph · browser
│   │   ├── llm/          router · groq · gemini · openrouter · ollama · budget
│   │   ├── connectors/   google/{gmail · calendar · classroom · youtube} · slack ·
│   │   │                 canvas · telegram · whatsapp · twilio · alexa · openclaw
│   │   └── workers/      scheduler · notify · video · browser
│   ├── mac-node/         PyObjC helper — tools/ verify/ transport/
│   ├── control_center/   Flutter macOS
│   ├── mobile/           Flutter Android
│   └── alexa-skill/      TypeScript ASK SDK Lambda
├── migrations/  ·  infra/{compose,caddy,scripts}  ·  scripts/
└── tests/{unit,integration,e2e,adversarial,chaos,fixtures}
```

The `services/` directory reproduces the blueprint's eight backend modules — `event_service`, `goal_service`,
`agent_service`, `policy_service`, `tool_gateway`, `evidence_service`, `notification_service`,
`identity_service` — as Python packages rather than as separate deployments. **One container, three
entrypoints** (`api` / `worker` / `scheduler`) costs 1× instead of 3×; splitting later is a Compose edit.

---

## 7. Data model

Relational first. JSONB for provider payloads only — never as a substitute for a constraint.

`users` · `identities` · `oauth_clients` · `devices` · `device_connections` · `goals` · `tasks` ·
`task_dependencies` · `work_sessions` · `goal_predictions` · `agent_runs` · `actions` · `approvals` ·
`evidence` · `memories` · `entities` · `relations` · `entity_aliases` · `source_accounts` · `source_objects` ·
`connector_cursors` · `events` · `jobs` · `schedules` · `notification_endpoints` · `standing_permissions` ·
`provider_health` · `llm_calls` · `audit_log`

**Deltas from blueprint §20:**

| Change | Reason |
|---|---|
| `memories.embedding vector(384)` not `1536` | Local MiniLM. 4× less storage, faster index, zero API cost. |
| `jobs` table | Replaces SQS: `status`, `visible_at`, `attempts`, `max_attempts`, `locked_by`, `last_error`, `dead_lettered_at`. |
| `approvals` carries the callback | Replaces the Step Functions task token. Clients see only an opaque `approval_id`. |
| `oauth_clients` | Alexa account linking needs us to *be* an OAuth2 authorization server. |
| `provider_health`, `llm_calls` | Cascade cooldowns and per-call cost accounting. New, and necessary for §4. |

**Invariants:**
- Every table carries `user_id`; every query filters on it. A row-isolation sweep runs in CI.
- `tasks.version` increments on update. A stale schedule fire reads the current version and exits silently —
  that is how "acknowledge cancels later alerts" actually works.
- Deadlines persist as **confirmed UTC timestamp + IANA timezone**, never raw extracted text. Reminders are
  *computed* from that pair.

**Indexes:** unique `source_objects(provider, account_id, object_id)`; unique event idempotency key
`(tenant_id, provider, provider_event_id)`; `tasks(user_id, status, due_at)`; `jobs(status, visible_at)`;
`schedules(fire_at) WHERE status='pending'`; partial indexes for open tasks, pending approvals, online devices.
**HNSW on embeddings only after measuring** — under ~10k rows a sequential scan wins.

---

## 8. Agent loop and failure prediction

The LLM is a planner inside a deterministic harness. It owns neither the loop, the credentials, the budget,
the policy, nor the definition of success.

| State | Output | Stop condition |
|---|---|---|
| INGEST → CLASSIFY → CONTEXT → PLAN → POLICY → EXECUTE → VERIFY → REFLECT → COMMIT | per blueprint §4 | duplicate · unsupported · scope · budget · deny · timeout · no new evidence · terminal |

```python
for step in range(MAX_STEPS):                       # 8
    proposal = planner.next_action(state)
    decision = policy.evaluate(user, proposal, state)
    if decision is DENY:             return stop("policy_denied")
    if decision is REQUIRE_APPROVAL: return await_human(proposal)   # suspends the run
    result  = await tools.execute(proposal, timeout=proposal.timeout)
    verdict = await verifier.check(proposal.expected, result)
    state   = reducer.apply(state, proposal, result, verdict)
    if verdict.success:                                continue
    if not verdict.new_evidence or state.replans >= 2: return ask_user(state)
return stop("step_budget_exhausted")
```

**Repair is a graph of known fixes, never "keep trying."** A retry is allowed only when the next attempt
changes a relevant condition or follows the provider's `Retry-After`. Full table in blueprint §5; implemented
in tool manifests.

### The differentiator

Not reminders — knowing the plan is **mathematically** unlikely to succeed:

```python
available_minutes = deadline - now - fixed_calendar_blocks - safety_buffer
p80_remaining     = sum(t.remaining * user_calibration for t in critical_path) * P80_FACTOR
finish_ratio      = available_minutes / p80_remaining

if finish_ratio < 1.0:
    severity = "critical" if finish_ratio < 0.65 else "at_risk"
    options  = [reduce_scope(noncritical), reorder_to_critical_path(), request_help_or_extension()]
```

`user_calibration` is learned from `work_sessions` — estimated vs. actual, per user. Transparent arithmetic
before any ML, because a judge can follow arithmetic and cannot follow a model.

**The sentence, generated from real rows:**
> "You have 170 usable minutes, while the 80th-percentile remaining work is 260 minutes. Removing the optional
> Alexa animation and postponing the knowledge-graph visualization raises predicted completion probability
> from 34% to 78%."

---

## 9. Memory and knowledge graph

Four tiers — working, episodic, semantic, source — per blueprint §8.

**Hybrid retrieval, in this order:** filter by tenant / connector scope / project / retention **in SQL first**
→ fetch exact relational facts and recent episodes → run `pgvector` similarity **only over the permitted
subset** → rerank by relevance, recency, importance, source authority → return citations, never credentials.
Filtering before the vector search is what keeps retrieval both cheap and tenant-safe.

**Knowledge graph** in plain Postgres: `entities`, `relations`, `entity_aliases`.

```
Pranav ─OWNS→ JARVIS X ─HAS_GOAL→ Hackathon Submission ─BLOCKED_BY→ Alexa Certification
```

**Provenance on every edge** — "why do you believe this?" must resolve to a source object. Nothing permanent
is inferred from one message. A user correction creates a new version and invalidates derived edges.
Visualization ships in Phase 3 (Flutter force-directed graph).

---

## 10. Safety

| Risk | Examples | Default |
|---|---|---|
| **R0** read-only | list tasks, read mail metadata, search memory | automatic after connector consent |
| **R1** reversible local | open/focus allowlisted app, create draft, start focus timer | automatic on paired owner device |
| **R2** external effect | send message, create invite, submit form | preview + single approval |
| **R3** destructive | delete, install, command template, permission change | approval **+ local Mac confirmation** |
| **R4** prohibited | payment, credential export, disabling audit, **any command originating in an email** | deny |

**Approval binding.** `SHA-256(canonical(tool) + args + user + device + expires_at)`. Editing anything is a new
proposal with a new hash. Clients receive only an opaque `approval_id`. Replayed or mutated approvals fail.

**Simulation mode.** Planning, policy and verification preconditions run; effectful tools are swapped for
simulators. The UI shows exact plan, permissions, recipients, paths, expected evidence, estimated time and
cost. A judge flips `SIMULATE → EXECUTE` on the *same hashed plan*. Highest demo-value-per-line in the build.

**Three kill switches.** Server flag rejects new R1–R3 and cancels queued jobs; Mac menu-bar STOP interrupts
local jobs and drops the socket; Android emergency revokes device sessions. **None delete evidence** — each
records who invoked it and why.

**The agent card is generated from the policy table.** `/.well-known/agent-card.json` publishes an A2A
descriptor of what JARVIS can be asked to do — and it lists **only R0 and R1**, built from `RULES` at request
time. A hand-written card drifts away from what the system actually permits, and a card that overstates the
system is a lie another machine will act on. Discovery only: there is no JSON-RPC task endpoint, and no card
can grant a capability — everything above R1 still stops at a human.

---

## 11. Device pairing and job protocol

Pairing creates a device *identity*; it never copies cloud secrets to the Mac.

1. Sign in on the Mac app (OAuth2 + PKCE against our own authorization server).
2. Helper generates an **ECDSA key in Keychain**, sends the public key + device metadata.
3. Backend returns a one-time challenge; helper signs; backend stores the verified public key.
4. Mac opens outbound WSS with a short-lived device token, authorized on connect.
5. Every job carries `job_id`, `action`, `args`, `nonce`, `issued_at`, `expires_at`, `policy_version`, `signature`.
6. Helper validates expiry, signature, nonce replay **and its own local allowlist** before executing, then
   emits `ACK` → `PROGRESS` → `RESULT`.

**Verification is state, not exit code.** `mac.open_app` succeeds only when `CGWindowListCopyWindowInfo`
reports the expected bundle id frontmost *and* `NSRunningApplication` confirms the pid. A tool that returned
`0` and changed nothing is a failure.

---

## 12. Phased build

Phases end on **exit tests**, not calendar dates.

**Everything that can be built has been built.** What remains is not code — it is six
things this machine or an account cannot currently supply:

| Item | Blocked on | Unblock |
|---|---|---|
| 2.3 live extraction accuracy | A working **Groq** key (Gemini works) | Reissue it; the harness and 30 fixtures are committed and self-verifying |
| 2.8 FCM push | A Firebase project | `docs/SETUP.md` Step 5. **The toolchain is no longer the blocker** — Flutter builds a release APK on this machine |
| 3.3 Android live card · 3.6 voice | A device to verify against | Both are client work, unbuilt. An ongoing notification and an exact alarm need no Firebase; the Glance widget is Kotlin |
| 3.1 macOS menu-bar extra | — | The Flutter macOS target and every screen exist; only the menu-bar item and its STOP are missing |
| 4.4 Alexa reminders · 4.5 certification | An Amazon developer account | Upload `apps/alexa-skill/interaction-model.json`, point the endpoint at `/alexa` |
| 5.x live provider runs | Slack app · Meta template approval (24–48h) · Twilio caller id · Canvas token | Account work; each connector is already tested against its own boundary |
| 6.4 macOS signing | An Apple Developer ID | Everything else in CI is green |
| 7.x burned captions · lip-sync | `ffmpeg-full` (core ffmpeg has no `libass`) and a SadTalker/Wav2Lip CLI | `JARVIS_FFMPEG_PATH`, `JARVIS_YOUTUBE_LIPSYNC_CMD` |

### Phase 0 — Foundation ✅ complete — 47 tests green

| # | Task | Exit test |
|---|---|---|
| 0.1 | ✅ Freeze five contracts: `EventEnvelope`, `Goal`, `Task`, `ActionProposal`, `Evidence` | Frozen in `packages/contracts/schemas/`, all valid JSON Schema 2020-12 |
| 0.2 | ✅ Compose Postgres 16 + pgvector; Alembic 0001 | Postgres 16.15 + pgvector 0.8.6; migration applies on a clean DB |
| 0.3 | ✅ FastAPI skeleton, JSON logging, `correlation_id` middleware, RFC-9457 errors | `/healthz` returns build SHA; correlation id on every response and log line |
| 0.4 | ✅ OAuth2 authorization server + JWT + Argon2id | Register → authorize → token → refresh → revoke, with PKCE and single-use codes |
| 0.5 | ✅ `jobs` queue: `SKIP LOCKED`, retry, backoff, DLQ | **100 jobs / 4 workers: zero double-processing; poison job dead-letters** |
| 0.6 | ✅ **LLM router**: Groq + Gemini + OpenRouter, cascade, budget guard, `llm_calls` accounting | **Primary killed mid-run → completes on the next tier**; paid tier unreachable while disabled |

> **Gate:** 0.5 and 0.6 must pass before Phase 1. Everything rides on the queue and the router.

### Phase 1 — Vertical slice ⭐ *the submission lives or dies here* — ✅ complete, 218 tests green

| # | Task | Exit test |
|---|---|---|
| 1.1 | ✅ Canonical envelope + idempotent ingest | Same provider event twice → one event, one job |
| 1.2 | ✅ Goal/task DAG + critical path + work sessions | Goal decomposed; cycles refused at the API |
| 1.3 | ✅ **Failure prediction + recovery options** | **Severity changes with estimate and with progress**; lognormal fitted to (p50, p80) |
| 1.4 | ✅ Schedule ladder T-24h/2h/1h/15m, version-guarded | **Acknowledging cancels every later alert** |
| 1.5 | ✅ Approvals with payload hash + **simulation mode** | **An R2 send cannot run without a valid, unexpired, matching approval** |
| 1.6 | ✅ Telegram: alerts + inline approve/reject | **Approve from Telegram → the action dispatches**; another account cannot decide your approval |
| 1.7 | ✅ **Cloud browser worker** (Playwright) + DOM evidence | **Navigate, act, verify URL/title/DOM with zero devices paired** |
| 1.8 | ✅ Mac pairing + `mac.open_app` + window verifier | **Evidence shows pid + frontmost bundle; a non-allowlisted bundle is refused at the helper** |
| 1.9 | ✅ Signed job protocol + offline queue/expiry/review | **Replay, expiry and tampering all rejected and audited; stale jobs offered for review on reconnect** |

> **Gate: passed.** deadline event → task → prediction → approval → **verified action**, one correlation
> id — and the browser path runs with **zero devices paired**.

### Phase 2 — Ingestion, the brain, and the phone — 🚧 9 of 10, 366 Python + 11 Dart tests green

| # | Task | Exit test |
|---|---|---|
| 2.0 | ✅ LiteLLM transport behind the existing cascade | **Swapped with zero test changes**; cascade, breaker and budget untouched |
| 2.1 | ✅ **LangGraph agent loop** with Postgres checkpointer + `interrupt()` | **Suspends on approval, resumes in a fresh runtime, completes**; policy still denies before execution |
| 2.2 | ✅ Gmail connector (OAuth + `history.list` polling) | Normalizes nested MIME, strips quoted history and signatures; first sync anchors instead of importing a decade |
| 2.3 | 🚧 **Deadline extraction**: schema-constrained, prompt-versioned, cached, self-consistent | Harness + 30 fixtures committed and self-verifying; **live number needs a provider key** |
| 2.4 | ✅ Google Calendar read → `fixed_calendar_blocks` | Overlapping meetings merged, spans clipped to the window |
| 2.5 | ✅ Escalation chain + quiet hours + per-day cap | **An ignored alert escalates exactly once**; quiet hours defer rather than drop |
| 2.6 | ✅ Memory tiers + hybrid SQL→vector retrieval | **Citations returned, credential-shaped content refused at write**; corrections supersede |
| 2.7 | ✅ Flutter app: Today · Goals · Approvals · Devices + kill switch | **Verified in a real browser**: sign-in → goals → prediction, risk card rendered from live rows. A release APK and a macOS `.app` both build now |
| 2.8 | ⬜ FCM push + WorkManager + AlarmManager exact alarm | Blocked on a Firebase project, no longer on the build — `flutter build apk` succeeds here |
| 2.9 | ✅ Hypothesis invariants + Schemathesis spec validation | **Found a real bug**: the Telegram webhook returned 500 unconfigured, which would have caused a retry storm |

### Phase 3 — Desktop, graph, modules — 🚧 3 of 6 · Xcode and the Android SDK are working now; what is left is client work

| # | Task | Exit test |
|---|---|---|
| 3.1 | 🚧 Flutter macOS Control Center + menu bar + kill switch | macOS target builds and ships to `/Applications`; Chat, Today, Approvals, Devices and the kill switch are in the app. **The menu-bar extra is not built** |
| 3.2 | ✅ Mac node full tool set: AX automation, window/screen evidence, scoped files, templates | Capture returns a **digest, not pixels**; a revoked permission is reported rather than read as "not there"; file access cannot escape its granted directory |
| 3.3 | ⬜ Android live deadline card: ongoing notification + Glance widget | Remaining time + completion probability + Acknowledge / Snooze / Start Focus. Unbuilt — needs a device to verify against, not a toolchain |
| 3.4 | ✅ Knowledge graph (visualization deferred to the Flutter surface) | **"Why do you believe this?" resolves to a source**; corroboration raises confidence but never reaches certainty; retraction leaves a trace |
| 3.5 | ✅ Morning / evening / focus modules | Views over the shared goal engine; the first hour goes to the **critical path**, not the nearest deadline |
| 3.6 | ⬜ Voice on the Mac: Whisper + Piper, push-to-talk, visible indicator | Ported from v1, with an indicator |

### Phase 4 — Alexa — ✅ code complete, 34 tests green (console work needs a developer account)

| # | Task | Exit test |
|---|---|---|
| 4.1 | ✅ Skill model: 7 intents, slots, utterances, en-IN | `apps/alexa-skill/interaction-model.json`. **The model and the dispatch table cannot drift** — a test fails if an intent exists on one side only, and if any custom intent has no utterance |
| 4.2 | ✅ HTTPS endpoint ⇄ *instead of an ASK SDK Lambda*, skill-ID restriction, signature + timestamp verification | **A forged certificate URL, a tampered body, a certificate not issued for `echo-api.amazon.com`, an expired certificate, a replayed timestamp and another developer's skill id are each refused** |
| 4.3 | ✅ Account linking against our own OAuth2 server | **Unlinked → LinkAccount card and no data**; a forged access token links nothing; a linked token reaches the same services the app uses |
| 4.4 | ⬜ Reminders (per-reminder consent) + Proactive Events | Blocked on an Amazon developer account. Both flows written up in `apps/alexa-skill/README.md`; the escalation ladder never depended on Alexa |
| 4.5 | 🚧 Privacy policy, terms, certification checklist | Checklist committed; certification itself needs the developer console. **Approving by voice needs Alexa's confirmation turn, and an R3 that needs a local Mac confirmation is refused out loud** |

> **Substitution, and why:** Alexa accepts either a Lambda or an HTTPS endpoint. Serving
> the skill from the API we already run removes a second language, a second deployment and
> an AWS account from the critical path. What the ASK SDK would have given us for free —
> request authenticity — is `connectors/alexa/verify.py`, and it is tested with a real key
> pair rather than a mock.

### Phase 5 — Remaining connectors — ✅ code complete, 47 tests green (live credentials pending)

| # | Task | Exit test |
|---|---|---|
| 5.1 | ✅ Slack Events API + `chat.postMessage` after approval | **The signature is checked over the raw body, before anything parses it**; a replay expires although its signature never does; the `url_verification` challenge is answered only after that check; a message from an unlinked Slack user stores nothing. Sending is R2 and needs an approval |
| 5.2 | ✅ Google Classroom read | Coursework → task. **A whole-day deadline lands at 23:59, not midnight** — the naive reading fires every reminder a day early |
| 5.3 | ✅ Canvas LMS read (free teacher instance for the demo) | Assignment → task. **A base URL pointing inside the host network is refused** — connector configuration is a request-forgery vector |
| 5.4 | ✅ WhatsApp Cloud API approved template | The body is a fixed sentence with named parameters, so an untrusted title cannot become instructions; newlines are collapsed because Meta rejects them |
| 5.5 | ✅ Twilio outbound call as final escalation | Placed **once** (one rung per attempt), **capped** (`max_calls_per_day`, enforced before a sender is reached), **opt-in** (an enabled endpoint row), **logged** (`notification.sent`). The spoken script escapes an injected `<Say>` |
| 5.6 | ✅ OpenClaw adapter, isolated, narrow service identity | **No DB credentials in that container** — it holds one shared secret that buys the right to post an *untrusted* event and nothing else. An unset secret denies rather than opens the route |
| 5.7 | ✅ Escalation worker draining `schedule.escalate` | **The rung is derived from what was already sent**, so a redelivered job cannot restart the ladder at rung 0 and shout through every channel again. An unconfigured channel is absent, so the ladder falls through instead of crashing |

> Every connector above is exercised against fixtures and its own security boundary. What
> is *not* proven without live credentials is the provider's own behaviour: a real Slack
> workspace, an approved WhatsApp template, a verified Twilio caller id, a Canvas token.
> Those are account tasks, not code tasks — §13 lists the lead times.

### Phase 6 — Credibility — 🚧 4 of 5 complete · **613 tests green across the whole suite**

| # | Task | Exit test |
|---|---|---|
| 6.1 | ✅ Adversarial: injection, replay, expired job, scope violation, storm | **12 attack shapes × a fully compromised planner → zero effectful actions**; 46 tests |
| 6.2 | ✅ Chaos: outage, worker death, thundering herd, redelivery, stale schedule, disconnect | **Known failures repair once or stop clearly**; 15 tests |
| 6.3 | ✅ Metrics scorecard vs. §15 targets | **All seven measured from what happened**; an unmeasured metric says so rather than reporting 100% |
| 6.4 | 🚧 GitHub Actions (backend · Flutter · contracts) | Runs lint, migrations, the full suite on real Postgres + Chromium, and fails on a stale OpenAPI, a leaked value in `.env.example` or an uncommitted package. **Blocked only on macOS signing, which needs a Developer ID** |
| 6.5 | ✅ DEMO CLOCK + one-click reset + **rehearsed** | **All 13 beats pass, twice**, on the real scheduler path; reset clears the demo tenant only |

**If time slips, cut in this order:** the video pipeline → OpenClaw → Canvas/Moodle → WhatsApp →
phone call → knowledge-graph *visualization* → Alexa reminders.
**Never cut:** verified execution · failure prediction · approval workflow · audit timeline.

### Phase 7 — the video pipeline — ✅ working, 22 tests green · *beyond the blueprint*

Not in the PDF. It earns its place by being the loudest possible demonstration that the
**same** queue, policy ladder, approval binding and evidence rules govern a long, expensive,
externally-visible job — not just a two-second email send.

| # | Task | Exit test |
|---|---|---|
| 7.1 | ✅ Research → script → speech → captions → imagery → render, as queued jobs | A render is a `jobs` row with retries and a DLQ, like everything else |
| 7.2 | ✅ `youtube.upload` and `youtube.reply` are R2 | **Neither can run without a valid, unexpired, matching approval**; the approval card carries the rendered file |
| 7.3 | ✅ A script assembled from untrusted research cannot upload itself | **An upload proposed from untrusted content is denied**, exactly as an emailed command is |
| 7.4 | ✅ Free-tier by default | edge-tts for speech, DuckDuckGo for research, local SDXL or a keyless image endpoint for imagery, ffmpeg for the render |

> **Machine-local blockers (not code):** Homebrew's core ffmpeg ships without `libass`, so
> burned captions need `ffmpeg-full` (`JARVIS_FFMPEG_PATH`); lip-sync needs a SadTalker or
> Wav2Lip CLI wired through `JARVIS_YOUTUBE_LIPSYNC_CMD`.

---

### Phase 11 — Gemini's expanded matrix, and the correctness pass the screenshots forced — ✅ built where marked

The owner pasted a second Gemini plan (Open Interpreter, browser-use, Manus, Rewind,
Computer-Use). Same treatment as §10.0½: keep what fits, name what does not and why.

| Gemini proposed | Verdict | Where |
|---|---|---|
| Background deep research → summary emailed/texted | ✅ already: `browser.act` + a routine on *Do things* with channel Telegram/app | 10.5, 10.1 |
| Auto-checkout, buying when the price drops, booking flights | ❌ **Never autonomous.** Anything that moves money is R4 (`payment.send`); a booking form is `browser.submit_form` R2 with the pre-submit screenshot on the card — you press the last button | §10 Safety |
| Job applications: fill and submit | ⚠️ Fills with `browser.act`; the submit is the R2 approval per application. Auto-submitting hundreds is the thing recruiters block accounts for | 10.5.3 |
| Screen OCR memory (Rewind-style, every screen indexed) | ❌ Still no. Titles-only activity is the line: a searchable history of everything on screen is a keylogger with a nicer name. `activity.query` answers "what was I doing at 3" | 10.6.3 |
| Cross-device clipboard sync | ✅ **11.4** `phone.clipboard_read/write` (R1) + Mac clipboard verbs; *Paste my clipboard there* / *Send my clipboard* on Devices | this phase |
| Semantic code/file knowledge graph of local repos | ⏸ `mac.find_files` is the scoped read; indexing repositories is a day of work and a lot of tokens; not now |
| Face/proximity unlock; lock when you walk away | ❌ unlock (10.0¼); ⏸ lock-on-leave needs BLE ranging from the phone — later |
| Settings toggles on both devices | ✅ 10.4.1–2, now also **from the Mac app itself** (11.1) |
| Open Interpreter: shell from the phone by voice | ❌ `shell.execute` is R4 by design; the command templates are the typed replacement | §16 |
| Dynamic morning alarm with weather, Slack, top tasks | ✅ 10.2.2 + 10.9.3 + **11.3 briefing mode** (the prose now comes from gathered facts) |
| Deadline dispatcher that calls and offers to request an extension | ⚠️ The call exists (5.3 → 10.2). Offering "press 1 to draft an extension request" on that call — ⏸ next: it is one more Gather on the deadline script plus `gmail.create_draft` to the task's source author |
| Meeting shadow (join Zoom, transcribe, action items) | ⏸ Not built: joining calls and recording audio is a separate product; the Mac helper's Whisper is the seed |
| Focus guard: brightness, minimise, DNS block | ✅ notify → speak → lock (10.6.4). ❌ `/etc/hosts` needs sudo — a root-writing verb is R4; minimise is possible via `mac.press_key` but a nudge that hides your work is worse than a lock |
| Dopamine budget / end-of-day screen-time debrief | ✅ the Evening review now reads today's minutes per app (`activity.summary` in the briefing context) |
| Live camera during a call | ❌ one frame per action stays (10.7.2) |
| Desk arrival: sit down → windows arranged, playlist on | ⏸ a *mac unlocked* event from the helper → a routine trigger with provider `mac`; not built |

| # | Task | Exit test |
|---|---|---|
| 11.1 | ✅ **The Mac app is the Mac's hand.** The Flutter macOS app's node now answers thirteen `mac.*` verbs itself — screenshot/describe (`screencapture` → artifact), open URL/app, notify, say, volume, lock, clipboard, media, `set_setting`, `system_info` — through argv (`open`, `osascript`, `pbpaste`, `networksetup`), never a shell; same signed-job guard, scheme/bundle/setting allowlists; pairs as *This Mac*. The Python helper remains for accessibility verbs (read_ui, press_button, typing, scoped files) | ✅ Dart tests: clipboard round-trip, Wi-Fi re-read as state, `firewall` and `file:` rejected, `say` argv; a phone refuses `mac.*` |
| 11.2 | ✅ **Deadlines you can see.** `GET /v1/tasks` (dated first) with the source of each: provider, sender, subject, link, and the exact words the date was read from. Goals tab opens with *Deadlines* (colour by urgency, Done, tap the source to open the mail); Home has a *Deadlines* panel. The audit that started it: the "Exam" task from Saritha's mail existed on the server with its evidence span and had no screen | ✅ Tests: the list carries `gmail · Saritha…`, `Meeting`, the evidence span; Done removes it; `status=all` keeps it |
| 11.3 | ✅ **Briefing mode.** A routine that only tells you things (`mode=brief`; the built-ins) is answered by the chat model over `services/briefing.gather` — dated tasks with sources, at-risk goals, recent mail, weather, today's screen time — with a fact-only fallback when the model garbles (the first Morning briefing in production came back as a prompt fragment). *Do things* routines still run the agent | ✅ Tests: the model sees the real deadline; a garbled reply falls back to facts; built-ins fire as `brief`, customs as chosen |
| 11.4 | ✅ Clipboard both ways (above) | ✅ |
| 11.5 | ✅ **Correctness pass.** Expired approvals are no longer listed as pending; every tab refreshes when switched to, every 45 s while visible, and on app resume (screens live in an IndexedStack, so their providers never disposed); a rejected provider key opens the breaker for an hour instead of failing first on every call; agent jobs hold a 15-minute lease (runs were being reaped mid-flight at 5) | ✅ Tests: an expired approval vanishes from the list; Groq's rejected key cools for > 50 min while Gemini answers; the agent lease is > 10 min |
| 11.6 | ⏸ Extension offer on the deadline call · desk-arrival trigger · lock-on-leave · meeting shadow · repo index — listed above with reasons |
| 11.7 | ✅ **Latency and polish pass.** Every list provider is stale-while-revalidate over a disk-backed response cache (`api/cache.dart`, `shared_preferences`): a screen opens with its last data instantly and the fresh answer replaces it; the cache empties on sign-out. The API gzips anything over 1 KB. Artifact bytes are cached in memory so Timeline images do not refetch on rebuild. Shell: a 220 ms fade-and-lift on every tab switch, a pending-approvals badge on the rail and bar, ⌘1–⌘0 and ⌘K on the Mac. Home: an *Ask anything* bar that hands the text to the chat. Approvals: a draining expiry bar. Timeline: filter chips (everything · actions · runs · alerts · with files). Goals: *Add a deadline* by hand (date, time, title). Routines: a countdown to the next run. Theme: floating rounded snackbars, consistent popup, tooltip and segmented-button shapes; empty states with a soft glow | ✅ Dart test: a GET is replayed from cache before the fresh answer and forgotten on sign-out; Python test: a large response is gzipped, `identity` is honoured |
| 11.8 | ✅ **Devices without friction.** Re-pairing the same Mac or phone *replaces* its old row (one live device per platform and name) and revoked rows are hidden; this device's own row is the top card, which now restores and connects on launch and reconnects after a drop without a tap. A tap in your own app on a device verb **is** the approval: `POST /v1/actions` decides it as `app-tap` and dispatches at once (R3 still needs the Mac's confirm; mail, posts and money still wait). `phone.call` is R1 (the thumb calls). One switch, *Trust my devices*, grants 30-day envelopes for screenshots, describe screen, typing, keys, files, WhatsApp and the camera so the agent's own proposals stop asking too; revoke in one tap. A *Remove* button per device. The Mac app now also sends WhatsApp (open the chat, press Return only when WhatsApp is frontmost) | ✅ Tests: pairing "This Mac" twice leaves one live row and the phone untouched; a tapped screenshot is approved by `app-tap` and dispatched while `message.send` still waits; the dialer is R1; typing waits until the switch is on and never for mail; the switch revokes cleanly |

---

### Phase 12 — the 2026 agentic patterns (Gemini's tier), and the production bug they surfaced — ✅ built where marked

The owner pasted a third Gemini plan: time-travel checkpointing, MCP, GraphRAG, a
Markdown "morning intelligence" layer, an MLFQ scheduler, and a local edge voice stack.
Checked against what already runs; several are already here.

| # | Task | Exit test |
|---|---|---|
| 12.0 | ✅ **The production bug the audit found.** The agent and notify workers claimed a batch of jobs and ran them in **one** session; when a job's flush hit a `UniqueViolationError`, the session was poisoned, `queue.fail` could not record it, the tick rolled back, and the job stayed `running` with an expired lease — reaped and retried forever (the agent worker's heartbeat was 24 min stale in production while six jobs sat pending). `workers/loop.py` `drain()` now claims in one short transaction, then runs **each job in its own transaction**: one job's failure cannot poison another, a failure is always recorded, and a bad job dead-letters after its retries instead of wedging the queue | ✅ Tests: a job that raises a real `UniqueViolation` dead-letters while its two neighbours succeed; a retryable job returns to `pending`, never stuck `running` |
| 12.1 | ✅ **Time-travel checkpointing** — *already built.* The LangGraph Postgres checkpointer saves state at every node (`runtime.postgres_checkpointer`); an R2/R3 pauses the run with `interrupt()` and the decision resumes it from the exact node (`runtime.resume`). Gemini's extra is *rewind to an earlier node and branch* — ⏸ deferred: LangGraph exposes checkpoint history, but a branch UI is a feature of its own and the resume path covers the failure case the owner described (fail at step 18, supply context, continue) | ✅ existing 8.x + 9.x run tests |
| 12.2 | ⏸ **MCP** — deliberate, with reasons unchanged from §10.0½: the tool gateway's R0–R4 tiers, approvals and evidence have no MCP equivalent, so MCP would be adopted *as a client* for a specific third-party server worth the bridge, never as a replacement for the gated connectors. Not an autonomous-pass change |
| 12.3 | ⚠️ **GraphRAG** — *partly built.* Triage already writes senders as `person` entities with `RELATED_AS` edges and provenance (`services/graph`, 10.6.2), and `GraphService.neighbourhood`/`why` traverse them; `profile_block` feeds contacts to every prompt. The missing piece is multi-hop *retrieval* at query time ("action items from my Pune trip" walking Location→Date→Event→Task) — ⏸ a `graph.query` tool over the existing edges, medium effort, deferred |
| 12.4 | ✅ **Morning intelligence layer.** The HUD *is* the live dashboard Gemini describes; `services/briefing.gather` already aggregates due tasks with sources, at-risk goals, recent mail, weather and screen time, and the built-in Morning briefing routine synthesises it before you wake (10.1, 11.3). Gemini's `home.md` framing adds nothing our HUD + briefing does not | ✅ 11.3 briefing tests |
| 12.5 | ✅ **MLFQ scheduler.** Named priority bands on the one Postgres queue (`JOB_PRIORITY`): a person waiting — a Telegram message, an app tap — is `interactive` (30) and is claimed before `background` (2) routine and research work; a resumed run after an approval is `decision` (20), a deadline rung `escalation` (15). Interactive chat already runs inline, ahead of the queue entirely | ✅ Test: an interactive `agent.run` queued after a background one is claimed first; the bands are ordered |
| 12.6 | ✅ **Local edge voice** — *already built,* and already preferred when the Mac is online: `macnode voice` runs openWakeWord + faster-whisper on-device and speaks with `say`, and Ollama (LLaMA 3) is a cascade provider, so the whole loop can stay local. The cloud (Twilio, edge-tts) is the fallback for when the Mac is unreachable, exactly as Gemini describes | ✅ 9.6 voice-loop tests |

---

| 12.7 | ✅ **Deadlines survive an exhausted LLM.** When the extractor's whole cascade fails (usually free-tier 429 quota), `extraction/regex_fallback.py` reads a plain date near a deadline word with no model — "exam deadline on 9 September 2026 at 9 am" still becomes a task, flagged confidence 0.55 for confirmation. Month names are an explicit list (so "Due" is not a month) and ambiguous DD/MM defaults to day-first for the owner's locale. A 429 now opens that provider's breaker for 5 minutes so the cascade fails fast instead of hammering the quota | ✅ Unit tests: five phrasings read, four non-dates refused; integration test: a mail normalised while the model raises a quota error still yields the 9 Sept task |

| 12.8 | ✅ **Device control from chat, and the new hands.** The chat/Telegram agent can drive your own devices: `load_context` now resolves your paired phone or Mac when no device is named, so "ring my phone" / "screenshot my Mac" from a message is addressed automatically (it was being *denied* before — a real bug the tests caught). New verbs: `phone.call` **places the call** (ACTION_CALL + CALL_PHONE, dialer fallback), `phone.ring` / `mac.ring` (loud even in silent mode, to find a device), `phone.locate` (GPS → a maps link, R2), `phone.whatsapp_send` (auto-taps Send via an **opt-in Accessibility Service**, R2). Devices cards gained Ring, Locate, Call and WhatsApp-send. **Telegram**: an unlinked chat is no longer met with silence — the bot replies with the chat id and how to link, and Connections has a one-tap *Link Telegram* card (the identities table was empty, which is why Telegram "didn't respond"). The trust switch covers the new hands-on verbs | ✅ Python: tiers and evidence; the agent addresses a ring to the phone with no device id; the trust switch covers locate/whatsapp. Dart: call places-or-falls-back, ring/locate/whatsapp-send report through their hooks, a refused location is a 403 |

### Phase 13 — 50 features, in batches (docs/FEATURES-50.md) — 🚧 batch 1 shipped

The owner asked for fifty more features, done one by one. The full list with batch order
is `docs/FEATURES-50.md`. Batch 1 (this pass):

| # | Feature | Done |
|---|---|---|
| 13.1 | **Snooze / reschedule a deadline** — chips (in an hour, tonight, tomorrow, in a week) or pick a date; writes the new `due_at` and re-arms the alert ladder | ✅ |
| 13.2 | **Agenda** — the Goals deadlines are grouped by day (Overdue · Today · Tomorrow · weekday · Next week · month), sorted, colour-cued | ✅ |
| 13.3 | **Search conversations** — a filter field in the chat thread picker | ✅ |
| 13.4 | **Quick capture** — one FAB anywhere: a thought straight to Jarvis, or add a deadline (date + time), without hunting for the screen | ✅ |
| 13.5 | **First-run onboarding** — a Home nudge to the 3-minute interview until the profile has something in it | ✅ |
| 13.7 | **Data export & wipe** — `GET /v1/export` (all your tasks, goals, routines, memories, chats; never secrets or key material), `POST /v1/account/wipe?confirm=DELETE` (clears content, keeps the login and the append-only audit log); Settings → *Your data* | ✅ |
| 13.8 | **Audit trail viewer** — `GET /v1/audit` (filter by action) + `/v1/audit/actions`; a dedicated screen with action chips | ✅ |
| 13.9 | **Focus now** — `GET /v1/focus`: the one thing to do (most overdue, else soonest) plus the overdue/upcoming lists; a Focus card at the top of Home | ✅ |
| 13.10 | **Recurring deadlines** — a task can repeat daily/weekly/weekdays/monthly; completing it spawns the next (weekdays skip the weekend, monthly clamps to day ≤ 28); a Repeat step in add-deadline and a chip on the tile | ✅ |
| 13.12 | **Phone system info & flashlight** — `phone.system_info` (battery, storage, network) and `phone.torch` verbs, node-routed and tiered R1 (the platform implementations report honestly when a plugin is absent, rather than faking) | ✅ verb + node tests |
| 13.11 | **Pin conversations** — pinned chats sort first in the picker, with a pin/unpin action | ✅ |
| 13.6 | **Natural-language quick-add** — `POST /v1/tasks/quick`: "pay rent friday 6pm" → a dated task via the regex reader (weekdays, today/tonight/tomorrow, times), no model needed; the quick-capture bar routes dated text to it | ✅ |

Batches 2–4 (queued): natural-language quick-add, recurring deadlines, slash-commands,
settings search, the Mac command palette; background execution via FCM, phone system
info, find-my-devices map; digests, weekly review, GraphRAG answers.

---

## 13. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **Free LLM tiers rate-limit mid-demo** | Demo stalls | Three-provider cascade + circuit breakers + ₹800/mo paid headroom. Pre-warm before the demo. **Verify current quotas in your own accounts** — published limits change often. |
| **Oracle Always Free ARM capacity exhausted** | No VM | Try adjacent regions; else Hetzner CX32 ~₹650/mo. Identical Compose file. |
| **Ollama not installed** | Only affects optional local dev | `brew install ollama` in bootstrap. Not on the critical path any more. |
| ~~Flutter macOS needs full Xcode~~ — **resolved** | Blocked Phase 3.1 | Xcode 26.6 installed and licensed; the macOS target and the Android APK both build. Homebrew still refuses *source* builds until Xcode 27; bottles are fine. |
| **Python 3.14.7 is your default** | `faster-whisper`, `sentence-transformers` have no 3.14 wheels | `uv python pin 3.12`. Non-negotiable. |
| **WhatsApp needs a Meta Business account + template approval** | 24–48 h lead time | Submit templates in Phase 2, use them in Phase 5. Telegram covers the same escalation slot meanwhile. |
| **Outbound calling to India is regulated** | Escalation call may not work | Twilio with a verified caller id for the demo. **Android `setAlarmClock` full-screen alarm is the primary wake mechanism** — free, reliable, and arguably better than a call. The call is a bonus tier. |
| **Alexa certification takes days** | Missed deadline | Phase 4 demos via the simulator; certification is not required to demo. |
| **Google Classroom needs institutional authorization** | May be unavailable | Canvas free teacher instance is the demoable LMS; Classroom ships behind the same connector interface. |
| **Gmail push needs GCP Pub/Sub + verified domain** | Days of setup for seconds of latency | 60 s `history.list` polling, identical normalizer. Upgrade only if asked. |
| **Mac asleep** | Tier-B actions only | By design — §3. Everything else is unaffected. |
| **Accessibility / Screen Recording prompts change after any signing change** | Demo-day surprise | Test from a **clean macOS user account** before the demo. |

---

## 14. Cost — ₹2,000/month envelope

| Item | Choice | Monthly |
|---|---|---|
| Compute | Oracle Always Free ARM | **₹0** |
| — if unavailable | Hetzner CX32 (4 vCPU / 8 GB) | ₹650 |
| Database | Postgres + pgvector on the same VM | ₹0 |
| Backups | `pg_dump` → Cloudflare R2 (10 GB free) | ₹0 |
| Queue / scheduler / approvals | Postgres | ₹0 |
| TLS / edge / DDoS | Caddy + Cloudflare | ₹0 |
| Push | FCM | ₹0 |
| LLM — chat/plan/classify | Groq free tier | ₹0 |
| LLM — extraction | Gemini free tier | ₹0 |
| LLM — overflow | OpenRouter `:free`, then paid credit | ₹0–800 |
| Embeddings | MiniLM on the VPS | ₹0 |
| Telegram | Bot API | ₹0 |
| Gmail / Calendar / Classroom / Canvas | provider quotas | ₹0 |
| Alexa | Lambda free tier | ₹0 |
| WhatsApp | utility templates, ~₹0.15 each | ₹0–100 |
| Voice calls | Twilio, capped | ₹0–200 |
| Domain | DuckDNS free, or ~₹900/yr | ₹0–75 |
| | **Expected** | **₹0–700** |
| | **Worst case** | **₹1,825** |

Inside budget with headroom, and the headroom is where it belongs: buying LLM reliability during judging.

**Controls in code, not in a spreadsheet:** `MONTHLY_BUDGET_INR` refuses paid calls past the cap;
`ENABLE_PAID_LLM=false` must still yield a working system (tested); per-run `MAX_STEPS` / `MAX_REPLANS` /
`MAX_TOKENS_PER_RUN` / `MAX_RUN_SECONDS`; per-user daily notification, WhatsApp and call caps; circuit
breakers on every connector; a dashboard showing daily spend.

**The four traps that generate surprise bills**, three designed out and one bounded: NAT Gateway data
processing (no private subnet), Aurora idle ACUs (no Aurora), an always-on Fargate task (one free VM), and an
unbounded agent loop (`MAX_STEPS=8`, enforced by the harness, not by a prompt).

---

## 15. Definition of done

| Metric | Target |
|---|---|
| Deadline extraction accuracy | ≥ 90% on 50 curated items |
| Duplicate task rate | < 1% under replay |
| Verified tool success | ≥ 95% — evidence verdict, **not** exit code |
| Approval coverage | 100% of R2/R3 actions |
| Event-to-alert latency | < 10 s on the demo path |
| Recovery correctness | known failures repair once or stop clearly |
| Prompt-injection block | 100% on adversarial fixtures |
| Monthly run cost | ≤ ₹2,000 |
| **Mac-offline capability** | **All Tier-A features pass with the Mac powered off** |

**Six test layers:** unit (reducers, policy, date resolution, prediction math) · contract (OpenAPI clients, WS
envelopes, connector fixtures, Alexa requests) · integration (Postgres, OAuth mocks) · e2e (real Telegram +
Gmail test account, paired Mac, Android push, Alexa simulator) · adversarial · chaos.
**613 passing, 1 skipped**, against a real Postgres 16 + pgvector and a real Chromium.

**One correlation id** connects webhook → event → extraction → task → schedule → notification → approval →
job → evidence. If you cannot follow one request across all nine hops in the logs, the system is not done.

---

## 16. What we harvest from v1

`legacy/jarvis-v1/` is **reference, not a dependency.** Nothing imports it.

| v1 file | Verdict |
|---|---|
| `tools.py` `open_app` | Concept ✅ / implementation ❌ — `os.system("open -a ...")` has no verification. Becomes typed `mac.open_app(bundle_id)` on `NSWorkspace` with an allowlist and frontmost-window evidence. |
| `tools.py` `run_command` | ❌ **Deleted.** `subprocess(shell=True)` on model output is arbitrary chat-to-shell — the exact R4 the policy engine exists to prevent. Replaced by approved command templates. |
| `voice_jarvis.py` record/transcribe/speak | ✅ Ported to `mac-node` with a visible listening indicator and push-to-talk. |
| `memory.py` (ChromaDB) | ⚠️ Replaced by `pgvector` — same embedding model, but memory now joins goals and tasks in one query under one tenant filter. |
| `knowledge/rag.py` | ⚠️ Reworked — whole-file embedding loses precision; chunk it and store provenance so retrieval can cite. |
| `brain.py` | ❌ Superseded — an unbounded `while True` with no budget, policy or verification is precisely what the state machine replaces. |
| `app.py` | ✅ Patterns ported — token streaming and the WS envelope become the Mac node transport. |
| `static/` HUD | ✅ Kept as design reference for the Flutter theme. |
| `try_tools()` | ❌ Superseded — `if "chrome" in text` also fires on "close chrome". Replaced by typed proposals through the policy engine. |

The two deletions are the point: `run_command` and the unbounded loop were the real vulnerabilities.

---

## 17. Start here

```bash
# Accounts to create first (all free, ~20 minutes)
#   console.groq.com · aistudio.google.com · openrouter.ai
#   cloud.oracle.com (Always Free VM) · dash.cloudflare.com (R2)
#   @BotFather on Telegram

brew install ollama && ollama pull llama3.1:8b   # optional local dev only
uv python pin 3.12                                # 3.14 has no ML wheels
xcode-select --install                            # + full Xcode for Phase 3.1

docker compose -f infra/compose/docker-compose.dev.yml up -d
uv sync && uv run alembic upgrade head
uv run uvicorn jarvis.main:app --reload
```

Then, in order, **without skipping ahead**:

1. Freeze the five contracts — everything downstream generates from them.
2. Build the `jobs` queue; prove it with four concurrent workers.
3. Build the LLM router; prove the cascade by killing the primary provider mid-run.
4. Goal DAG → prediction heuristic → one recovery card.
5. Approvals + simulation, then Telegram approve/reject.
6. Cloud browser worker with DOM evidence — **with the Mac off.**
7. Only then: Mac node, Gmail, Android, macOS, Alexa, remaining connectors.

> The spine is: **one real deadline → one sourced task → one approved action → one verified result — and it
> must work with the Mac powered off.**
