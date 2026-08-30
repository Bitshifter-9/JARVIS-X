# JARVIS X — Implementation Plan

**Owner:** Pranav Bandaram · **Repo:** `Bitshifter-9/JARVIS-X` · **Supersedes:** `Bitshifter-9/Jarvis-` (preserved in [`legacy/jarvis-v1/`](legacy/jarvis-v1/))

Build plan for the *JARVIS X Advanced Architecture and Implementation Blueprint*, under four constraints:

| Constraint | Consequence |
|---|---|
| **Every feature in the PDF ships** | Full coverage matrix in §2. Nothing is silently dropped; where the PDF itself says *defer* or *do not build*, we honour that and say so. |
| **Cloud-first — must work with the Mac offline** | The Mac is an **optional execution node**, never a dependency. Inference, ingestion, prediction, scheduling, escalation and approval all run in the cloud. §3. |
| **Budget ≈ ₹2,000/month (~$24)** | Free tiers by default, paid fallback wired up behind a hard cap. Realistic spend ₹0–700/mo. §14. |
| **No iOS** | Android is the only phone. Live Activity → Android ongoing notification + widget. Contracts stay iOS-ready. |

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
| 1 | Alexa custom skill with account linking | ✅ Full | `apps/alexa-skill` — Phase 4 |
| 1 | OpenClaw as isolated channel adapter | ✅ Full | `POST /internal/connectors/openclaw/events` — Phase 5 |
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
| 15 | Alexa: 7 intents + account linking | ✅ Full | Phase 4 |
| 16 | ASK SDK Lambda, reminders, proactive events, certification | ✅ Full | Phase 4 |
| 17 | Gmail | ✅ Full | Phase 2 |
| 17 | Google Calendar | ✅ Full | Phase 2 |
| 17 | Slack | ✅ Full | Phase 5 |
| 17 | Google Classroom | ✅ Full | Phase 5 |
| 17 | Canvas / Moodle LMS | ✅ Full — Canvas free teacher instance for demo | Phase 5 |
| 17 | Telegram | ✅ Full | Phase 1 |
| 17 | WhatsApp Cloud API templates | ✅ Full | Phase 5 — see §13 risk |
| 17 | Outbound phone call | ✅ Full ⇄ Twilio instead of Amazon Connect | Phase 5 — see §13 risk |
| 18 | OpenClaw adapter, isolated, no DB credentials | ✅ Full | Phase 5 |
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
│   │   ├── connectors/   gmail · calendar · slack · classroom · canvas ·
│   │   │                 telegram · whatsapp · twilio · openclaw
│   │   └── workers/      agent · connector · scheduler · browser
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

### Phase 2 — Ingestion, the brain, and the phone — 🚧 3 of 10, 283 tests green

| # | Task | Exit test |
|---|---|---|
| 2.0 | ✅ LiteLLM transport behind the existing cascade | **Swapped with zero test changes**; cascade, breaker and budget untouched |
| 2.1 | ✅ **LangGraph agent loop** with Postgres checkpointer + `interrupt()` | **Suspends on approval, resumes in a fresh runtime, completes**; policy still denies before execution |
| 2.2 | Gmail connector (OAuth + `history.list` polling) | A real email deadline creates exactly one sourced task |
| 2.3 | 🚧 **Deadline extraction**: schema-constrained, prompt-versioned, cached, self-consistent | Harness + 30 fixtures committed and self-verifying; **live number needs a provider key** |
| 2.4 | Google Calendar read → `fixed_calendar_blocks` | Prediction changes when a meeting is added |
| 2.5 | Escalation chain + quiet hours + per-day cap | An ignored alert escalates exactly **once** |
| 2.6 | Memory tiers + hybrid SQL→vector retrieval | Retrieval returns citations, never credentials |
| 2.7 | Flutter Android: Today · Goals · Chat · Approvals · Timeline · Devices · Connectors | Phone approves; phone shows the evidence |
| 2.8 | FCM push + WorkManager + AlarmManager exact alarm (opt-in) | Alarm-clock wake works with the screen locked |
| 2.9 | 🚧 Hypothesis invariants ✅ + Schemathesis contract fuzzing | Generated inputs find no policy bypass; API fuzzing still to wire |

### Phase 3 — Desktop, graph, modules

| # | Task | Exit test |
|---|---|---|
| 3.1 | Flutter macOS Control Center + menu bar + kill switch | Chat, deadline board, timeline, approval cards, STOP |
| 3.2 | Mac node full tool set: AX automation, window/screen evidence, scoped files, command templates | Each tool produces structured evidence; templates show the exact command |
| 3.3 | Android live deadline card: ongoing notification + Glance widget | Remaining time + completion probability + Acknowledge / Snooze / Start Focus |
| 3.4 | Knowledge graph + **visualization** | "Why do you believe this?" resolves to a source |
| 3.5 | Morning / evening / focus / learning modules | Each is a view over the shared goal engine, not a new engine |
| 3.6 | Voice on the Mac: Whisper + Piper, push-to-talk, visible indicator | Ported from v1, with an indicator |

### Phase 4 — Alexa

| # | Task | Exit test |
|---|---|---|
| 4.1 | Skill model: 7 intents, slots, utterances, en-IN | Model builds; simulator resolves each intent |
| 4.2 | ASK SDK Lambda, skill-ID restriction, interceptors | Only our skill id is accepted |
| 4.3 | Account linking against our OAuth2 server | Link, call an authorized intent, unlink |
| 4.4 | Reminders (per-reminder consent) + Proactive Events | Consent flow honoured; daily caps respected |
| 4.5 | Privacy policy, terms, certification checklist | Certification checklist passes |

### Phase 5 — Remaining connectors

| # | Task | Exit test |
|---|---|---|
| 5.1 | Slack Events API + `chat.postMessage` after approval | Signing secret verified; send requires approval |
| 5.2 | Google Classroom read | Coursework deadline creates a task |
| 5.3 | Canvas LMS read (free teacher instance for the demo) | Assignment deadline creates a task |
| 5.4 | WhatsApp Cloud API approved template | Template alert delivered |
| 5.5 | Twilio outbound call as final escalation | Call placed once, capped, opt-in, logged |
| 5.6 | OpenClaw adapter, isolated, narrow service identity | OpenClaw event → action card; **no DB credentials in that container** |

### Phase 6 — Credibility

| # | Task | Exit test |
|---|---|---|
| 6.1 | Adversarial: injection, replay, expired job, scope violation, storm | Malicious email causes **zero** effectful actions |
| 6.2 | Chaos: 429/500, Mac disconnect, duplicate webhook, stale schedule, provider outage | Known failures repair once or stop clearly |
| 6.3 | Metrics dashboard vs. §15 targets | All seven measured, not asserted |
| 6.4 | CI/CD: GitHub Actions, Developer-ID sign + notarize macOS, signed AAB | Clean deployment from scratch |
| 6.5 | Demo runbook, DEMO CLOCK (real scheduler path), one-click tenant reset | Seven-minute rehearsal succeeds twice |

**If time slips, cut in this order:** OpenClaw → Canvas/Moodle → WhatsApp → phone call → knowledge-graph
*visualization* → Alexa reminders.
**Never cut:** verified execution · failure prediction · approval workflow · audit timeline.

---

## 13. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **Free LLM tiers rate-limit mid-demo** | Demo stalls | Three-provider cascade + circuit breakers + ₹800/mo paid headroom. Pre-warm before the demo. **Verify current quotas in your own accounts** — published limits change often. |
| **Oracle Always Free ARM capacity exhausted** | No VM | Try adjacent regions; else Hetzner CX32 ~₹650/mo. Identical Compose file. |
| **Ollama not installed** | Only affects optional local dev | `brew install ollama` in bootstrap. Not on the critical path any more. |
| **Flutter macOS needs full Xcode** (you have only Command Line Tools) | Blocks Phase 3.1 | Install Xcode (~15 GB, free) **during Phase 0**. Android needs no Xcode, so Phase 2.4 is unblocked regardless. |
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
