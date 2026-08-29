# mac-node — the Mac automation helper

Runs on your Mac under `launchd`. Opens **one outbound WSS** to the backend. No inbound port, ever.

## It is optional, by design

The backend is cloud-resident and never depends on this helper. With the Mac offline, ingestion, extraction,
prediction, scheduling, escalation, approvals and **cloud browser automation** all continue. This helper adds
exactly one thing: actions that can only happen on a Mac.

| Tier B — what this helper owns |
|---|
| `mac.open_app`, `mac.focus` |
| AX UI automation (`AXUIElement`) |
| Window and screen evidence (`CGWindowListCopyWindowInfo`, Screen Recording) |
| Scoped local file access |
| Predefined command templates (never chat-to-shell) |
| Wake-word / push-to-talk voice capture |

## Responsibilities

- Validate every job: signature, `expires_at`, nonce replay, and its **own** local allowlist.
- Execute typed actions via PyObjC — never a shell string.
- Produce **evidence**, not exit codes.
- Honour the menu-bar STOP: interrupt local jobs, drop the socket.
- Refuse work whose intent has gone stale rather than executing it late.

## Why PyObjC and not Swift

PyObjC calls the identical Apple frameworks — `NSWorkspace`, `NSRunningApplication`, `AXUIElement`,
`CGWindowListCopyWindowInfo`. It needs no Xcode, and Xcode is not currently installed. Swift becomes relevant
only for shipping a signed, notarized `.app`, which is a **packaging** concern, not a capability one.

## The helper never sees model text

It receives a validated action schema. That is the point of the split: a smaller permission surface,
independent logs, job cancellation, and predictable launch-at-login even when the UI is closed.
