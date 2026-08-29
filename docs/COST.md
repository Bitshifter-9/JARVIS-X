# Cost model

**Envelope: ₹2,000/month (~$24).** Expected spend **₹0–700**. Worst case ₹1,825.

The headroom exists for one purpose: buying LLM reliability during judging, when a free-tier rate limit would
otherwise stall the demo.

## Monthly run cost

| Item | Choice | Free-tier limit | Monthly |
|---|---|---|---|
| Compute | Oracle Cloud Always Free ARM | 4 cores / 24 GB, **perpetual** | **₹0** |
| — if capacity unavailable | Hetzner CX32 (4 vCPU / 8 GB) | — | ₹650 |
| Database | Postgres 16 + `pgvector`, **same VM** | — | ₹0 |
| Backups | `pg_dump` → Cloudflare R2 | 10 GB, zero egress | ₹0 |
| Queue | Postgres `FOR UPDATE SKIP LOCKED` | — | ₹0 |
| Scheduler | Postgres tick worker | — | ₹0 |
| Approvals | `approvals` table | — | ₹0 |
| TLS / edge / DDoS | Caddy + Cloudflare | unlimited | ₹0 |
| Evidence blobs | Cloudflare R2 | 10 GB | ₹0 |
| Push | Firebase Cloud Messaging | unlimited | ₹0 |
| **LLM — chat / plan / classify** | **Groq** free tier | high daily request quota | **₹0** |
| **LLM — deadline extraction** | **Gemini** free tier | generous daily quota, native JSON schema | **₹0** |
| **LLM — overflow** | **OpenRouter** `:free`, then paid credit | `:free` models rate-limited; paid lifts them | **₹0–800** |
| Embeddings | MiniLM on the VPS, CPU | — | ₹0 |
| Telegram | Bot API | unlimited | ₹0 |
| Gmail / Calendar / Classroom / Canvas | provider quotas | generous | ₹0 |
| Alexa | AWS Lambda | 1 M requests/mo | ₹0 |
| WhatsApp | utility templates | ~₹0.15 each | ₹0–100 |
| Voice calls | Twilio, hard-capped | — | ₹0–200 |
| Domain | DuckDNS free, or ~₹900/yr | — | ₹0–75 |
| | | **Expected** | **₹0–700** |
| | | **Worst case** | **₹1,825** |

> ⚠️ Free-tier quotas move constantly. **Verify current limits in your own accounts before demo week.** The
> router treats a changed limit as a config edit, not a code change.

## Why co-locate the database

Postgres runs on the same VM as the API, not on a managed host in another region. A cross-region hop adds
hundreds of milliseconds to *every* query, and the agent loop makes many per run — that is the difference
between a 2-second and a 9-second event-to-alert path against a 10-second target. Managed Postgres is the
right answer at scale; at one VM it is a latency tax with no benefit.

Durability comes from nightly `pg_dump` to R2 rather than from managed replication. For a single-tenant
personal system that is the correct trade.

## What we did not use, and what it would have cost

| Blueprint service | Approx. monthly | Replaced by |
|---|---|---|
| ECS Fargate ×3 services | ₹3,500–7,500 | One container on a free VM |
| Aurora Serverless v2 | ₹4,000+ (bills at idle) | Postgres on the same VM |
| NAT Gateway | ₹2,800 + data processing | No private subnet |
| API Gateway WebSocket | ₹100 + connection-minutes | FastAPI native WebSocket |
| Step Functions Standard | per state transition | `approvals` table |
| Cognito | free under 50k MAU | Our own OAuth2 server (needed for Alexa linking anyway) |
| CloudFront + WAF | ₹450+ | Cloudflare free plan |
| Bedrock | per token | Groq + Gemini free tiers |
| | **≈ ₹11,000–15,000/mo avoided** | |

Every one of these maps 1:1 back to its AWS service. Migrating is a Compose swap plus a CDK stack, because the
contracts and module boundaries were kept identical.

## Controls, in code not in a spreadsheet

- `JARVIS_MONTHLY_BUDGET_INR` / `JARVIS_LLM_BUDGET_INR` — the router refuses paid calls past the cap and
  falls back to free providers.
- `JARVIS_ENABLE_PAID_LLM=false` **must** yield a fully working system. There is a test asserting it.
- Per-run: `MAX_STEPS`, `MAX_REPLANS`, `MAX_TOKENS_PER_RUN`, `MAX_RUN_SECONDS`.
- Per-user daily caps on notifications, WhatsApp templates and calls.
- Circuit breakers on every connector and every LLM provider.
- `llm_calls` records provider, model, prompt version, token counts and cost estimate for every single call.
  The dashboard shows spend-to-date.

## The four traps that generate surprise bills

1. **NAT Gateway data processing** — designed out; there is no private subnet.
2. **Aurora Serverless v2 idle ACUs** — Aurora is not used.
3. **An always-on Fargate task** — one container on a perpetually free VM.
4. **An unbounded agent loop** — `MAX_STEPS=8`, enforced by the harness, never by a prompt.

The first three are architectural and cannot recur. The fourth is the one that actually bites agent projects,
and it is why the step budget lives in the loop rather than in an instruction.
