# Setup — what only you can do

The code is done and CI is green. What remains needs your accounts, and nothing here
needs more code from me.

Ordered by value per minute spent. Step 1 is worth doing today; step 5 can wait weeks.

---

## 1. LLM keys — 15 min, free · **do this first**

Right now the agent cannot think. Every provider reports `not configured`, and the
**graded 90% extraction accuracy is unmeasured** — the harness runs and reports 26.7%,
which is only the no-deadline fixtures passing by default.

| Provider | Where | Free tier |
|---|---|---|
| **Groq** | <https://console.groq.com/keys> | Generous; no card. Used for chat, planning, classification. |
| **Gemini** | <https://aistudio.google.com/apikey> | Generous; no card. Used for deadline extraction — JSON-schema-constrained decoding is why. |
| OpenRouter | <https://openrouter.ai/keys> | Optional overflow. Skip for now. |

```bash
cp .env.example .env
# paste the two keys into JARVIS_GROQ_API_KEY and JARVIS_GEMINI_API_KEY
make test-live        # the real accuracy number against 30 curated fixtures
```

If it prints ≥90%, that metric is earned. If it prints less, the failures are listed by
fixture id and the prompt is at `packages/prompts/deadline_extraction.md` — edit it, bump
the version in its header, re-run.

Leave `JARVIS_ENABLE_PAID_LLM=false`. There is a test asserting the system works without it.

---

## 2. Telegram bot — 5 min, free

Unblocks the approval round trip: an alert with Approve/Reject buttons that actually
dispatches an action.

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. Message your own new bot once, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `message.chat.id`.
3. Put both in `.env` as `JARVIS_TELEGRAM_BOT_TOKEN` and `JARVIS_TELEGRAM_OWNER_CHAT_ID`.

The chat id is not an account — it is linked to yours in the `identities` table, and an
unlinked chat gets nothing useful. That is deliberate.

For inbound buttons the bot needs a public HTTPS URL:

```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:8000        # prints a https://…trycloudflare.com URL
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=<THAT_URL>/webhooks/telegram&secret_token=<PICK_A_SECRET>"
# put the same secret in .env as JARVIS_TELEGRAM_WEBHOOK_SECRET
```

---

## 3. Google OAuth — 20 min, free

Unblocks real Gmail and Calendar ingestion. Until this, deadlines come from fixtures.

1. <https://console.cloud.google.com> → new project.
2. **APIs & Services → Library** → enable **Gmail API** and **Google Calendar API**.
3. **OAuth consent screen** → External → add yourself under **Test users**.
   (Staying in "Testing" is fine and avoids Google's verification review entirely.)
4. **Credentials → Create → OAuth client ID → Web application**.
   Authorized redirect URI: `http://localhost:8000/v1/connectors/google/callback`
5. Copy the client id and secret into `.env`.

Read scopes only. Send scopes are requested separately, when you turn on a feature that
needs them.

---

## 4. Xcode — ~1 hour download, free

Unblocks the Flutter **macOS** Control Center (phase 3.1). Android and web do not need it.

```bash
# Install Xcode from the Mac App Store (~15 GB), then:
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
sudo xcodebuild -runFirstLaunch
sudo gem install cocoapods
flutter doctor            # Xcode should turn green
```

Do the download in the background while you work on steps 1–3.

---

## 5. Firebase — 15 min, free

Unblocks Android push and the live deadline card (phases 2.8 / 3.3).

1. <https://console.firebase.google.com> → add project → add an **Android** app.
2. Package name: `dev.jarvisx.jarvis_x`
3. Download `google-services.json` → put it in `apps/mobile/android/app/`.
4. **Project settings → Service accounts → Generate new private key** → save the JSON
   outside the repo and set `JARVIS_FCM_CREDENTIALS_PATH` to its path.

`google-services.json` is not a secret, but the service-account key is. Keep it out of git.

---

## 6. Deploy — ~1 hour, ₹0

Only needed when you want it running while your Mac is closed.

1. <https://cloud.oracle.com> → **Always Free** ARM VM (4 cores / 24 GB), Ubuntu 22.04.
   If your region is out of capacity, try an adjacent one, or use Hetzner CX32 (~₹650/mo).
2. On the VM: install Docker, clone the repo, copy `.env`, `docker compose up -d`,
   `alembic upgrade head`.
3. Point Caddy at it for automatic TLS, or front it with a Cloudflare Tunnel.

`docs/COST.md` has the full line-by-line budget.

---

## Running it locally, today

```bash
make bootstrap     # Postgres + pgvector, migrations, deps
make seed          # demo@jarvis-x.dev / demo-password-12345
make api           # http://localhost:8000/docs
make mobile        # the Flutter app in Chrome
make test          # 500 tests
```

Android:

```bash
cd apps/mobile && flutter build apk --release
# or: flutter run -d <device>   (the app reaches your Mac at 10.0.2.2 from an emulator)
```

## What each step actually unlocks

| Step | Without it | With it |
|---|---|---|
| 1 LLM keys | No extraction, no planning; the 90% metric is unmeasured | The agent thinks; the graded number becomes real |
| 2 Telegram | Approvals only via API calls | The demo's approve-on-your-phone moment |
| 3 Google | Deadlines from fixtures | Deadlines from your actual inbox |
| 4 Xcode | Android + web only | The macOS Control Center |
| 5 Firebase | No push | Lock-screen deadline card, alarm-style wake |
| 6 Deploy | Runs while your Mac is awake | Runs while it is closed |
