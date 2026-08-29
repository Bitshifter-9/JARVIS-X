# mac-node — the Mac automation helper

Runs on your Mac under `launchd`. Opens **one outbound WSS** to the backend. No inbound port, ever.

## Responsibilities

- Validate every job: signature, `expires_at`, nonce replay, and its **own** local allowlist policy.
- Execute typed actions via PyObjC — never a shell string.
- Produce **evidence**, not exit codes.
- Relay `llm.generate` to local Ollama, so cloud inference costs nothing.
- Honour the menu-bar STOP: interrupt local jobs, drop the socket.

## Why PyObjC and not Swift

PyObjC calls the identical Apple frameworks — `NSWorkspace`, `NSRunningApplication`, `AXUIElement`,
`CGWindowListCopyWindowInfo`. It needs no Xcode, and Xcode is not currently installed. Swift becomes relevant
only for shipping a signed, notarized `.app`, which is a **packaging** concern, not a capability one.

## The helper never sees model text

It receives a validated action schema. That is the whole point of the split: a smaller permission surface,
independent logs, job cancellation, and predictable launch-at-login even when the UI is closed.
