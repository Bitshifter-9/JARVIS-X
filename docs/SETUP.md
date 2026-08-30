# Setup — the detailed walkthrough

The code is done and CI is green. What remains needs your accounts.

Ordered by value per minute. **Step 1 is worth doing today.** Step 6 can wait weeks.
Every step ends with a command that proves it worked, so you never have to guess.

---

## Step 0 · Run it locally first (5 min)

Do this before anything else, so you have a working baseline to compare against.

```bash
cd ~/Documents/Jarvis-x
make bootstrap        # Postgres + pgvector, migrations, dependencies
make seed             # creates demo@jarvis-x.dev / demo-password-12345
make api              # leave running — http://localhost:8000/docs
```

In a second terminal:

```bash
make mobile           # the Flutter app opens in Chrome
```

Sign in with `demo@jarvis-x.dev` / `demo-password-12345`, set the API field to
`http://127.0.0.1:8000`. You should see a **Critical** risk card at ~3%.

**Verify:**
```bash
curl -s localhost:8000/healthz          # {"status":"ok",...}
make test                               # 500 passed
```

---

## Step 1 · LLM keys — 15 min, free, **highest value**

Right now the agent cannot think. Every provider reports `not configured`, and your
**graded 90% extraction accuracy is unmeasured** — the harness reports 26.7%, which is
only the no-deadline fixtures passing by default.

### 1a. Groq (chat, planning, classification)

1. Go to <https://console.groq.com>
2. Sign in with Google or GitHub. **No credit card.**
3. Left sidebar → **API Keys** → **Create API Key**
4. Name it `jarvis-x` → **Submit**
5. **Copy it now.** Groq shows it once.

### 1b. Google Gemini (deadline extraction)

1. Go to <https://aistudio.google.com/apikey>
2. Sign in with a Google account
3. **Create API key** → choose or create a project
4. Copy the key

Gemini handles extraction rather than Groq because it constrains decoding to a JSON
schema — the model *cannot* emit a shape the parser rejects. That is why it is the one
carrying a graded accuracy target.

### 1c. Wire them in

```bash
cd ~/Documents/Jarvis-x
cp .env.example .env
```

Edit `.env` and set exactly these two lines:

```bash
JARVIS_GROQ_API_KEY=gsk_your_key_here
JARVIS_GEMINI_API_KEY=AIza_your_key_here
```

Leave `JARVIS_ENABLE_PAID_LLM=false`. There is a test asserting the system works without it.

**Verify — this is the moment your graded metric becomes real:**

```bash
make test-live
```

It runs 30 curated fixtures through the real model and prints:

```
extraction accuracy: 93.3% (30 cases)
```

- **≥90%** → the metric in `docs/DEMO-RUNBOOK.md` is earned. Record the number.
- **<90%** → failures are listed by fixture id. The prompt is
  `packages/prompts/deadline_extraction.md`; edit it, bump the version in its header,
  re-run. The fixtures are in `tests/fixtures/deadline_extraction.json`.

**If it errors instead:** `all providers failed for extract — gemini: not configured`
means the `.env` was not picked up. Confirm you are running from the repo root and that
the file is named `.env`, not `.env.txt`.

---

## Step 2 · Telegram bot — 10 min, free

This unblocks the demo's best moment: an alert on your phone with **Approve / Reject**
buttons that actually dispatches a real action.

### 2a. Create the bot

1. Open Telegram, search for **@BotFather** (blue checkmark).
2. Send `/newbot`
3. It asks for a **display name** — anything, e.g. `JARVIS X`
4. It asks for a **username** — must be unique and **end in `bot`**, e.g.
   `pranav_jarvisx_bot`
5. It replies with:
   ```
   Use this token to access the HTTP API:
   8123456789:AAF-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
   Copy that token.

### 2b. Find your chat id

A chat id is not an account. It gets *linked* to your JARVIS account in the `identities`
table, and an unlinked chat gets nothing useful back — that is deliberate.

1. In Telegram, open your new bot and press **Start** (or send it any message).
2. In a terminal:

```bash
TOKEN=8123456789:AAF-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
curl -s "https://api.telegram.org/bot$TOKEN/getUpdates" | python3 -m json.tool
```

3. Find `"chat": {"id": 5551234567, ...}`. That number is your chat id.

**If `result` is an empty list:** you have not messaged the bot yet, or a webhook is
already set (a webhook and `getUpdates` are mutually exclusive). Clear it and retry:
```bash
curl -s "https://api.telegram.org/bot$TOKEN/deleteWebhook"
```

### 2c. Wire it in

```bash
JARVIS_TELEGRAM_BOT_TOKEN=8123456789:AAF-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
JARVIS_TELEGRAM_OWNER_CHAT_ID=5551234567
JARVIS_TELEGRAM_WEBHOOK_SECRET=pick-any-long-random-string
```

Generate the secret with `openssl rand -hex 24`. Telegram echoes it on every webhook call,
and that is what distinguishes a real update from anyone who discovers your URL.

**Verify outbound (no public URL needed):**

```bash
curl -s "https://api.telegram.org/bot$TOKEN/sendMessage" \
  -d chat_id=5551234567 -d text="JARVIS X is wired up."
```
You should get the message in Telegram.

### 2d. Inbound buttons need a public HTTPS URL

Telegram will not deliver to `localhost`. A free tunnel gives you one:

```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:8000
```

It prints something like `https://random-words-1234.trycloudflare.com`. Then:

```bash
PUBLIC=https://random-words-1234.trycloudflare.com
SECRET=your-webhook-secret
curl -s "https://api.telegram.org/bot$TOKEN/setWebhook?url=$PUBLIC/webhooks/telegram&secret_token=$SECRET"
```

**Verify:**
```bash
curl -s "https://api.telegram.org/bot$TOKEN/getWebhookInfo" | python3 -m json.tool
```
Look for your URL and `"pending_update_count": 0`. If `last_error_message` shows
something, the tunnel or the API is not reachable.

> The free tunnel URL changes every restart. Re-run `setWebhook` each time, or use a
> named Cloudflare tunnel once you deploy (step 6).

**Full round trip:** with `make api` and the tunnel running, create an action needing
approval and you will get a card in Telegram. Pressing **Approve** dispatches it —
`tests/integration/test_telegram.py` covers exactly this path.

---

## Step 3 · Google (Gmail + Calendar) — 25 min, free

Until this, deadlines come from fixtures. After it, they come from your real inbox.

### 3a. Create the project

1. <https://console.cloud.google.com> → project dropdown (top left) → **New Project**
2. Name: `jarvis-x` → **Create** → switch to it

### 3b. Enable the two APIs

1. **APIs & Services → Library**
2. Search **Gmail API** → **Enable**
3. Search **Google Calendar API** → **Enable**

### 3c. Consent screen

1. **APIs & Services → OAuth consent screen**
2. User type: **External** → **Create**
3. App name `JARVIS X`, your email for both support and developer contact → **Save and continue**
4. Scopes → **Save and continue** (the app requests them at runtime; you do not need to list them here)
5. **Test users** → **Add users** → add your own Gmail address → **Save and continue**

> **Leave it in "Testing".** Publishing triggers Google's verification review — weeks of
> back-and-forth for a project at this stage. Testing mode works fully for up to 100 users
> you list yourself.

### 3d. Create the credential

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
2. Application type: **Web application**
3. Name: `jarvis-x-local`
4. Under **Authorized redirect URIs** → **Add URI**, paste **exactly**:
   ```
   http://localhost:8000/v1/connectors/google/callback
   ```
5. **Create** → copy the **Client ID** and **Client secret**

The path must match character for character — Google compares it exactly, and a trailing
slash will fail with `redirect_uri_mismatch`.

### 3e. Wire it in

```bash
JARVIS_GOOGLE_CLIENT_ID=1234-abcd.apps.googleusercontent.com
JARVIS_GOOGLE_CLIENT_SECRET=GOCSPX-your_secret
JARVIS_BASE_URL=http://localhost:8000
```

### 3f. Connect

Restart `make api`, then get a sign-in link:

```bash
TOKENS=$(curl -s localhost:8000/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@jarvis-x.dev","password":"demo-password-12345"}')
ACCESS=$(echo "$TOKENS" | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

curl -s localhost:8000/v1/connectors/google/authorize \
  -H "Authorization: Bearer $ACCESS" | python3 -c 'import json,sys;print(json.load(sys.stdin)["authorization_url"])'
```

Open that URL in a browser, sign in, accept. You will land on a **"Google connected"**
page listing exactly what was granted.

**Verify:**
```bash
curl -s localhost:8000/v1/connectors -H "Authorization: Bearer $ACCESS" | python3 -m json.tool
```
You should see `"provider": "gmail"`, `"status": "active"` and the read scopes. Access
tokens are never returned — there is a test asserting that.

**To disconnect and delete everything it fetched:**
```bash
curl -s -X POST "localhost:8000/v1/connectors/<ID>/disconnect" -H "Authorization: Bearer $ACCESS"
```
Deletion is the default. A connector that keeps your mail after you disconnect it has not
really been disconnected.

> Only **read** scopes are requested. Sending email is a separate consent, requested when
> you enable a feature that needs it.

---

## Step 4 · Xcode — ~1 hour download, free

Unblocks the Flutter **macOS** Control Center. Android and web do not need it.

**Start the download first and do steps 1–3 while it runs.**

1. Open the **App Store** → search **Xcode** → **Get** (~15 GB)
2. When it finishes, open Xcode once and accept the licence
3. Then:

```bash
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
sudo xcodebuild -runFirstLaunch
sudo gem install cocoapods
```

**Verify:**
```bash
flutter doctor          # "Xcode - develop for iOS and macOS" should be ✓
cd apps/mobile && flutter build macos --debug
```

---

## Step 5 · Firebase — 20 min, free

Unblocks Android push and the lock-screen deadline card.

1. <https://console.firebase.google.com> → **Create a project** (or reuse the `jarvis-x`
   project from step 3 — reusing keeps things tidy)
2. Google Analytics: **not needed**, turn it off
3. On the project home, click the **Android** icon
4. **Android package name** — must match exactly:
   ```
   dev.jarvisx.jarvis_x
   ```
5. **Register app** → **Download `google-services.json`**
6. Put it at:
   ```
   apps/mobile/android/app/google-services.json
   ```
7. **Project settings** (gear) → **Service accounts** → **Generate new private key** →
   save the JSON **outside the repo**, e.g. `~/.jarvis/fcm-service-account.json`
8. In `.env`:
   ```bash
   JARVIS_FCM_CREDENTIALS_PATH=/Users/pranavkumar/.jarvis/fcm-service-account.json
   ```

`google-services.json` is not a secret and belongs in the repo. **The service-account key
is** — it can send push to every install of your app. Keep it out of git.

**Verify:**
```bash
cd apps/mobile && flutter build apk --debug     # should still succeed
```

---

## Step 6 · Deploy — ~1 hour, ₹0

Only needed when you want it running while your Mac is closed. Everything except
Mac-local actions works without your laptop.

1. <https://cloud.oracle.com> → sign up → **Compute → Instances → Create**
2. Shape: **VM.Standard.A1.Flex**, Ampere ARM, **4 OCPU / 24 GB** — this is Always Free,
   perpetually, not a trial
3. Image: **Ubuntu 22.04**. Save the SSH key it offers.
4. **Networking → Virtual Cloud Network → Security List** → add ingress on TCP **443**
5. On the VM:
   ```bash
   sudo apt update && sudo apt install -y docker.io docker-compose-plugin git
   sudo usermod -aG docker $USER && newgrp docker
   git clone https://github.com/Bitshifter-9/JARVIS-X.git && cd JARVIS-X
   # copy your .env across, then:
   docker compose -f infra/compose/docker-compose.dev.yml up -d
   ```
6. Put Caddy in front for automatic TLS, or run a named Cloudflare tunnel so the URL stops
   changing.

> **If the region has no ARM capacity** — common — try an adjacent region, or use Hetzner
> CX32 at about ₹650/month. The Compose file is identical either way.

`docs/COST.md` has the full budget line by line.

---

## What each step unlocks

| Step | Without it | With it |
|---|---|---|
| 1 LLM keys | The agent cannot think; 90% metric unmeasured | It thinks, and the graded number is real |
| 2 Telegram | Approvals only via `curl` | Approve on your phone — the demo moment |
| 3 Google | Deadlines from fixtures | Deadlines from your actual inbox |
| 4 Xcode | Android + web only | The macOS Control Center |
| 5 Firebase | No push | Lock-screen card, alarm-style wake |
| 6 Deploy | Runs while your Mac is awake | Runs while it is closed |

## Keeping secrets out of git

`.env` is gitignored, and there is a CI check that every Python package is committed —
added after a `.gitignore` rule silently kept two source directories out of the repo.
Before any commit that touches config:

```bash
git status --short          # .env must never appear
```
