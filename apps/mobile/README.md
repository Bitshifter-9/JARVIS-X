# mobile — Flutter control surface

Approvals, deadlines, evidence and the kill switch.

```bash
make seed          # demo@jarvis-x.dev / demo-password-12345
make api           # backend on :8000
make mobile        # app in Chrome on :8081
make mobile-test   # analyze + 11 Dart tests
```

## Platform status

| Target | State | Needs |
|---|---|---|
| **Web** | ✅ builds and runs | nothing — this is the development surface |
| **Android** | ⚠️ code ready, cannot build here | `cmdline-tools` + `flutter doctor --android-licenses` |
| macOS | ⏸ deferred to phase 3 | full Xcode |
| iOS | ⏸ out of scope | — |

Web is a *development* surface, not a shipping one: `flutter_secure_storage` degrades to
`localStorage` in a browser, which is not where a refresh token belongs. Android uses
Keystore-backed storage, which is.

## Pointing at the API

`JARVIS_API` at build time, or the field on the sign-in screen at runtime. An **Android
emulator reaches your Mac at `10.0.2.2`**, not `localhost` — the single most common
first-run failure, so it is the helper text under the field.

## Screens

| Screen | Shows |
|---|---|
| Today | Every goal with a deadline, its failure forecast, and ranked recovery options |
| Goals | The goal list with a severity dot and completion probability each |
| Approvals | What is waiting on you: action, risk, expiry, and whether the Mac must confirm too |
| Devices | Which Macs can act for you, online state, and how many apps each may open |

The app bar carries the **kill switch**. It cancels queued work and revokes device
sessions; it never deletes evidence, and the confirmation dialog says so.
