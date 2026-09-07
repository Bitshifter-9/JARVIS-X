# Setup — the detailed walkthrough

> **Already deployed?** The ordered, instance-specific checklist is [GO-LIVE.md](GO-LIVE.md) —
> accounts, apps, Google, Telegram, Mac, phone, Slack, WhatsApp, Twilio, Canvas, Alexa,
> updating, troubleshooting. This file is the general reference.

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

## Running it — three processes

```bash
make api          # the REST/WebSocket surface the apps talk to
make worker       # agent loop · connector polling · escalation · heartbeat
make scheduler    # fires deadline rungs (T-24h / 2h / 1h / 15m) into the queue
make video-worker # optional: the YouTube pipeline
```

**What runs without you asking:** the worker polls Gmail (and Classroom, on the same
consent) every `JARVIS_GMAIL_POLL_SECONDS` (60), Canvas on the same tick when configured,
Slack and OpenClaw arrive by webhook the instant they happen, the heartbeat checks every
goal every 30 minutes and alerts once per day per goal at risk, and the scheduler fires
each deadline rung once. WhatsApp and the phone call are *rungs* of the escalation ladder
— they fire only when the rungs before them were ignored, never on a timer of their own.

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
Mac-local actions works without your laptop — and the Mac helper and the phone dial
*out* to the server, so nothing changes for them except the URL.

### 6a. A VM — pick by what you can pay with

The stack wants **2 vCPU / 4 GB RAM minimum** (Chromium + embeddings + Postgres), Ubuntu
22.04, SSH. Everything below is identical once you have that.

| Option | Pays with | Cost | Notes |
|---|---|---|---|
| **Your Mac + `make tunnel`** | nothing | ₹0 | Works today, while the Mac is on. Cloudflare quick tunnel: no account, no card, no router setup. URL changes per restart (the script re-pins it). |
| **India-native VPS** — AIC Cloud, YouStable, RupeeCloud, CloudPe, Inservers | **UPI**, RuPay, net banking | ≈₹500–1,200/mo for 2 vCPU / 4–8 GB | The realistic "Mac closed" option from India. Monthly billing, Ubuntu KVM, root SSH — `make deploy` works unchanged. |
| **AWS, new account** — `make aws-launch` | Visa/Mastercard **debit works** (₹2 verification charge); RuPay reported to work | **₹0 through 31 Dec 2026** on a `t4g.small` (2 vCPU / 2 GB + swap), then ≈₹1,000/mo | The Free plan gives $100–200 credits for 6 months; separately, t4g.small has 750 free hours/month until end-2026. Disk + public IP come out of the credits. |
| Oracle Always Free | credit card only (Indian debit usually refused) | ₹0 | 4 OCPU / 24 GB ARM if the card check passes and the region has capacity. |
| Hetzner CX32 | card or PayPal | ≈₹650/mo | Excellent value; the payment wall is the problem. |
| DigitalOcean / Vultr / Linode | Visa/Mastercard **debit works** (international enabled); no UPI, no RuPay | ≈₹2,000/mo for 4 GB | Over budget. Free with a GitHub Student Pack credit if you qualify. |
| Render / Koyeb / Railway free tiers | no card | ₹0 | 512 MB containers — too small for this stack. |

**AWS, the one-command way:**

1. <https://aws.amazon.com/free> → *Create a free account* → choose the **Free plan** →
   card verification (₹2, refunded) → sign in to the console.
2. Top-right your name → **Security credentials** → *Access keys* → **Create access key**
   → *Command Line Interface* → download the CSV.
3. On the Mac: `aws configure` → paste the two keys, region `ap-south-1`, output `json`.
4. `make aws-launch` — creates the key pair, security group (22/80/443), a 30 GB
   Ubuntu 22.04 ARM instance, and prints the IP.
5. DuckDNS → set your name to that IP. Then `make deploy HOST=ubuntu@<ip>
   KEY=~/.ssh/jarvis-x-ap-south-1.pem DOMAIN=<name>.duckdns.org`.

Console instead of CLI: EC2 → *Launch instance* → Ubuntu 22.04 (**64-bit Arm**) →
**t4g.small** → create a key pair (download the `.pem`) → allow SSH/HTTP/HTTPS → storage
**30 GiB gp3** → Launch. Then step 5.

If you go Oracle: **Compute → Instances → Create**, shape **VM.Standard.A1.Flex** 4 OCPU /
24 GB, Ubuntu 22.04, save the SSH key, and add ingress on TCP **80** and **443** in the
subnet's security list.

### 6b. A name

Free and two minutes: <https://www.duckdns.org> → sign in (GitHub/Google) → type a
subdomain, e.g. `pranav-jarvis` → **add domain** → put the VM's public IP in the *current
ip* box → **update ip**. Your host is `pranav-jarvis.duckdns.org`. (Own a domain? An `A`
record at the VM's IP works the same.) Caddy issues the certificate itself; you never
touch TLS.

### 6c. Bring it up — one command from your laptop

```bash
make deploy HOST=ubuntu@<vm-ip> KEY=~/.ssh/<your-key> DOMAIN=<name>.duckdns.org
```

That installs Docker on the VM, **opens ports 80/443 in the VM's own iptables** (Oracle's
Ubuntu image blocks them even after you open the cloud security list — the usual reason
Caddy never gets a certificate), clones the repo, copies your `.env` with the domain
pinned and a real JWT secret, builds, migrates, starts everything, and waits for
`https://<domain>/healthz`. Run it again to update.

Or by hand, on the VM:

```bash
ssh ubuntu@<vm-ip>
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker
git clone https://github.com/Bitshifter-9/JARVIS-X.git && cd JARVIS-X
scp your-laptop:.env .            # or paste it; then set these three lines in it:
#   JARVIS_DOMAIN=jarvis.yourdomain.com
#   JARVIS_BASE_URL=https://jarvis.yourdomain.com
#   JARVIS_JWT_SECRET=$(openssl rand -hex 32)      ← a real one; the placeholder is refused
make prod-up                      # builds the image, migrates, starts api · worker · scheduler · caddy
make prod-logs                    # watch it come up; first start downloads Chromium + models
```

`https://jarvis.yourdomain.com/healthz` answers with the build SHA when it is up.

### 6d. Point the surfaces at it

| Surface | Where |
|---|---|
| Phone / Mac app | Sign-in screen → server URL → `https://jarvis.yourdomain.com` |
| Mac helper | `python -m macnode pair --api https://jarvis.yourdomain.com --email …` then `run` |
| Telegram webhook | `curl "https://api.telegram.org/bot$TOKEN/setWebhook?url=https://jarvis.yourdomain.com/webhooks/telegram&secret_token=$SECRET"` |
| Slack / WhatsApp / Alexa | the same host, paths in Step 7 |
| Google OAuth | add `https://jarvis.yourdomain.com/v1/connectors/google/callback` as a redirect URI |

### 6e. Updating

```bash
git pull && make prod-up          # rebuilds only what changed; migrations run first
```

Backups: `docker compose --env-file .env -f infra/compose/docker-compose.prod.yml exec postgres pg_dump -U jarvis jarvis | gzip > backup.sql.gz`
— nightly to R2 is a one-line cron once you have a bucket (`docs/COST.md`).

`docs/COST.md` has the full budget line by line.

---

### 6f. No VM yet? The Mac is the server — `make tunnel`

```bash
make stack     # terminal 1: api + worker + scheduler, one Ctrl-C stops all
make tunnel    # terminal 2: prints https://<random>.trycloudflare.com
```

The tunnel script pins the URL into `.env` (`JARVIS_BASE_URL`), re-registers the Telegram
webhook, and prints what to paste into Slack, WhatsApp, Alexa and Google. Restart
`make stack` once after the first run so the API reads the new base URL. Limits: the URL
changes each time the tunnel restarts, and it dies when the Mac sleeps — set *Prevent
automatic sleeping* in Energy settings if you want it up overnight.

## Step 6½ · The Mac as a hand, and "Hey Jarvis" — 10 min

Pair the Mac once (it generates a key in the Keychain; the private half never leaves):

```bash
uv run python -m macnode pair --api http://localhost:8000 --email you@example.com \
    --bundles com.google.Chrome net.whatsapp.WhatsApp com.apple.Safari com.apple.Notes
uv run python -m macnode run      # answers signed jobs: open, type, screenshot, WhatsApp…
```

Grant **Accessibility** and **Screen Recording** to the terminal that runs it (System
Settings → Privacy & Security) — typing and screenshots report `permission denied`
rather than failing silently until you do. Then, from the phone's **Devices** tab: tap
*Screenshot* and it arrives on Telegram and in the **Timeline**.

Always-on voice, like Siri:

```bash
uv sync --extra voice && uv pip install openwakeword
uv run python -m macnode voice --api http://localhost:8000 --email you@example.com
```

Say **"Hey Jarvis"**, wait for the tink, speak. Audio never leaves the Mac — Whisper runs
locally; only the text goes to `/v1/chat`, exactly like typing it.

## Step 6¾ · The phone as a hand — 1 min

Devices tab → **Pair this phone** → **Connect**. Jarvis can now open links and apps on the
phone and *draft* WhatsApp messages (you tap Send). Every job is signed by the server and
checked on the phone; **STOP** on the same card outranks any signature.

## Step 7 · Optional connectors — each independent, each free or nearly so

None of these is on the critical path. Every one is already implemented and tested; what
follows is only the account work that gives it live credentials. Skip any of them and the
system stays whole — an unconfigured channel is simply absent.

### 7a. Slack (phase 5.1) — 10 min

Slack's "Create new app" screen offers four starts. Pick **From a manifest** — it declares
exactly the permissions JARVIS needs and nothing more. (Ignore *AI agent* and *Starter
app*: they add Slack-side AI features and slash commands we do not use.)

1. <https://api.slack.com/apps> → **Create New App** → **From a manifest** → choose your
   workspace → **Next**.
2. Switch the editor to **JSON**, delete what is there, paste the contents of
   [`docs/slack-manifest.json`](slack-manifest.json) → **Next** → **Create**.
3. You land on **Basic Information**. Under *App Credentials*, copy the **Signing Secret**
   (click *Show*).
4. Left menu → **OAuth & Permissions** → **Install to Workspace** → **Allow**. Copy the
   **Bot User OAuth Token** (`xoxb-…`).
5. Put both in `.env` and restart the API:

   ```bash
   JARVIS_SLACK_BOT_TOKEN=xoxb-...
   JARVIS_SLACK_SIGNING_SECRET=...      # unset ⇒ every Slack request is rejected
   ```

6. **Now** the events — the manifest deliberately has none, because Slack's validator
   demands a Request URL alongside them, and JARVIS answers Slack's URL challenge only
   after checking the signature, which needs the secret from step 3. Left menu →
   **Event Subscriptions** → toggle **On** → *Request URL* =
   `https://<your-host>/webhooks/slack` — Slack shows **Verified ✓** within a second.
   Then open **Subscribe to bot events** → *Add Bot User Event* three times:
   `message.channels`, `message.groups`, `message.im` → **Save Changes**. Slack asks you
   to reinstall the app — do (OAuth & Permissions → Reinstall).

   > Local development has no public host. Run a tunnel first —
   > `cloudflared tunnel --url http://localhost:8000` prints an `https://…trycloudflare.com`
   > URL — and use that as `<your-host>`. The same trick serves Telegram and WhatsApp.

7. Invite the bot to a channel: in Slack, `/invite @JARVIS X`.
8. **Link your Slack user to your account**, or your messages are a stranger's and are
   dropped. Your id is under your profile → **⋯** → *Copy member ID* (looks like
   `U0…`). Then, signed in to JARVIS:

   ```bash
   curl -X POST https://<your-host>/v1/identities \
        -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
        -d '{"provider": "slack", "subject": "U0YOURID"}'
   ```

   (`POST /v1/auth/login` with your email and password returns the access token.)

**What happens now:** a message in a channel the bot is in — "the report is due Friday
5pm" — becomes an event, the worker extracts the deadline and creates a sourced task,
and the schedule ladder arms. Posting *to* Slack (`slack.post_message`) is R2: JARVIS
drafts, you approve, it posts.

### 7b. Google Classroom (phase 5.2)

No new credential — same OAuth client as Step 3. Enable the **Google Classroom API** in the
same project, then connect with the extra scope:

```
GET /v1/connectors/google/authorize?include_classroom=true
```

Requested separately on purpose: a school account whose administrator has not authorized
the app fails the *whole* consent screen if Classroom is bundled with Gmail.

### 7c. Canvas LMS (phase 5.3)

A free teacher instance at <https://canvas.instructure.com> → **Account → Settings → New
Access Token**.

```bash
JARVIS_CANVAS_BASE_URL=https://canvas.instructure.com
JARVIS_CANVAS_API_TOKEN=...
```

The base URL must be `https://` and a public host — a URL pointing inside the host network
is refused rather than dialled.

### 7d. WhatsApp (phase 5.4) — **submit the template early**

Meta takes 24–48 hours to approve a template, so do this before you need it.

1. <https://developers.facebook.com> → app → **WhatsApp** → get the test number's
   *Phone number ID* and a permanent access token.
2. **Message Templates** → new **Utility** template named `jarvis_deadline_alert` with two
   body parameters: `⏰ {{1}} — {{2}}`.
3. Webhook (for delivery receipts): `https://<your-host>/webhooks/whatsapp`, verify token of
   your choosing.

```bash
JARVIS_WHATSAPP_PHONE_NUMBER_ID=...
JARVIS_WHATSAPP_ACCESS_TOKEN=...
JARVIS_WHATSAPP_VERIFY_TOKEN=...
```

### 7e. Twilio voice (phase 5.5)

Trial account, verify **your own** number as a caller id, take the trial number.

```bash
JARVIS_TWILIO_ACCOUNT_SID=AC...
JARVIS_TWILIO_AUTH_TOKEN=...
JARVIS_TWILIO_FROM_NUMBER=+1...
JARVIS_MAX_CALLS_PER_DAY=3          # enforced in code, before a call is ever placed
```

Outbound calling to India is regulated (PLAN.md §13). The Android full-screen alarm is the
primary wake mechanism; the call is a bonus tier.

### 7f. Alexa (phase 4)

1. <https://developer.amazon.com/alexa/console/ask> → **Create Skill** → Custom, *Provision
   your own*, locale **en-IN**.
2. **JSON Editor** → paste `apps/alexa-skill/interaction-model.json` → Build.
3. **Endpoint** → HTTPS → `https://<your-host>/alexa`, certificate option *"a sub-domain of
   a domain that has a wildcard certificate"* (Caddy issues a real one).
4. **Account Linking** → against our own OAuth2 server; the exact fields are in
   `apps/alexa-skill/README.md`.
5. Copy the skill id:

```bash
JARVIS_ALEXA_SKILL_ID=amzn1.ask.skill....
JARVIS_ALEXA_VERIFY_SIGNATURE=true   # never false outside local development
```

Certification is not required to demo — the console simulator drives the live endpoint.

### 7g. OpenClaw adapter (phase 5.6)

The adapter runs as its own container with **no database credentials**. Give it one secret:

```bash
JARVIS_OPENCLAW_SHARED_SECRET=$(openssl rand -hex 32)
```

It posts to `/internal/connectors/openclaw/events` with `X-OpenClaw-Secret`. Everything it
says arrives as untrusted content, so it can produce an action *card* — never an action.

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
| 7 Connectors | Telegram covers every escalation rung | Slack, LMS deadlines, WhatsApp and a phone call as the last rung |

## Keeping secrets out of git

`.env` is gitignored, and there is a CI check that every Python package is committed —
added after a `.gitignore` rule silently kept two source directories out of the repo.
Before any commit that touches config:

```bash
git status --short          # .env must never appear
```
