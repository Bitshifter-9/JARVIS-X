# Architecture

Companion to [`PLAN.md`](../PLAN.md). This document explains *how it works*; the plan explains *what to build
and in what order*.

## Four planes

| Plane | Owns | Never owns |
|---|---|---|
| **Experience** | UI, voice capture, notification, approval, explainability | Raw cloud credentials or unrestricted OS execution |
| **Control** | Identity, events, task state, plans, policy, workflows, audit | Direct GUI manipulation |
| **Execution** | Typed cloud connectors and paired local tools | Deciding its own permissions |
| **Evidence** | Read-after-write checks, window/DOM state, provider ids, screenshots | Using model confidence as proof |

The separation is the security model. An LLM that plans but cannot grant itself permission, and a verifier
that reports state rather than intent, is what makes autonomy safe enough to demo.

## Event flow

Events wake JARVIS; durable state remembers progress. The agent never polls everything in one loop, and never
stays alive waiting for a person.

```
Gmail ──watch/poll──► Ingress ──normalize + enqueue──► Agent
                                                        │ extract + upsert
                                                        ▼
                                                   DB / Scheduler
                                                        │ T-1h alert
                                                        ▼
                                                   Mobile / Mac
                                                        │ acknowledge
                                                        ▼
                                              cancel later alerts
```

### Event classes

| Event type | Producer | Route | Consumer |
|---|---|---|---|
| `source.message.changed` | Gmail / Telegram poll or webhook | `jobs` queue | normalizer + deadline extractor |
| `task.deadline.soon` | scheduler tick | `jobs` priority lane | escalation worker |
| `action.approval.decided` | Android / Mac / Telegram callback | HTTP → run resume | suspended agent run |
| `device.job.result` | Mac WebSocket | WS route → event bus | verifier + audit writer |
| `goal.progress.changed` | task service | event bus | failure predictor + notification |

### Canonical envelope

```json
{
  "event_id": "evt_01...",
  "event_type": "source.message.changed",
  "occurred_at": "2026-08-30T09:10:00Z",
  "tenant_id": "usr_...",
  "source": { "provider": "gmail", "object_id": "18c..." },
  "correlation_id": "cor_...",
  "causation_id": null,
  "schema_version": 1,
  "payload": { "history_id": "..." }
}
```

- **Idempotency key** = `(tenant_id, provider, provider_event_id)`, enforced by a unique index.
- A webhook acknowledges fast, stores minimal metadata and enqueues. It **never** calls the LLM synchronously.
- Everything is at-least-once, so every consumer is idempotent and reconciliation jobs run even when webhooks
  look healthy.

## The job queue (replaces SQS)

```sql
UPDATE jobs SET status='running', locked_by=:worker, visible_at=now() + interval '5 minutes'
WHERE id = (
  SELECT id FROM jobs
  WHERE status='pending' AND visible_at <= now()
  ORDER BY priority DESC, visible_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
) RETURNING *;
```

`SKIP LOCKED` gives concurrent workers without double-processing. `visible_at` is the visibility timeout.
`attempts >= max_attempts` sets `dead_lettered_at` — a real DLQ, in a column.

## Scheduling and escalation

Never schedule from raw extracted text. Persist a **confirmed UTC timestamp plus IANA timezone**, then
*compute* each reminder from that pair.

| Stage | Implementation | Guardrail |
|---|---|---|
| Retrieve | fetch the object by provider id | least OAuth scope; content labelled untrusted |
| Extract | strict JSON: title, `due_at`, timezone, owner, `evidence_span`, confidence | low temperature; schema validated |
| Resolve | relative dates resolved against message timestamp + account timezone | reject past/impossible values |
| Confirm | auto-confirm structured Calendar/LMS fields; **ask** on ambiguous email | confidence alone never overrides a conflict |
| Deduplicate | unique `(provider, account, object)` + task version | one task, many source updates |
| Schedule | T-24h / T-2h / T-1h / T-15m | payload carries `task_id` **and** `version` |
| Escalate | push → Telegram → call | quiet hours, opt-in, per-day cap, ack cancels |

**Version guard:** a task update increments `version`. A stale schedule fire reads the current version, sees a
mismatch, and exits silently. This is how "acknowledge cancels later alerts" is actually implemented.

## Mac device pairing and job protocol

Pairing creates a device *identity*. It does not copy cloud secrets to the Mac.

1. Sign in to the Mac app; obtain a user token.
2. The helper generates an **ECDSA key in Keychain** and sends the public key plus device metadata.
3. The backend returns a one-time challenge; the helper signs it; the backend stores the verified public key.
4. The Mac opens an outbound WSS with a short-lived device token, authorized on connect.
5. Every job carries `job_id`, `action`, `args`, `nonce`, `issued_at`, `expires_at`, `policy_version`, `signature`.
6. The helper validates expiry, signature, nonce replay and **its own local policy** before executing, then
   emits `ACK` → `PROGRESS` → `RESULT`.

```json
{
  "type": "job.dispatch",
  "job_id": "job_...",
  "action": "mac.open_app",
  "args": { "bundle_id": "com.microsoft.VSCode" },
  "risk": "R1",
  "nonce": "...",
  "issued_at": "...",
  "expires_at": "...",
  "policy_version": 7,
  "signature": "base64..."
}
```

**Offline behaviour:** jobs expire. A job whose intent has gone stale is never silently executed later — on
reconnect the backend offers pending jobs for explicit review unless the action is safe, recent, and policy
permits delayed execution.

## Mac tools and verification

PyObjC reaches the same Apple APIs Swift would, without requiring Xcode.

| Capability | API | Note |
|---|---|---|
| Open / focus app | `NSWorkspace` / `NSRunningApplication` | bundle-id allowlist; **no shell** |
| UI automation | `AXUIElement` | requires Accessibility consent |
| Window evidence | `CGWindowListCopyWindowInfo` | store title/bundle/pid; redact sensitive text |
| Screen capture | Screen Recording permission | optional; bounded to one window |
| Files | security-scoped, user-selected directories | no full-disk access by default |
| Terminal | predefined templates only | never chat-to-shell; exact command shown for R3 |
| Secrets | Keychain | the device private key never leaves it |

**Verification is state, not exit code.** `mac.open_app` succeeds only when a subsequent
`CGWindowListCopyWindowInfo` query reports the expected bundle id frontmost and `NSRunningApplication`
confirms the pid. A tool that returned `0` but changed nothing is a failure.

## Memory

| Tier | Stores | Retention | Retrieval |
|---|---|---|---|
| Working | current run, recent observations, pending approvals | hours/days | exact run id |
| Episodic | actions, results, evidence, corrections | configurable, summarized | time + goal + actor |
| Semantic | stable preferences, project facts, learned estimates | until corrected | SQL + vector hybrid |
| Source | permitted documents, messages, LMS objects | source policy / TTL | metadata filter + vector |

**Hybrid retrieval, in this order:** filter by tenant/scope/project/retention **in SQL first** → fetch exact
relational facts and recent episodes → run `pgvector` similarity **only across the permitted subset** →
rerank by relevance, recency, importance and source authority → return citations, never credentials.

Filtering before the vector search is what keeps retrieval both cheap and tenant-safe.

**Write policy:** the agent may *propose* a memory. A reducer decides whether it is ephemeral, needs
confirmation, conflicts with an existing fact, or should be discarded. A user correction creates a new version
and invalidates derived edges. Nothing permanent is inferred from a single message.

## Knowledge graph

Ordinary Postgres tables first: `entities`, `relations`, `entity_aliases`.

```
Pranav ─OWNS→ JARVIS X ─HAS_GOAL→ Hackathon Submission ─BLOCKED_BY→ Alexa Certification
```

**Provenance on every edge.** "Why do you believe this?" must always resolve to a source object.

## API surface

| Method / path | Purpose | Authorization |
|---|---|---|
| `POST /v1/chat/runs` | start a conversational run | user |
| `GET /v1/runs/{id}/events` | SSE timeline | owner |
| `GET/POST /v1/goals` | query / create | owner |
| `PATCH /v1/tasks/{id}` | status, estimate, due-time correction | owner + version (`If-Match`) |
| `GET /v1/goals/{id}/prediction` | failure probability + explanation | owner |
| `POST /v1/approvals/{id}/decision` | approve / edit / reject | owner (+ step-up for R3) |
| `POST /v1/agent/pause` | kill switch | owner |
| `POST /v1/devices/pair` | register public device key | owner + challenge |
| `POST /webhooks/{provider}` | provider ingress | provider signature, **not** user auth |

**WebSocket routes:** `$connect` authorizes the device token; `device.hello` negotiates protocol; then
`job.ack`, `job.progress`, `job.result`, `device.heartbeat`, `device.permission.changed`.

**Rules:** every mutation accepts `Idempotency-Key` and a version precondition where relevant; errors are
RFC-9457 problem objects carrying `correlation_id`; Dart/Python/TS clients generate from one OpenAPI document;
and the API **never** returns provider refresh tokens or raw stored email bodies to a client.

## Observability

One `correlation_id` connects: webhook → event → extraction → task → schedule → notification → approval →
Mac job → evidence.

Each step records model name, prompt version, input/output token counts, tool proposal, policy result and
verifier verdict — with source content and secrets redacted.

**If you cannot follow a single request across all nine hops in the logs, the system is not done.**
