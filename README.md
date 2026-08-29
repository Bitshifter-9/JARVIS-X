# JARVIS X

> **JARVIS X observes commitments, predicts failure, prepares the next best action, executes through
> policy-controlled tools, verifies the result, and escalates only when you have authorized it.**

A personal operations platform that predicts deadline failure, prepares a recovery plan, asks before
consequences, executes across your devices, and **proves what actually happened**.

Not a chatbot. Not a voice assistant that opens apps. A closed loop:

```
event → task → prediction → recovery plan → approval → execution → verified evidence → escalation
```

<sub>Successor to [`Bitshifter-9/Jarvis-`](https://github.com/Bitshifter-9/Jarvis-) · v1 source preserved in
[`legacy/jarvis-v1/`](legacy/jarvis-v1/)</sub>

---

## Why this is different

Most assistants answer questions or run commands. JARVIS X manages commitments across your digital life.

| | Typical assistant | JARVIS X |
|---|---|---|
| Trigger | You ask | A deadline event wakes it |
| Planning | Single response | Goal DAG, critical path, calibrated estimates |
| Foresight | None | Predicts *when the current plan will miss*, and proposes recovery |
| Consequences | Just does it | R0–R4 risk policy; approval required before external effects |
| Proof of success | "Done!" | Foreground bundle id, pid, DOM state, provider message id |
| Stoppability | Kill the terminal | Three independent kill switches; evidence is never deleted |

**The differentiator is failure prediction.** Not reminders — knowing the plan is mathematically unlikely to
succeed:

> *"You have 170 usable minutes, while the 80th-percentile remaining work is 260 minutes. Removing the
> optional Alexa animation and postponing the knowledge-graph visualization raises predicted completion
> probability from 34% to 78%."*

---

## Architecture

```
                        SURFACES
   Flutter macOS Control Center · Flutter Android · Telegram · (Alexa)
                             │
                    EDGE  —  Caddy + Cloudflare
                    auto-TLS · rate limit · signature check
                             │
   ┌───────────────── ONE CONTAINER, THREE ENTRYPOINTS ─────────────────┐
   │  api        FastAPI: REST + SSE + WebSocket                        │
   │  worker     agent loop · connectors · verifier                     │
   │  scheduler  30 s tick: due schedules → jobs                        │
   │                                                                    │
   │  AGENT CORE   INGEST→CLASSIFY→CONTEXT→PLAN→POLICY→EXECUTE→         │
   │               VERIFY→REFLECT→COMMIT                                │
   │  POLICY       deterministic R0–R4, outside the model               │
   └────────────────────────────────────────────────────────────────────┘
        │                      │                        │
   Postgres+pgvector    Cloudflare R2            Outbound WSS
   goals · tasks        evidence blobs                  │
   approvals · jobs                              MAC NODE (your Mac)
   evidence · audit                              PyObjC tools · Ollama
                                                 verifier · kill switch
```

**Four planes.** *Experience* owns UI, voice and approval — never credentials. *Control* owns identity,
state, policy and audit — never GUI manipulation. *Execution* owns typed connectors and paired local tools —
never its own permissions. *Evidence* owns read-after-write verification — and never treats model confidence
as proof.

**The Mac opens only an outbound connection.** No public port. No raw shell endpoint. Ever.

Full detail: **[PLAN.md](PLAN.md)** · **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

---

## Stack

Chosen for capability first and cost second — **target run cost under $2/month.**

| Layer | Technology |
|---|---|
| Backend | Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · Alembic |
| Database | PostgreSQL 16 + `pgvector` (Neon free tier) |
| Queue | Postgres `FOR UPDATE SKIP LOCKED` — replaces SQS |
| Scheduler | `schedules` table + 30 s tick — replaces EventBridge Scheduler |
| Approvals | Payload-hashed DB rows — replaces Step Functions callback tokens |
| Auth | Self-issued JWT + Argon2id, behind an `IdentityProvider` interface |
| Realtime | FastAPI native WebSocket |
| Chat / planning LLM | **Ollama on your Mac**, relayed over the device WebSocket — $0 |
| Extraction LLM | Claude Haiku 4.5, strict JSON, hard budget cap — ~$1/mo |
| Embeddings | `all-MiniLM-L6-v2` local, 384-d |
| Mac automation | **Python + PyObjC** — `NSWorkspace`, `AXUIElement`, `CGWindowList` |
| Clients | Flutter (macOS + Android) · Telegram |
| Hosting | Oracle Cloud Always Free ARM VM · Docker Compose · Caddy |

**Inference costs nothing** because the Mac node advertises `llm.generate` as a tool over the socket it
already holds open. The cloud VM has no GPU and never needs one. With `ENABLE_PAID_LLM=false` the entire
system still works — only extraction gets weaker. That is a test, not a claim.

**No iOS.** No APNs, no TestFlight, no ActivityKit. Android only.

---

## Repository layout

```
JARVIS-X/
├── PLAN.md                  ← the build plan. Start here.
├── docs/                    architecture · cost · threat model · demo runbook
├── legacy/jarvis-v1/        v1 source, read-only reference
├── packages/
│   ├── contracts/           ⚠ SOURCE OF TRUTH — schemas + OpenAPI
│   └── policy/              risk rules as data + test vectors
├── apps/
│   ├── api/jarvis/          FastAPI + agent core + workers + connectors
│   ├── mac-node/            PyObjC automation helper
│   ├── control_center/      Flutter macOS
│   ├── mobile/              Flutter Android
│   └── alexa-skill/         phase 3
├── migrations/              Alembic
├── infra/                   compose · caddy · deploy scripts
├── scripts/                 bootstrap · seed · demo reset
└── tests/                   unit · integration · e2e · adversarial · fixtures
```

---

## Status

🚧 **Phase 0 — Foundation.** Structure and plan committed; implementation starting.

| Phase | Scope | State |
|---|---|---|
| 0 | Contracts · DB · auth · job queue | 🚧 In progress |
| 1 | **Vertical slice**: deadline → task → prediction → approval → verified Mac action | ⬜ Next |
| 2 | Gmail · extraction · Android · macOS · escalation · memory | ⬜ |
| 3 | Adversarial · chaos · metrics · Alexa · knowledge graph · demo | ⬜ |

Phases end on **exit tests**, not calendar dates. See [PLAN.md §9](PLAN.md).

---

## Getting started

**Prerequisites** — do these first, they are the long poles:

```bash
brew install ollama && ollama pull llama3.1:8b   # local inference
uv python pin 3.12                                # 3.14 has no ML wheels yet
xcode-select --install                            # + full Xcode for Flutter macOS
```

**Run the stack:**

```bash
docker compose -f infra/compose/docker-compose.dev.yml up -d   # Postgres + pgvector
uv sync
uv run alembic upgrade head
uv run uvicorn jarvis.main:app --reload
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
- Approvals are bound to `SHA-256(tool + args + user + device + expiry)`. Edit it and it is a new proposal.
- **Simulation mode** runs planning, policy and verification with effectful tools swapped for simulators —
  then executes the *same hashed plan* on demand.
- Three kill switches: server flag, Mac menu-bar STOP, Android session revoke. None of them delete evidence.

See [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md).

---

## License

Not yet chosen — add before making the repository public.
