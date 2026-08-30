# JARVIS X

> **JARVIS X observes commitments, predicts failure, prepares the next best action, executes through
> policy-controlled tools, verifies the result, and escalates only when you have authorized it.**

A personal operations platform that predicts deadline failure, prepares a recovery plan, asks before
consequences, executes across your devices, and **proves what actually happened**.

Not a chatbot. Not a voice assistant that opens apps. A closed loop:

```
event → task → prediction → recovery plan → approval → execution → verified evidence → escalation
```

**It runs in the cloud.** Your Mac is an optional execution node, never a dependency — deadlines still fire,
predictions still update, approvals still resolve and browser actions still execute with the laptop shut.

<sub>Successor to [`Bitshifter-9/Jarvis-`](https://github.com/Bitshifter-9/Jarvis-) · v1 source preserved in
[`legacy/jarvis-v1/`](legacy/jarvis-v1/)</sub>

---

## Why this is different

| | Typical assistant | JARVIS X |
|---|---|---|
| Trigger | You ask | A deadline event wakes it |
| Planning | Single response | Goal DAG, critical path, calibrated estimates |
| Foresight | None | Predicts *when the current plan will miss*, and proposes recovery |
| Consequences | Just does it | R0–R4 risk policy; approval required before external effects |
| Proof of success | "Done!" | Foreground bundle id, pid, DOM state, provider message id |
| Stoppability | Kill the terminal | Three independent kill switches; evidence is never deleted |
| Availability | Dies with the process | Cloud-resident; survives your Mac being offline |

**The differentiator is failure prediction** — not reminders, but knowing the plan is mathematically unlikely
to succeed:

> *"You have 170 usable minutes, while the 80th-percentile remaining work is 260 minutes. Removing the
> optional Alexa animation and postponing the knowledge-graph visualization raises predicted completion
> probability from 34% to 78%."*

---

## Architecture

```
                        SURFACES
   Flutter macOS · Flutter Android · Telegram · WhatsApp · Alexa · phone call
                             │
                    EDGE  —  Caddy + Cloudflare
                    auto-TLS · rate limit · signature check
                             │
   ┌───────────────── ONE CONTAINER, THREE ENTRYPOINTS ─────────────────┐
   │  api        FastAPI: REST + SSE + WebSocket + OAuth2 server        │
   │  worker     agent loop · connectors · browser · verifier           │
   │  scheduler  30 s tick: due schedules → jobs                        │
   │                                                                    │
   │  AGENT CORE   INGEST→CLASSIFY→CONTEXT→PLAN→POLICY→EXECUTE→         │
   │               VERIFY→REFLECT→COMMIT                                │
   │  POLICY       deterministic R0–R4, outside the model               │
   │  LLM ROUTER   Groq → Gemini → OpenRouter, budget-capped            │
   └────────────────────────────────────────────────────────────────────┘
        │                    │                    │              │
  Postgres+pgvector   Cloudflare R2      Headless Playwright   Outbound WSS
  goals · tasks       evidence blobs     DOM evidence                │
  approvals · jobs                       (cloud-side)          MAC NODE
  memories · graph                                             PyObjC tools
  audit · llm_calls                                            verifier · STOP
```

**Four planes.** *Experience* owns UI, voice and approval — never credentials. *Control* owns identity, state,
policy and audit — never GUI manipulation. *Execution* owns typed connectors and paired local tools — never
its own permissions. *Evidence* owns read-after-write verification — and never treats model confidence as proof.

**The Mac opens only an outbound connection.** No public port. No raw shell endpoint. Ever.

Full detail: **[PLAN.md](PLAN.md)** · **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

---

## Cloud-first: the Mac is optional

| Tier | Capability | Mac needed? |
|---|---|---|
| **A — always on** | Ingestion, extraction, goal engine, **failure prediction**, scheduling, escalation, approvals, memory, knowledge graph, audit, Telegram / WhatsApp / call / Alexa / Android, **browser automation + DOM evidence** | ❌ No |
| **B — Mac only** | `mac.open_app`, `mac.focus`, AX UI automation, window/screen evidence, local files, wake-word voice | ✅ Yes |

The browser worker runs **headless Playwright in the cloud**, so "execute → verify with real DOM evidence" —
the demo's proof-of-work moment — survives the Mac being off. When a Tier-B action is requested while the Mac
is offline it is queued with an expiry, and **never silently executed once stale**: on reconnect it is offered
for explicit review.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · Alembic |
| Database | PostgreSQL 16 + `pgvector`, co-located with the API |
| Queue | Postgres `FOR UPDATE SKIP LOCKED` — replaces SQS |
| Scheduler | `schedules` table + 30 s tick — replaces EventBridge Scheduler |
| Approvals | Payload-hashed DB rows — replaces Step Functions callback tokens |
| Auth | Authlib OAuth2 server + JWT + Argon2id (Alexa account linking needs a real OAuth2 grant) |
| Agent loop | **LangGraph** + Postgres checkpointer — sequencing only; policy stays ours |
| Realtime | FastAPI native WebSocket |
| **LLM** | **LiteLLM** transport → **Groq** (chat/plan) → **Gemini** (extraction) → **OpenRouter** (overflow + paid), cascading on rate limits, hard budget cap |
| Testing | pytest · **Hypothesis** invariants · **Schemathesis** contract fuzzing · committed accuracy evals |
| Embeddings | `all-MiniLM-L6-v2` on the VPS, CPU, 384-d |
| Browser | Headless Playwright, cloud-side |
| Mac automation | Python + PyObjC — `NSWorkspace`, `AXUIElement`, `CGWindowList` |
| Clients | Flutter (macOS + Android) · Telegram · WhatsApp · Alexa |
| Hosting | Oracle Cloud Always Free ARM · Docker Compose · Caddy |

No single LLM provider's rate limit can stop the system: the router cascades on 429, trips a circuit breaker
per provider, and accounts for every call in `llm_calls`. With `ENABLE_PAID_LLM=false` the whole system still
works on free tiers alone — and that is a test, not a claim.

**No iOS.** No APNs, no TestFlight, no ActivityKit — the Android ongoing notification and Glance widget carry
the live deadline card. Contracts stay iOS-ready.

---

## Cost

**Envelope ₹2,000/month. Expected spend ₹0–700.** Against roughly ₹11,000–15,000/month for the blueprint's
AWS topology. Every substitution maps 1:1 back to its AWS service, so migrating later is a Compose swap plus a
CDK stack rather than a rewrite. See [docs/COST.md](docs/COST.md).

---

## Feature coverage

**Every feature in the source blueprint ships.** [PLAN.md §2](PLAN.md) maps all 34 sections of the PDF to
where each one is implemented. Two items are *not* built, both because the blueprint itself says so:
multi-agent orchestration (*"deferred until single-agent contracts are reliable"*) and arbitrary GUI/terminal
autonomy (*"do not build"*). iOS is deferred by choice.

---

## Repository layout

```
JARVIS-X/
├── PLAN.md                  ← the build plan. Start here.
├── docs/                    architecture · cost · threat model · demo runbook
├── legacy/jarvis-v1/        v1 source, read-only reference
├── packages/
│   ├── contracts/           ⚠ SOURCE OF TRUTH — schemas · OpenAPI · tool manifests
│   └── policy/              risk rules as data + test vectors
├── apps/
│   ├── api/jarvis/          FastAPI · agent core · services · connectors · llm · workers
│   ├── mac-node/            PyObjC automation helper — `python -m macnode pair|run`
│   ├── control_center/      Flutter macOS
│   ├── mobile/              Flutter Android
│   └── alexa-skill/         TypeScript ASK SDK Lambda
├── migrations/              Alembic
├── infra/                   compose · caddy · deploy
├── scripts/                 bootstrap · seed · demo reset
└── tests/                   unit · integration · e2e · adversarial · chaos · fixtures
```

---

## Status

🚧 **Phase 6 — 4 of 5.** 500 Python + 11 Dart tests green, including 46 adversarial, 15 chaos, and a 13-beat demo rehearsal that runs twice. Remaining UI work is blocked on toolchains: full Xcode for macOS, Android SDK + a Firebase project for push.

| Phase | Scope | State |
|---|---|---|
| 0 | Contracts · DB · OAuth2 · job queue · **LLM router** | ✅ |
| 1 | **Vertical slice** — deadline → prediction → approval → verified action, *Mac off* | ✅ |
| 2 | LangGraph loop · LiteLLM · extraction · Gmail · Calendar · escalation · memory · Flutter app | 🚧 9/10 |
| 3 | Mac tool set ✅ · knowledge graph ✅ · product modules ✅ · macOS UI · voice | 🚧 3/6 |
| 4 | Alexa skill · account linking · reminders · certification | ⬜ |
| 5 | Slack · Classroom · Canvas · WhatsApp · calls · OpenClaw | ⬜ |
| 6 | Adversarial ✅ · chaos ✅ · metrics ✅ · demo ✅ · CI 🚧 | 🚧 4/5 |

Phases end on **exit tests**, not calendar dates. See [PLAN.md §12](PLAN.md).

---

## Getting started

**What only you can do — see [docs/SETUP.md](docs/SETUP.md)** for the ordered walkthrough.
The short version: two free LLM keys unblock the agent and the graded accuracy metric;
everything else can wait.

**Free accounts to create first (~20 minutes):** [Groq](https://console.groq.com) ·
[Google AI Studio](https://aistudio.google.com) · [OpenRouter](https://openrouter.ai) ·
[Oracle Cloud](https://cloud.oracle.com) · [Cloudflare R2](https://dash.cloudflare.com) ·
`@BotFather` on Telegram.

```bash
make bootstrap    # pins Python 3.12, installs deps, starts Postgres+pgvector, migrates
make api          # http://localhost:8000/docs
make seed         # demo@jarvis-x.dev / demo-password-12345
make test         # 500 Python tests
make mobile       # the Flutter app in Chrome
make mobile-test  # analyze + 11 Dart tests
make test-live    # + accuracy evals against real providers (needs keys)
```

`make help` lists everything. Postgres binds host port **5433**, because a Homebrew
PostgreSQL commonly owns 5432 and silently connecting to the wrong server is a miserable
thing to debug.

Optional, and not on the critical path:

```bash
brew install ollama && ollama pull llama3.1:8b   # local dev inference; never required
xcode-select --install                           # + full Xcode for Flutter macOS (phase 3)
```

---

## Safety model

| Risk | Examples | Default |
|---|---|---|
| **R0** read-only | list tasks, read mail metadata, search memory | automatic after consent |
| **R1** reversible local | open allowlisted app, create draft, start focus timer | automatic on paired owner device |
| **R2** external effect | send message, create invite, submit form | preview + approval |
| **R3** destructive | delete, install, permission change | approval + local Mac confirmation |
| **R4** prohibited | payment, credential export, disabling audit, **any command originating in an email** | denied |

- Retrieved email, Slack and web text is **untrusted data**. It can propose no tool call and cannot change policy.
- Approvals bind to `SHA-256(tool + args + user + device + expiry)`. Edit it and it becomes a new proposal.
- **Simulation mode** runs planning, policy and verification with effectful tools swapped for simulators —
  then executes the *same hashed plan* on demand.
- Three kill switches: server flag, Mac menu-bar STOP, Android session revoke. None delete evidence.

See [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md).

---

## License

Not yet chosen — add before making the repository public.
