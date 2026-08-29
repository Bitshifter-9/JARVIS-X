# Cost model

**Target: under $2/month.** Every line item below is either free-tier or explicitly capped in code.

## Monthly run cost

| Item | Service | Free-tier limit | Our usage | Cost |
|---|---|---|---|---|
| Compute | Oracle Cloud Always Free | 4 ARM cores / 24 GB RAM, **perpetual** | 1 VM, ~2 GB used | **$0** |
| Database | Neon free tier | 0.5 GB storage, scale-to-zero, `pgvector` included | well under | **$0** |
| Queue | Postgres `SKIP LOCKED` | — | one table | **$0** |
| Scheduler | Postgres tick worker | — | 30 s loop | **$0** |
| TLS / edge | Caddy + Cloudflare | unlimited | 1 domain | **$0** |
| Evidence blobs | Cloudflare R2 | 10 GB storage, **zero egress fee** | screenshots | **$0** |
| Push | Firebase Cloud Messaging | unlimited | Android only | **$0** |
| Chat / planning LLM | Ollama on your Mac | — | electricity | **$0** |
| Telegram | Bot API | unlimited | alerts + approvals | **$0** |
| Gmail / Calendar | Google API | generous quotas | polling at 60 s | **$0** |
| Alexa (phase 3) | AWS Lambda | 1 M requests/mo | a few hundred | **$0** |
| **Extraction LLM** | **Claude Haiku 4.5** | — | ~50 extractions/day, ~1 k tokens each | **~$1** |
| Domain (optional) | any registrar | — | DuckDNS is free | **$0–12/yr** |
| | | | **Total** | **~$1–2/mo** |

## What we deliberately did not use, and what it would have cost

| Blueprint service | Approx. monthly | Replaced by |
|---|---|---|
| ECS Fargate ×3 services | $40–90 | One container on an Always Free VM |
| Aurora Serverless v2 | $45+ (bills at idle) | Neon free tier |
| NAT Gateway | $32 + data processing | No private subnet — Caddy on a public VM |
| API Gateway WebSocket | $1 + connection-minutes | FastAPI native WebSocket |
| Step Functions Standard | per state transition | `approvals` table |
| Cognito | free under 50 k MAU | Self-issued JWT (interface kept) |
| CloudFront + WAF | $5+ | Cloudflare free plan |
| | **≈ $120–170/mo avoided** | |

## Controls, in code not in a spreadsheet

- `JARVIS_ANTHROPIC_MONTHLY_BUDGET_USD` — the router refuses paid calls past it and falls back to `mac_relay`.
- `JARVIS_ENABLE_PAID_LLM=false` **must** produce a fully working system, only with weaker extraction.
  There is a test asserting this.
- Per-run: `MAX_STEPS`, `MAX_REPLANS`, `MAX_TOKENS_PER_RUN`, `MAX_RUN_SECONDS`.
- Per-user: daily notification and call caps.
- Circuit breakers on every connector.

## The four traps that generate surprise bills

1. **NAT Gateway data processing** — designed out; there is no private subnet.
2. **Aurora Serverless v2 idle ACUs** — Aurora is not used.
3. **An always-on Fargate task** — one container on a perpetually free VM.
4. **An unbounded agent loop** — `MAX_STEPS=8`, enforced outside the model.

The first three are architectural. The fourth is the one that bites agent projects, and it is the reason the
step budget lives in the harness rather than in a prompt.
