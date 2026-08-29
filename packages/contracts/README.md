# contracts — SOURCE OF TRUTH

⚠️ **Freeze these before writing application code.** Everything downstream generates from them:
Python (Pydantic), Dart (Flutter clients) and TypeScript (Alexa skill).

## The five v1 contracts

| Contract | File | Purpose |
|---|---|---|
| `EventEnvelope` | `schemas/event_envelope.schema.json` | Every event entering the system |
| `Goal` | `schemas/goal.schema.json` | Deadline, outcome, priority, success metric |
| `Task` | `schemas/task.schema.json` | Estimate, remaining, dependencies, evidence, **version** |
| `ActionProposal` | `schemas/action_proposal.schema.json` | tool, args, expected evidence, risk, idempotency key, expiry |
| `Evidence` | `schemas/evidence.schema.json` | kind, uri, digest, redaction, verdict |

## Rules

- A change that removes or retypes a field is a **breaking change** — bump `schema_version`.
- CI runs an OpenAPI/JSON-Schema compatibility check and regenerates clients.
- No client ever talks to the database. Clients merge through these contracts.
