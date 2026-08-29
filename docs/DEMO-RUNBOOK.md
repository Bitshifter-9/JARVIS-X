# Demo runbook — seven minutes

The demo is a *closed loop*, shown once, end to end. Resist the urge to tour features.

| Time | Live action | What it proves |
|---|---|---|
| 0:00 | One sentence + the architecture view | Not a chatbot — a closed-loop personal operating system |
| 0:30 | A real deadline arrives from Gmail | Proactive event ingestion |
| 1:10 | Task appears with **source evidence** and a goal dependency | Explainable memory and a real goal graph |
| 1:50 | Prediction says the plan will miss; three recovery options appear | The differentiator |
| 2:40 | Run **simulation** — exact Mac and message actions previewed | Safety and transparency |
| 3:20 | Approve on the phone; the Mac opens the project and required docs | Cross-device human control |
| 4:10 | Verifier shows foreground bundle id + pid + DOM evidence | The agent *proves* success |
| 4:45 | An injected malicious email is blocked by policy | Security maturity |
| 5:20 | Ignore the compressed alert → phone, then Telegram escalation, **once** | Durable proactivity |
| 6:00 | Control Center timeline updates across surfaces | Multi-surface continuity |
| 6:35 | Metrics, kill switch, roadmap | Production credibility |

## The two moments that win it

**1:50 — the prediction.** Read the generated sentence aloud, from real rows:

> "You have 170 usable minutes, while the 80th-percentile remaining work is 260 minutes. Removing the optional
> Alexa animation and postponing the knowledge-graph visualization raises predicted completion probability
> from 34% to 78%."

**2:40 → 3:20 — simulate, then execute the *same hashed plan*.** Show the hash before and after. Nothing about
the plan changed between preview and execution, and that is checkable.

## Resilience

- **DEMO CLOCK** maps one hour to one minute but runs the **real scheduler path** — not a mocked timer. Say so.
- Keep signed fixtures for provider delays; state clearly when a fixture is being replayed.
- Record a 90-second backup video showing the real phone, Mac and (if built) Alexa device.
- One-click reset clears **only** demo-tenant rows. Never production data, never evidence from another tenant.

## Pre-flight checklist

- [ ] Mac node paired, connected, heartbeat green
- [ ] Ollama running, model pulled and **warm** (send one throwaway prompt — a cold load costs 20 s on stage)
- [ ] Android device on the same network, FCM token registered, notifications permitted
- [ ] Telegram bot responding, owner chat id allowlisted
- [ ] Accessibility + Screen Recording permissions granted **and tested from a clean macOS user account**
- [ ] Demo tenant seeded; one-click reset verified
- [ ] Adversarial fixture loaded and confirmed to produce zero effectful actions
- [ ] Backup video on the desktop, not in the cloud
- [ ] Rehearsed twice, end to end, without notes

## If something breaks on stage

State what broke, show the audit timeline entry for it, and continue. A system that records its own failure
with a correlation id is more convincing than one that never fails — and the judges have already seen the
architecture that makes that possible.
