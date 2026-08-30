# mac-node — the Mac automation helper

Runs on your Mac. Opens **one outbound WebSocket** to the backend. No inbound port, ever.

```bash
python -m macnode pair --api https://jarvis.example.com --email you@example.com \
                       --bundles com.google.Chrome com.microsoft.VSCode
python -m macnode run
```

Pairing generates an ECDSA P-256 key whose private half is stored in the **macOS Keychain**
and never leaves this machine. The backend keeps only the public half.

## It is optional, by design

The backend is cloud-resident and never depends on this helper. With the Mac offline,
ingestion, extraction, prediction, scheduling, escalation, approvals and **cloud browser
automation** all continue. This helper adds exactly one thing: actions that can only
happen on a Mac.

| Tier B — what this helper owns |
|---|
| `mac.open_app`, `mac.focus_app` |
| AX UI automation (`AXUIElement`) |
| Window evidence (`CGWindowListCopyWindowInfo`) |
| Scoped local file access |
| Predefined command templates (never chat-to-shell) |

## It refuses work on its own authority

The server already decided a job is allowed. The helper checks again anyway, because that
decision crossed a network to reach a process holding real macOS permissions. Every job is
validated for **signature, expiry, nonce replay and this Mac's own allowlist** before
anything happens — in that order, cheapest and most decisive first.

The local allowlist is the check that matters most. If the backend is ever wrong — a bug,
a compromise, a stale policy version — this is what stops it opening Terminal. There is a
test for exactly that: a perfectly signed job from the real server is refused because
`com.apple.Terminal` is not in this Mac's list.

The menu-bar **STOP** outranks a valid signature.

## It reports observations, never verdicts

The helper says which pid it saw and which bundle id is frontmost. The server's verifier
decides whether that satisfies what the action required. A device must not be able to
declare its own success — and results are signed with the device key, so nothing else
reaching the socket can fabricate one.

## Why PyObjC and not Swift

PyObjC calls the identical Apple frameworks — `NSWorkspace`, `NSRunningApplication`,
`AXUIElement`, `CGWindowListCopyWindowInfo`. It needs no Xcode, and Xcode is not currently
installed. Swift becomes relevant only for shipping a signed, notarized `.app`, which is a
**packaging** concern, not a capability one.

Every OS call sits behind `MacAdapter`, so the guard and executor logic is tested in CI on
any machine; `FakeMacAdapter` scripts the awkward cases — an app that launches but will not
come forward, an app that is not installed.
