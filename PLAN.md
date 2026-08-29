# JARVIS X — Implementation Plan

**Owner:** Pranav Bandaram · **Repo:** `Bitshifter-9/JARVIS-X` · **Supersedes:** `Bitshifter-9/Jarvis-` (preserved in [`legacy/jarvis-v1/`](legacy/jarvis-v1/))

This is the build plan. It adapts the *JARVIS X Advanced Architecture and Implementation Blueprint* to four
constraints that the blueprint did not have:

| Constraint | Consequence |
|---|---|
| **Lowest possible running cost** | The AWS control plane (Fargate, Aurora, EventBridge, SQS, Step Functions, Cognito) is replaced by free-tier equivalents. Target: **under $2/month**. |
| **No iOS** | No Xcode iOS toolchain, no TestFlight, no ActivityKit Live Activity, no APNs. Android is the only phone. |
| **Solo developer** | One deployable container with three entrypoints, not eight microservices. Contracts stay identical so it can be split later. |
| **Existing v1 code** | The working Ollama / Whisper / Piper / tools stack is harvested, not thrown away. |

**The product sentence does not change:** JARVIS X observes commitments, predicts failure, prepares the next
best action, executes through policy-controlled tools, verifies the result, and escalates only when the user
has authorized it.

---

## 1. What we are actually building

The blueprint's thesis is right and we keep it whole: this is judged as an **autonomous personal operations
platform**, not a voice assistant that opens apps. The closed loop is the product.

```
event → task → prediction → recovery plan → approval → execution → VERIFIED EVIDENCE → escalation
```

**Four planes, unchanged from the blueprint.** What changes is only the technology filling each box.

| Plane | Owns | Never owns |
|---|---|---|
| Experience | UI, voice capture, notification, approval, explainability | Cloud credentials, unrestricted OS execution |
| Control | Identity, events, task state, plans, policy, workflows, audit | Direct GUI manipulation |
| Execution | Typed cloud connectors and paired local tools | Deciding its own permissions |
| Evidence | Read-after-write checks, window/DOM state, provider IDs | Using model confidence as proof |

### The one milestone that matters

> From one real deadline event → one sourced task → one predicted failure → one approved Mac action →
> one **verified** result visible on the Android timeline.

Everything in Phase 2 and 3 hangs off that spine. If we build nothing else, we build that.

---

## 2. Stack decisions — blueprint vs. what we build

Every substitution below preserves the blueprint's *contract* and changes only its *implementation*, so a
later migration to AWS is a deployment change, not a rewrite.

| Concern | Blueprint | **We build** | Why | Cost |
|---|---|---|---|---|
| Compute | ECS Fargate ×3 services | **Oracle Cloud Always Free** ARM VM (4 cores / 24 GB), Docker Compose | Always Free is perpetual, not a 12-month trial. 24 GB is more RAM than 3 Fargate tasks. | **$0** |
| Database | Aurora PostgreSQL Serverless v2 | **Neon** free Postgres 16 + `pgvector` (fallback: Postgres container on the same VM) | Neon free tier includes pgvector and scale-to-zero. Aurora Serverless v2 bills even at idle. | **$0** |
| Queue | SQS + DLQ | **Postgres table + `FOR UPDATE SKIP LOCKED`** | Gives at-least-once delivery, retries, visibility timeout and a real DLQ column, in one table. No extra service. | **$0** |
| Scheduler | EventBridge Scheduler | **`schedules` table + 30 s tick worker** | We need one-shot T-24h/T-2h/T-1h/T-15m fires. A tick loop over an indexed timestamp does exactly that. | **$0** |
| Human approval | Step Functions callback token | **`approvals` table**: payload SHA-256, `expires_at`, `decision` | The workflow never "waits" — it is a suspended DB row resumed by an event. Cheaper *and* easier to audit. | **$0** |
| Identity | Cognito + PKCE | **Self-issued JWT** (HS256 access 15 min / refresh 30 d) + Argon2id | Behind an `IdentityProvider` interface so Cognito drops in for the Alexa phase. | **$0** |
| Realtime | API Gateway WebSocket | **FastAPI native WebSocket** + `device_connections` table | Same outbound-only, zero-inbound-port model for the Mac. | **$0** |
| Evidence blobs | S3 + KMS | **Cloudflare R2** free tier (10 GB, zero egress fee) | Screenshots and DOM snapshots. Local disk in dev. | **$0** |
| Edge / TLS | CloudFront + WAF | **Caddy** (automatic Let's Encrypt) + Cloudflare proxy | Free TLS, free WAF-ish rules, free DDoS shield. | **$0** |
| Push | FCM + APNs | **FCM only** | No iOS. | **$0** |
| Chat LLM | Bedrock | **Ollama** on the Mac, relayed through the device WebSocket | Inference happens on hardware you already own. | **$0** |
| Extraction LLM | Bedrock | **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`), strict JSON, capped | The one place accuracy decides the demo (90% target). Budget-guarded. | **~$1/mo** |
| Embeddings | Titan / OpenAI 1536-d | **`all-MiniLM-L6-v2` local, 384-d** | Already in v1. 384-d is 4× less storage and faster HNSW. Schema uses `vector(384)`. | **$0** |
| Mac automation | Swift + Pigeon + LaunchAgent | **Python + PyObjC** (`NSWorkspace`, `AXUIElement`, `CGWindowList`) + `launchd` plist | PyObjC calls the *identical* Apple APIs. No Xcode required, and Xcode is not installed. Swift is a Phase 3 packaging concern, not a capability one. | **$0** |
| Clients | Flutter macOS + iOS + Android | **Flutter macOS + Android** | Per your decision. | **$0** |
| Alexa | Custom skill, week 1 | **Phase 3**, Lambda free tier | Doesn't block the vertical slice. | **$0** |

**Total run cost: ~$1–2/month**, and that is one paid API line item you can switch off with an env var.

### The clever bit: LLM inference costs nothing

The cloud VM has no GPU and we are not paying for one. Instead the **Mac node advertises `llm.generate` as a
tool over the same outbound WebSocket it already holds open**. The backend routes inference to your Mac.

```
Cloud VM (control plane, no GPU)
   │  job.dispatch { action: "llm.generate", ... }
   ▼
Mac node ──► Ollama (llama3.1:8b) ──► streams tokens back over the same socket
```

Three providers behind one `LLMProvider` protocol, chosen per call by risk and required accuracy:

| Provider | Used for | Cost |
|---|---|---|
| `mac_relay` | chat, classification, planning, reflection | $0 |
| `ollama_local` | everything, when the backend runs on your Mac in dev | $0 |
| `anthropic` | deadline extraction only; strict JSON schema; hard token cap | ~$1/mo |

If the Mac is offline, chat degrades to "Mac node unavailable" rather than silently billing you. That is a
policy decision, encoded in `llm/router.py`.

---

## 3. Target architecture

```
                        SURFACES
   Flutter macOS Control Center · Flutter Android · Telegram · (Alexa, phase 3)
                             │
                             ▼
                    EDGE  —  Caddy + Cloudflare
                    auto-TLS · rate limit · webhook signature check
                             │
                             ▼
   ┌───────────────────── ONE CONTAINER, THREE ENTRYPOINTS ─────────────────────┐
   │                                                                            │
   │   api          FastAPI: REST + SSE + WebSocket                             │
   │   worker       agent loop · connector sync · verifier   (SKIP LOCKED)      │
   │   scheduler    30 s tick: due schedules → jobs                             │
   │                                                                            │
   │   AGENT CORE     INGEST→CLASSIFY→CONTEXT→PLAN→POLICY→EXECUTE→VERIFY→       │
   │                  REFLECT→COMMIT                                            │
   │   POLICY ENGINE  deterministic R0–R4, outside the model                    │
   └────────────────────────────────────────────────────────────────────────────┘
             │                          │                         │
             ▼                          ▼                         ▼
   Postgres + pgvector          Cloudflare R2             Outbound WSS
   goals · tasks · jobs         evidence blobs                  │
   approvals · evidence                                         ▼
   memories · audit_log                                   MAC NODE (your Mac)
                                                          PyObjC tools · Ollama
                                                          verifier · kill switch
```

**Trust boundaries — carried over verbatim from the blueprint, because they are the security story:**

1. Every surface is untrusted until its identity maps to a user. Telegram IDs, device keys and JWT subjects
   are separate identities linked in one `identities` table.
2. Retrieved email / Slack / LMS / web text is **untrusted data**. It can propose no tool call and cannot
   change policy. It is wrapped in a delimiter envelope and never concatenated into an instruction slot.
3. The agent *proposes* structured actions. The policy service returns `ALLOW` / `REQUIRE_APPROVAL` / `DENY`.
   The executor **revalidates independently** before dispatch.
4. The Mac opens only an outbound authenticated connection. No public port, no raw shell endpoint, ever.

---

## 4. Repository layout

```
JARVIS-X/
├── PLAN.md                      ← this file
├── README.md
├── docs/
│   ├── ARCHITECTURE.md          planes, trust boundaries, event flow
│   ├── COST.md                  every line item and its free-tier limit
│   ├── THREAT-MODEL.md          8 threats → controls → the test that proves it
│   └── DEMO-RUNBOOK.md          the 7-minute script + reset procedure
├── legacy/jarvis-v1/            v1 source, read-only reference (see §11)
├── packages/
│   ├── contracts/               ⚠ SOURCE OF TRUTH — freeze before writing code
│   │   ├── schemas/             EventEnvelope · Goal · Task · ActionProposal · Evidence
│   │   └── openapi/             generated → Dart + Python + TS clients
│   └── policy/                  risk rules as data + their test vectors
├── apps/
│   ├── api/jarvis/
│   │   ├── api/routes/          chat · goals · tasks · approvals · devices · webhooks
│   │   ├── core/                config · security · errors · idempotency · budget
│   │   ├── db/                  models · session · queue (SKIP LOCKED)
│   │   ├── services/            event · goal · agent · policy · tool_gateway
│   │   │                        evidence · notification · identity
│   │   ├── llm/                 provider protocol · ollama · mac_relay · anthropic · router
│   │   ├── connectors/          gmail · calendar · telegram · (slack, lms)
│   │   └── workers/             agent_worker · connector_worker · scheduler
│   ├── mac-node/macnode/        PyObjC helper — tools/ verify/ transport/
│   ├── control_center/          Flutter macOS
│   ├── mobile/                  Flutter Android
│   └── alexa-skill/             phase 3
├── migrations/                  Alembic
├── infra/{compose,caddy,scripts}
├── scripts/                     dev bootstrap, seed, demo reset
└── tests/{unit,integration,e2e,adversarial,fixtures}
```

**Why one container:** three Fargate services cost 3×. One image with `CMD` selecting `api` / `worker` /
`scheduler` costs 1× and keeps the module boundaries in code where they matter. Splitting later is a
Compose-file edit.

---

## 5. Data model

Relational first. JSONB for provider-specific payloads only — never as a substitute for a constraint.

**Core tables** (verbatim intent from blueprint §20, adapted):

`users` · `identities` · `devices` · `device_connections` · `goals` · `tasks` · `task_dependencies` ·
`work_sessions` · `goal_predictions` · `agent_runs` · `actions` · `approvals` · `evidence` · `memories` ·
`entities` · `relations` · `entity_aliases` · `source_accounts` · `source_objects` · `connector_cursors` ·
`events` · `jobs` · `schedules` · `notification_endpoints` · `standing_permissions` · `audit_log`

**Deltas from the blueprint, and the reason for each:**

| Change | Reason |
|---|---|
| `memories.embedding vector(384)` not `vector(1536)` | Local MiniLM embeddings. 4× less storage, faster index, no API cost. |
| New `jobs` table | Replaces SQS. Columns: `status`, `visible_at`, `attempts`, `max_attempts`, `locked_by`, `last_error`, `dead_lettered_at`. |
| `approvals` carries the callback | Replaces the Step Functions task token. Client only ever sees the opaque `approval_id`. |
| `events.idempotency_key UNIQUE` | `(tenant_id, provider, provider_event_id)`. The blueprint's at-least-once guard, enforced by the database. |

**Non-negotiable invariants:**

- **Every** table carries `user_id`. **Every** query filters on it. A row-level isolation test runs in CI.
- `tasks.version` increments on every update. A stale schedule fire reads the current version and exits
  silently. This is how "acknowledge cancels later alerts" actually works.
- Deadlines persist as **confirmed UTC timestamp + IANA timezone**, never as raw extracted text. Every
  reminder is *computed* from that pair.

**Indexes:** unique on `source_objects(provider, account_id, object_id)`; unique on the event idempotency key;
`tasks(user_id, status, due_at)`; `jobs(status, visible_at)`; `schedules(fire_at) WHERE status='pending'`;
partial indexes for open tasks, pending approvals, online devices. **HNSW on `memories.embedding` only after
measuring** — under ~10k rows a sequential scan wins.

---

## 6. The agent loop

The LLM is a planner inside a deterministic harness. It does not own the loop, the credentials, the budget,
the policy, or the definition of success.

| State | Input | Output | Stop condition |
|---|---|---|---|
| INGEST | event + identity | normalized observation | invalid signature / duplicate |
| CLASSIFY | observation | intent, urgency, source trust | unsupported intent |
| CONTEXT | intent | goal / task / memory context | privacy scope exceeded |
| PLAN | goal + context | bounded action DAG | no valid plan / budget |
| POLICY | next action | allow / approval / deny | deny or expired approval |
| EXECUTE | approved typed action | tool result | timeout / circuit open |
| VERIFY | result + expected state | evidence verdict | verified or no new evidence |
| REFLECT | verdict + remaining plan | continue / repair / ask / stop | max steps / replans / cost |
| COMMIT | final evidence | state update + follow-up | terminal |

```python
for step in range(MAX_STEPS):                       # MAX_STEPS = 8
    proposal = planner.next_action(state)
    decision = policy.evaluate(user, proposal, state)
    if decision is DENY:             return stop("policy_denied")
    if decision is REQUIRE_APPROVAL: return await_human(proposal)   # suspends the run
    result  = await tools.execute(proposal, timeout=proposal.timeout)
    verdict = await verifier.check(proposal.expected, result)
    state   = reducer.apply(state, proposal, result, verdict)
    if verdict.success:                              continue
    if not verdict.new_evidence or state.replans >= 2: return ask_user(state)
return stop("step_budget_exhausted")
```

**Self-healing is a graph of known repairs, never "keep trying."** A retry is permitted *only* when the next
attempt changes a relevant condition, or when the provider's `Retry-After` header instructs it.

| Failure | Diagnosis evidence | Allowed repair | Limit |
|---|---|---|---|
| Chrome not running | `NSRunningApplication` absent | `mac.open_app(chrome)` | 1 repair |
| Browser page wrong | DOM URL/title mismatch | `browser.navigate(expected_url)` | 2 navigations |
| Slack 429 | status + `Retry-After` | queue retry after header | provider cap |
| OAuth expired | provider `invalid_grant` | mark connector `reauth_required` | never retry a secret |
| Mac offline | connection state stale | push mobile notice, queue job | job expires |
| Deadline ambiguous | two plausible dates | ask user with choices | **no autonomous guess** |

**Budgets, enforced outside the model:** `MAX_STEPS=8`, `MAX_REPLANS=2`, `MAX_TOKENS_PER_RUN`, wall-clock
ceiling, per-user daily notification cap. Exceeding any one is a normal terminal state, not an exception.

---

## 7. The killer feature: failure prediction

Not reminders. **Knowing the current plan is mathematically unlikely to succeed, and proposing a fix.**

Transparent heuristic before any ML — because a judge can follow arithmetic and cannot follow a model:

```python
available_minutes = deadline - now - fixed_calendar_blocks - safety_buffer
p80_remaining     = sum(task.remaining * user_calibration_multiplier
                        for task in critical_path) * P80_FACTOR
finish_ratio      = available_minutes / p80_remaining

if finish_ratio < 1.0:
    severity = "critical" if finish_ratio < 0.65 else "at_risk"
    options  = [reduce_scope(noncritical_tasks),
                reorder_to_critical_path(),
                request_help_or_extension()]
```

`user_calibration_multiplier` is learned from `work_sessions`: estimated vs. actual, per user. That is the
feedback loop that makes it feel personal by day 10.

**The sentence a judge remembers — and it must be generated from real rows, not hardcoded:**

> "You have 170 usable minutes, while the 80th-percentile remaining work is 260 minutes. Removing the optional
> Alexa animation and postponing the knowledge-graph visualization raises predicted completion probability
> from 34% to 78%."

---

## 8. Safety, and why it is a feature

| Risk | Examples | Default |
|---|---|---|
| **R0** read-only | list tasks, read mail metadata, search memory | automatic after connector consent |
| **R1** reversible local | open/focus allowlisted app, create draft, start focus timer | automatic for the paired owner device |
| **R2** external effect | send message, create invite, submit form | **preview + single approval** |
| **R3** destructive | delete, install, terminal template, permission change | approval **plus** local Mac confirmation |
| **R4** prohibited | payment, credential export, disabling audit, any command originating in an email | **deny** |

**Approval binding.** An approval stores `SHA-256(canonical(tool) + args + user + device + expires_at)`.
Editing anything produces a *new* proposal and a *new* hash. The client receives only an opaque `approval_id`.
A replayed or mutated approval fails the hash check and is rejected.

**Simulation mode.** Planning, policy and verification preconditions all run; effectful tools are swapped for
simulators. The UI shows the exact plan, permissions, recipients, paths, expected evidence, estimated time and
cost. A judge flips `SIMULATE → EXECUTE` on the *same hashed plan*. This is the highest
demo-value-per-line-of-code feature in the entire build.

**Kill switch, three independent levers.** A global server flag rejects new R1–R3 actions and cancels queued
jobs; the Mac menu-bar STOP interrupts local jobs and drops the socket; the Android emergency action revokes
device sessions. **A kill switch never deletes evidence** — it records who invoked it and why.

---

## 9. Phased build

Phases, not calendar days — days slip, exit tests do not. **A phase is done when its exit test passes.**

### Phase 0 — Foundation (the part everyone skips and regrets)

| # | Task | Exit test |
|---|---|---|
| 0.1 | Freeze five v1 contracts: `EventEnvelope`, `Goal`, `Task`, `ActionProposal`, `Evidence` | One schema generates both Dart and Python types |
| 0.2 | `docker compose up` → Postgres 16 + pgvector; Alembic migration 0001 | `pytest tests/integration` green on a clean DB |
| 0.3 | FastAPI skeleton, structured JSON logging, `correlation_id` middleware, RFC-9457 problem responses | `/healthz` returns build SHA; every log line carries a correlation id |
| 0.4 | JWT auth + Argon2id, `IdentityProvider` interface | Register → login → refresh → revoke |
| 0.5 | `jobs` table + `SKIP LOCKED` claim + retry/backoff/DLQ | 100 jobs, 4 workers, zero double-processing, poison job dead-letters |

> **Gate:** do not start Phase 1 until 0.5 passes. Every later phase rides on that queue.

### Phase 1 — The vertical slice ⭐ *the whole submission lives or dies here*

| # | Task | Exit test |
|---|---|---|
| 1.1 | Mac pairing: ECDSA key in Keychain, one-time challenge, outbound WSS, heartbeat | Pair, disconnect, reconnect, revoke — all visible in `devices` |
| 1.2 | `mac.open_app` via PyObjC `NSWorkspace` + bundle-ID allowlist | Chrome opens; a non-allowlisted bundle is refused *at the helper* |
| 1.3 | **Verifier**: `NSRunningApplication` pid + `CGWindowListCopyWindowInfo` frontmost bundle | Evidence row shows pid + frontmost bundle id. **Exit code is not evidence.** |
| 1.4 | Signed job protocol: `job_id`, nonce, `issued_at`, `expires_at`, `policy_version`, signature | Replayed job rejected; expired job rejected; both audited |
| 1.5 | Canonical event envelope + idempotent ingest | Same provider event twice → one task |
| 1.6 | Telegram connector: alerts + inline approval buttons | Approve from Telegram; Mac executes; evidence returns |
| 1.7 | Goal/task DAG + critical path | Hackathon goal decomposed, dependencies enforced |
| 1.8 | Failure-prediction heuristic + recovery options | Risk severity *changes* when an estimate or progress changes |
| 1.9 | Scheduler: T-24h/T-2h/T-1h/T-15m, version-guarded | Acknowledging cancels every later alert |
| 1.10 | Approvals with payload hash + simulation mode | An R2 send **cannot** run without a valid unexpired matching approval |

> **Gate — the milestone:** deadline event → sourced task → prediction → approval → verified Mac action,
> one correlation id end to end.

### Phase 2 — Surfaces and connectors

| # | Task | Exit test |
|---|---|---|
| 2.1 | Gmail connector (OAuth desktop + `history.list` **polling**, not Pub/Sub — see §12) | A real email deadline creates exactly one sourced task |
| 2.2 | Deadline extraction: Haiku, strict JSON, low temperature, schema-validated | ≥90% on a curated 50-item fixture set |
| 2.3 | Flutter Android: Today · Goals · Approvals · Timeline · Devices · Connectors | Phone approves; phone shows Mac evidence |
| 2.4 | FCM push + Android exact alarm (opt-in) + ongoing notification | Deadline card with live remaining time and probability |
| 2.5 | Flutter macOS Control Center + menu-bar kill switch | Chat, deadline board, timeline, approval cards, STOP |
| 2.6 | Escalation chain: push → Telegram → call, quiet hours, per-day cap | An ignored alert escalates exactly **once** |
| 2.7 | Memory: working / episodic / semantic + hybrid SQL-then-vector retrieval | Retrieval returns citations, never credentials |
| 2.8 | Google Calendar read → `fixed_calendar_blocks` feeds the predictor | Prediction changes when a meeting is added |

### Phase 3 — Credibility

| # | Task | Exit test |
|---|---|---|
| 3.1 | Adversarial fixtures: prompt injection, replay, expired job, scope violation, storm | Malicious email causes **zero** effectful actions |
| 3.2 | Chaos: connector 429/500, Mac disconnect, duplicate webhook, stale schedule | Known failures repair once or stop clearly |
| 3.3 | Metrics dashboard against blueprint §26 targets | All seven targets measured, not asserted |
| 3.4 | Alexa skill: `NextDeadline`, `GoalStatus`, `StartFocus` + account linking | Simulator + one real device invocation |
| 3.5 | Knowledge graph `entities`/`relations` with provenance on every edge | "Why do you believe this?" resolves to a source |
| 3.6 | Demo runbook, DEMO CLOCK (1 h → 1 min, real scheduler path), one-click tenant reset | Seven-minute rehearsal succeeds twice |

**If time slips, cut in this order:** WhatsApp → knowledge-graph *visualization* → Alexa reminders →
Slack → Alexa entirely.
**Never cut:** verified Mac action · failure prediction · approval workflow · audit timeline.
Those four *are* the differentiation.

---

## 10. Environment and cost control

| Environment | What runs | Guardrail |
|---|---|---|
| Local | Docker Postgres + FastAPI + Ollama on the Mac | Zero cloud model calls in unit tests; fixture replay only |
| Cloud (single) | Oracle Always Free VM, Neon Postgres, Caddy TLS | One environment — a solo dev cannot keep two honest |
| Demo | Same VM, `TENANT=demo`, seeded fixtures | One-click reset clears **only** demo-tenant rows |

**Hard budget controls, in code not in a spreadsheet:**

- `ANTHROPIC_MONTHLY_BUDGET_USD` — the router refuses paid calls past it and falls back to `mac_relay`.
- Per-run caps: max input/output tokens, max wall time, max tool calls.
- Per-user daily notification and call caps.
- `ENABLE_PAID_LLM=false` must produce a fully working system, only with weaker extraction. **This is a test.**

**The traps that actually generate surprise bills** (none of which apply to us, by construction): NAT Gateway
data processing, Aurora Serverless v2 idle ACUs, an always-on Fargate task, and an unbounded agent loop. The
first three we designed out. The fourth is `MAX_STEPS`.

---

## 11. What we harvest from v1

`legacy/jarvis-v1/` is **reference, not a dependency.** Nothing imports it. Port deliberately:

| v1 file | Verdict | Action |
|---|---|---|
| `tools.py` `open_app` | Concept ✅ / implementation ❌ | `os.system("open -a ...")` is a shell string with no verification. Becomes typed `mac.open_app(bundle_id)` on `NSWorkspace` with an allowlist and frontmost-window evidence. |
| `tools.py` `run_command` | ❌ **Delete** | `subprocess.check_output(cmd, shell=True)` on model output is arbitrary chat-to-shell — exactly the R4 the blueprint prohibits. Replaced by predefined command *templates* only. |
| `voice_jarvis.py` record/transcribe | ✅ Port | faster-whisper int8 + sounddevice work well. Move into `mac-node`, add a visible listening indicator and push-to-talk. |
| `speak()` Piper TTS | ✅ Port | Keep. Add sentence-buffered streaming as v1 already does. |
| `memory.py` ChromaDB | ⚠️ Replace | Same embedding model, but into `pgvector` so memory joins goals and tasks in one query with one tenant filter. |
| `knowledge/rag.py` | ⚠️ Rework | Whole-file embedding loses precision. Chunk + store provenance so retrieval can cite. |
| `brain.py` prompt loop | ❌ Superseded | Free-running `while True` with no budget, no policy, no verification — it is precisely the loop the blueprint replaces with a state machine. |
| `app.py` FastAPI + WebSocket + pywebview | ✅ Port patterns | Token streaming and the WS envelope are good. Becomes the Mac node transport. |
| `static/` HUD UI | ✅ Keep as design reference | Feed the visual language into the Flutter theme. |
| `try_tools()` keyword matching | ❌ Superseded | Keyword `if "chrome" in text` fires on "close chrome". Replaced by typed proposals through the policy engine. |

**The two deletions are the point.** `run_command` and the unbounded loop are the exact vulnerabilities the
blueprint's policy engine exists to eliminate. Removing them is a talking point, not an omission.

---

## 12. Known risks, honestly stated

| Risk | Impact | Mitigation |
|---|---|---|
| **Flutter macOS needs full Xcode** — only Command Line Tools are installed | Blocks Phase 2.5 | Install Xcode (~15 GB, free) **during Phase 0**, not on the day you need it. Android needs no Xcode, so Phase 2.3 is unblocked either way. |
| **Ollama not currently installed** | Blocks `mac_relay` | `brew install ollama && ollama pull llama3.1:8b` in Phase 0 bootstrap. |
| **Python 3.14.7 is the system default** | `faster-whisper`, `chromadb`, `sentence-transformers` lack 3.14 wheels | Pin **3.12** via `uv python pin 3.12`. Non-negotiable. |
| **Gmail push needs GCP Pub/Sub + verified domain** | Days of setup for seconds of latency | Phase 2.1 uses `history.list` polling on a 60 s cursor — **identical normalizer**, zero GCP setup. Upgrade to push only if a judge asks. |
| **Oracle Always Free ARM capacity is often exhausted in popular regions** | No VM | Try adjacent regions; fall back to Fly.io (~$2/mo) or run the control plane on the Mac behind a Cloudflare Tunnel. The Compose file is identical. |
| **Mac asleep ⇒ no `mac_relay` inference, no local execution** | Proactivity gaps | Backend stays up regardless: scheduling, extraction (Haiku) and notifications are unaffected. Mac jobs queue with an expiry and are offered for explicit review on reconnect — never silently executed late. |
| **Accessibility / Screen Recording prompts differ after any signing change** | Demo-day surprise | Test from a **clean macOS user account** before the demo. |
| **Alexa certification takes days** | Missed deadline | Phase 3, demoed via the simulator — certification is not required for a demo. |

---

## 13. Definition of done

**Metrics** (blueprint §26 — measured, not claimed):

| Metric | Target |
|---|---|
| Deadline extraction accuracy | ≥ 90% on 50 curated items |
| Duplicate task rate | < 1% under replay |
| Verified tool success | ≥ 95% (evidence verdict, **not** exit code) |
| Approval coverage | 100% of R2/R3 actions |
| Event-to-alert latency | < 10 s on the demo path |
| Prompt-injection block | 100% on adversarial fixtures |
| Monthly run cost | ≤ $2 |

**One correlation id** connects: webhook → event → extraction → task → schedule → notification → approval →
Mac job → evidence. If you cannot follow a single request across all nine hops in the logs, the system is not
done.

---

## 14. Start here

```bash
# 1. Prerequisites (Phase 0, do these first — they are the long poles)
brew install ollama && ollama pull llama3.1:8b
uv python pin 3.12
xcode-select --install          # then install full Xcode from the App Store for Flutter macOS

# 2. Bring up the local stack
docker compose -f infra/compose/docker-compose.dev.yml up -d
uv sync && uv run alembic upgrade head
uv run uvicorn jarvis.main:app --reload
```

Then, in order, and **do not skip ahead**:

1. Freeze the five contracts in `packages/contracts/schemas/`. Everything downstream generates from them.
2. Build the `jobs` queue and prove it with four concurrent workers (Phase 0.5).
3. Pair the Mac node and get **one verified `mac.open_app`** with real frontmost-window evidence.
4. Replay one fixture Gmail event → one sourced task.
5. Add the prediction heuristic and render **one** recovery card.
6. Add approvals + simulation, then wire Telegram approve/reject.
7. Only after that vertical slice is green: Android, then Flutter macOS, then Alexa.

> The spine is: **one real deadline → one sourced task → one approved action → one verified result.**
> Build that end to end before building anything wide.
