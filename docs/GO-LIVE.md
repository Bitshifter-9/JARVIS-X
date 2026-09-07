# JARVIS X — go-live checklist for `pranav-jarvis.duckdns.org`

Everything below is specific to **your** deployment. The server is live at
`https://pranav-jarvis.duckdns.org` (AWS Mumbai, `t4g.small`, free through 31 Dec 2026).
Do the sections in order; each one says what you get at the end.

| Section | Time | What works afterwards |
|---|---|---|
| [1. Accounts and apps](#1-accounts-and-apps) | 5 min | Chat with memory on phone + Mac, Timeline, Settings for every variable |
| [2. Google](#2-google) | 5 min | Sign in with Google, Gmail → tasks, Calendar → free time, YouTube upload |
| [3. Telegram](#3-telegram) | 3 min | Alerts with Acknowledge/Snooze, approvals by button, chat with Jarvis by text |
| [4. The Mac as a hand](#4-the-mac-as-a-hand) | 5 min | Open apps/URLs, WhatsApp, screenshots to your phone, files, lock, music, "Hey Jarvis" |
| [5. The phone as a hand](#5-the-phone-as-a-hand) | 2 min | Open URLs/apps and draft WhatsApp on the phone from anywhere |
| [6. Slack](#6-slack) | 5 min | Deadlines in Slack → tasks; posts after approval |
| [7. WhatsApp alerts](#7-whatsapp-alerts) | 15 min + 1–2 days approval | Third rung of escalation |
| [8. Twilio call](#8-twilio-call) | 10 min | The last rung: a phone call, capped at 3/day |
| [9. Canvas / Classroom](#9-canvas--classroom) | 5 min | Assignments become tasks with no model involved |
| [10. Alexa](#10-alexa) | 20 min | "Alexa, ask Jarvis X what's due" |
| [11. Updating, backups, cost](#11-updating-backups-cost) | — | How to ship a change and not lose data |
| [12. Troubleshooting](#12-troubleshooting) | — | The five things that actually go wrong |

Every credential goes in **one** place: the app → **Settings** tab. It shows every
`JARVIS_*` variable the server is running with, grouped and typed; saving stores the
value in the **database** (`settings_overrides`), which every process — the API and
the workers — lays over its environment: the API at once, the workers on their next
tick (seconds). It survives deploys and restarts. Editing `.env` on your Mac and running
`make deploy …` sets the *defaults* underneath; a value saved in the app wins over it.

Where the data lives: **Postgres 16 + pgvector in a container on the AWS VM** (volume
`pgdata`), shared by every device. The Mac and the phone keep only their own session
token and local preferences; nothing of yours is stored on them. No Redis: Postgres is
also the queue and the scheduler (PLAN.md §5).

---

## 0. The chat, as of 2026-09-07

Streaming replies, Markdown with code blocks, threads (tap the title: switch, rename,
delete; ＋ for a new one), long-press a message for **Copy / Read aloud / Regenerate /
Edit and resend**, **Stop** while it writes, the ✨ menu to pick the model for this chat,
the 🎙 icon for a full-screen voice conversation (tap the orb to interrupt), an
"Agent · …" card under replies that acted (open it to see each tool call, its risk and
its evidence), and in Settings: *Custom instructions* (`persona extra`), *What Jarvis
remembers*, and *Diagnostics* — the card to open first when something feels wrong.

## 0½. Two new tabs (2026-09-07)

**Connections** — the truth about every integration, checked live: is the Google token
valid, when did it last scan, how many new items, is the worker alive, is the Groq key
accepted, is push wired. **Scan now** polls every connected account this second.
If it says *no Google account connected — nothing is scanned*, press **Connect Google**
there: the server's database is separate from your Mac's local one, so accounts must be
connected against `https://pranav-jarvis.duckdns.org` once.

**Train** — write who you are, what matters, the people in your life, how you want
replies and how you decide; Jarvis sees it on every turn. *Learn my writing style* reads
your sent mail once and writes a style card. Long-press a reply for 👍/👎 — a 👎 with a
reason becomes a rule. In chat, "From now on…", "Never…", "Remember that…" are kept
verbatim.

## 1. Accounts and apps

The production database started empty, so the first thing is an account.

1. **Mac app** — `/Applications/jarvis_x.app` (installed). Open it → server URL
   `https://pranav-jarvis.duckdns.org` → **Create account** (email + password) or
   **Sign in with Google** (after §2). You stay signed in: the session is kept in the
   Keychain and refreshed for 30 days of inactivity.
2. **Phone** — `~/Desktop/JARVIS-X.apk`. AirDrop/Drive it to the phone → open → *Install
   from this source* → open → same server URL → sign in with the same account.
3. **Both apps**: Settings tab → *Groq API key* — paste a **fresh** key from
   console.groq.com (the one in `.env` is rejected by Groq). Gemini works already.

You now have: Jarvis chat with memory, Today, Goals, Approvals, Timeline, Videos,
Devices, Settings — on both devices, one brain.

---

## 2. Google

Google needs to know the deployed callback URL once.

1. [console.cloud.google.com](https://console.cloud.google.com) → your project → **APIs &
   Services → Credentials** → your OAuth client → *Authorized redirect URIs* → **Add**:
   `https://pranav-jarvis.duckdns.org/v1/connectors/google/callback` → Save.
   (Keep the `http://localhost:8000/...` one for local development.)
2. **APIs & Services → Library**: make sure *Gmail API*, *Google Calendar API*, *YouTube
   Data API v3* are enabled — and *Google Classroom API* if you will use §9.
3. In the app → Settings → **Connect Google** → finish in the browser → the list updates
   itself. Do this on the Mac or the phone; the browser page bounces back to the app.

After this: **Sign in with Google** works on the sign-in screen; the worker polls Gmail
**every 4 hours** (`JARVIS_GMAIL_POLL_SECONDS=14400` — change it in Settings) and turns
deadline emails into sourced tasks; Calendar feeds free time into the prediction.

**Several Gmail accounts:** press *Connect Google* once per account, choosing a different
Google account each time. Each becomes its own row on the Settings → Accounts list, each
is polled, and each can be disconnected on its own. When Jarvis sends mail it uses the
first connected address unless you say which ("email from my college account").

---

## 3. Telegram

The webhook is already pointed at the server. Link your chat:

1. Open your bot in Telegram → send `/start` → it replies with your chat id.
2. App → Settings → *Telegram owner chat id* → paste → Save. (This also makes it the
   destination for screenshots and files from the Mac.)
3. Link it as an identity so *your* free text is a command channel:
   ```bash
   TOKEN=$(curl -fsS https://pranav-jarvis.duckdns.org/v1/auth/login \
     -H 'Content-Type: application/json' -d '{"email":"YOU","password":"PASS"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')
   curl -fsS -X POST https://pranav-jarvis.duckdns.org/v1/identities -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' -d '{"provider":"telegram","subject":"<chat id>"}'
   ```
4. Type "what's due today" to the bot. A stranger typing to it gets nothing.

---

## 4. The Mac as a hand

Two processes on the Mac; both dial *out*, nothing listens.

```bash
cd ~/Documents/Jarvis-x
uv run python -m macnode pair --api https://pranav-jarvis.duckdns.org --email YOU \
    --bundles com.google.Chrome net.whatsapp.WhatsApp com.apple.Safari com.apple.Notes com.apple.mail
uv run python -m macnode run          # leave running (a LaunchAgent later: see docs/SETUP.md 6½)
```

macOS will ask for **Accessibility** and **Screen Recording** for `python` the first time
a job needs them — allow both in System Settings → Privacy & Security, then re-run.

Then, from the phone: Devices → **Mac panel** → *Screenshot* — it arrives in Telegram and
in Timeline. Or just tell Jarvis: "open Chrome", "send Amma a WhatsApp saying I'll be late",
"screenshot my Mac", "lock my Mac", "what's on my clipboard", "play music".

**"Hey Jarvis" on the Mac:**
```bash
uv sync --extra voice && uv pip install openwakeword
uv run python -m macnode voice --api https://pranav-jarvis.duckdns.org --email YOU
```
Say "Hey Jarvis" → tone → speak → it answers in the server's neural voice. `--whisper small`
for better recognition (one-time 460 MB download).

---

## 5. The phone as a hand

App on the phone → **Devices → Pair this phone → Connect**. It stays connected while the
app is open and reconnects when you return. Then: "open YouTube on my phone", "draft a
WhatsApp to Ravi on my phone" — the phone opens WhatsApp with the text filled in; you
tap send. **STOP** on the same tab drops the connection instantly.

---

## 5½. Push notifications and "Hey Jarvis" on the phone — 15 min

**Push (the first rung of the ladder — free, instant):**

1. [console.firebase.google.com](https://console.firebase.google.com) → *Add project* (any
   name; Analytics off is fine).
2. *Add app* → **Android** → package name **`dev.jarvisx.jarvis_x`** → Register. You may
   skip the google-services.json download — JARVIS does not ship it in the APK.
3. Project settings → **General** → your Android app card: copy **API key**, **App ID**
   (`1:…:android:…`), **Sender ID** (= *Project number*), **Project ID**. App → Settings →
   *Push* section → paste all four → Save.
4. Project settings → **Service accounts** → *Generate new private key* → download the
   JSON. Either put its **path on your Mac** in `.env` as `JARVIS_FCM_CREDENTIALS_PATH`
   and run `make deploy …` — the deploy ships the file into the server's read-only
   `secrets/` directory and rewrites the variable to the path inside the container — or
   paste the JSON's **contents** into Settings → *fcm credentials path* (the whole JSON
   is accepted as the value).
5. Re-open the phone app once signed in: it initialises Firebase from those values,
   fetches its token and registers it — Settings → *Notifications* shows a `push`
   endpoint ending in the token's last 8 characters.

Alerts now reach the phone first, even with the app closed; Telegram is the next rung
only if you ignore it.

**"Hey Jarvis" with the screen off (Android):**

1. [console.picovoice.ai](https://console.picovoice.ai) → sign up (free for personal use)
   → copy your **AccessKey**. The wake-word model runs on the phone; the key only
   licenses it.
2. App → Settings → *picovoice access key* → paste → Save.
3. Settings → **Always listening — "Hey Jarvis"** → on. Allow the microphone and the
   notification. A small "JARVIS is listening" notification stays up while it is on.
4. Lock the phone. Say **"Jarvis"** → a tick → ask → it answers aloud in the neural
   voice. Same brain, same policy: anything effectful becomes an approval.

Battery: Porcupine is designed for this (well under 1% per hour); speech recognition
only runs after the wake word.

---

## 6. Slack

Steps 1–4 are done (app created from `docs/slack-manifest.json`, token + secret saved).

5. [api.slack.com/apps](https://api.slack.com/apps) → your app → **Event Subscriptions** →
   toggle **On** → *Request URL* `https://pranav-jarvis.duckdns.org/webhooks/slack` → wait
   for **Verified ✓**.
6. Same page → **Subscribe to bot events** → *Add Bot User Event* ×3: `message.channels`,
   `message.groups`, `message.im` → **Save Changes** → Slack asks you to **reinstall** → do.
7. Add the bot to the channels that matter. `/invite @JARVIS X` works on desktop; on
   **mobile it often says "you don't have permission"** — that is a Slack workspace
   restriction, not JARVIS. Two reliable ways instead:
   - **Channel → name at the top → Integrations → Add apps → JARVIS X.** This adds the
     app without needing invite permission.
   - Or from any device, in the channel type: `@JARVIS X` then Enter — Slack offers
     "Add to channel".
   If none appear, the app was probably **not reinstalled** after step 6 (Slack shows a
   yellow "reinstall your app" banner — click it and Authorize), or your workspace
   requires an **admin to approve apps** (ask the workspace owner, or use a workspace you
   admin). With the `channels:join` scope in the manifest, JARVIS can also add itself to a
   **public** channel once installed.
8. Link your Slack user: profile → ⋯ → *Copy member ID* (`U0…`), then with the `$TOKEN`
   from §3:
   ```bash
   curl -fsS -X POST https://pranav-jarvis.duckdns.org/v1/identities -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' -d '{"provider":"slack","subject":"U0YOURID"}'
   ```

Test: post "the report is due Friday 5pm" in that channel → Today tab shows the task.

---

## 7. WhatsApp alerts

The escalation ladder's third rung: push → Telegram → **WhatsApp** → alarm → call. Meta
takes 1–2 days to approve the template, so start now.

1. [developers.facebook.com](https://developers.facebook.com) → *My Apps* → **Create App**
   → *Other* → **Business** → name it → create.
2. Add product **WhatsApp** → *API Setup*. Note **Phone number ID** and generate a
   **permanent token** (Business settings → System users → add → *Generate token* with
   `whatsapp_business_messaging` and `whatsapp_business_management`).
3. **Message Templates** → *Create*: category **Utility**, name exactly
   `jarvis_deadline_alert`, language English, body: `⏰ {{1}} — {{2}}` (sample values:
   "CS401 Assignment 3", "due in 2 hours"). Submit.
4. App → Settings → WhatsApp section: *phone number id*, *access token*, and a *verify
   token* you invent (any string) → Save.
5. Meta → WhatsApp → *Configuration* → Webhook: **Callback URL**
   `https://pranav-jarvis.duckdns.org/webhooks/whatsapp`, **Verify token** = the string
   from step 4 → *Verify and save* → subscribe to `messages`.
6. Add your own number as a recipient (*API Setup → To*) while the number is a test
   number. In the app, add a notification endpoint of channel `whatsapp` for your number
   (Settings → Notifications) so the ladder can reach it.

---

## 8. Twilio call

The last rung, capped at 3 calls/day in code (`JARVIS_MAX_CALLS_PER_DAY`).

1. [twilio.com/try-twilio](https://www.twilio.com/try-twilio) → sign up (trial credit).
2. **Verified Caller IDs** → add your own mobile number → enter the OTP. On a trial,
   calls go only to verified numbers — which is exactly what you want.
3. **Phone Numbers → Buy a number** with *Voice* capability (trial credit covers it).
4. App → Settings → Twilio section: *account sid*, *auth token* (Console → Account
   Info), *from number* (the number you bought, E.164 `+1…`) → Save.
5. Add a notification endpoint of channel `call` with your verified number.

Test without waiting for a deadline: ignore a Telegram alert for a task due in 30
minutes; the ladder climbs to the call on its own, once.

---

## 9. Canvas / Classroom

**Canvas** (free, works today): [canvas.instructure.com/register](https://canvas.instructure.com/register)
→ *Teacher* → create a course with an assignment that has a due date → Account →
Settings → **New Access Token**. App → Settings → Canvas: base URL
`https://canvas.instructure.com`, the token → Save. Within a minute the assignment is a
task — no model involved, the due date is structured.

**Classroom** (needs your school to allow the app): enable *Google Classroom API* in the
Cloud project, then open
`https://pranav-jarvis.duckdns.org/v1/connectors/google/authorize?include_classroom=true`
signed in (or Settings → Connect Google with Classroom on). Same connector shape as Canvas.

---

## 10. Alexa

1. [developer.amazon.com/alexa/console/ask](https://developer.amazon.com/alexa/console/ask)
   → **Create Skill** → name *Jarvis X* → locale **English (IN)** → *Custom* → *Provision
   your own* → Create.
2. **Interaction Model → JSON Editor** → paste `apps/alexa-skill/interaction-model.json`
   → Save → **Build Model**.
3. **Endpoint** → *HTTPS* → Default region
   `https://pranav-jarvis.duckdns.org/alexa` → certificate type: *My development endpoint
   has a certificate from a trusted certificate authority* → Save.
4. Copy the **Skill ID** (Endpoint page) → App → Settings → Alexa → *skill id* → Save.
5. **Account Linking** → toggle on → Auth code grant; Authorization URI
   `https://pranav-jarvis.duckdns.org/oauth/authorize`, Access Token URI
   `https://pranav-jarvis.duckdns.org/oauth/token`, Client ID/Secret from
   `POST /v1/oauth/clients` (see `apps/alexa-skill/README.md`), scope `tasks.read
   tasks.write approvals.decide` → Save.
6. **Test** tab → *Development* → type "ask jarvis x what is due today". Link the
   account in the Alexa phone app when prompted.

---

## 11. Updating, backups, cost

- **Ship a change:** edit locally → `make deploy HOST=ubuntu@3.109.138.224
  KEY=~/.ssh/jarvis-x-ap-south-1.pem DOMAIN=pranav-jarvis.duckdns.org`. Sessions
  survive a deploy (the JWT secret is stable). ~4 minutes.
- **Logs:** `ssh -i ~/.ssh/jarvis-x-ap-south-1.pem ubuntu@3.109.138.224 'cd JARVIS-X && sudo make prod-logs'`
- **Changed `.env` on the server by hand?** `docker compose … restart` keeps the old
  environment; use `sudo make prod-up` (recreates the containers). Better: change it in
  the app's Settings, which needs neither.
- **Backup:** `ssh … 'cd JARVIS-X && sudo docker compose --env-file .env -f infra/compose/docker-compose.prod.yml exec -T postgres pg_dump -U jarvis jarvis' | gzip > jarvis-$(date +%F).sql.gz`
- **If the IP changes** (only if the instance is *stopped*): `make aws-launch` prints the
  new one; the next `make deploy` updates DuckDNS itself.
- **Cost:** ₹0 through Dec 2026 for the instance; disk + IP ≈ ₹500/mo from the sign-up
  credits. After December: ≈ ₹1,000/mo. AWS Budgets → set an alert at ₹1,500 (it also
  earns free-plan credits).
- **Security to do once:** IAM → *Users* → create a user with `AmazonEC2FullAccess` →
  access key → `aws configure` with it → then IAM → root → **delete the root access
  key**. Root keys can do anything, including billing.

---

## 12. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Google sign-in opens the browser, login works, nothing comes back | The app was pointed at a non-https server (LAN IP / localhost); Google only redirects to public https | Server URL = `https://pranav-jarvis.duckdns.org`; redirect URI registered (§2) |
| Google sign-in comes back to the app but it spins forever | Android froze the app's network while Chrome was in front; one failed poll ended the loop (fixed 2026-09-06 — polls now retry for 5 min; pending logins live in Postgres, so a restart mid-flow no longer loses them) | Install the current APK; retry |
| Asked to sign in on every launch (Mac) | Keychain data-protection needs a Developer-ID-signed app | Fixed in this build (classic keychain); reinstall `/Applications/jarvis_x.app` |
| Everyone signed out after a deploy | JWT secret regenerated | Fixed: the secret is saved locally and reused |
| Slack "didn't respond with the correct challenge" | Signing secret on the server differs from the app's, or the API wasn't restarted after saving it | Settings → Slack → paste again → Save (applies live), retry Verify |
| A Mac action stays "queued" | `macnode run` isn't running, or the bundle id isn't in the Mac's allowlist | Start the helper; re-pair with `--bundles …` |
| "Pair this phone" fails at the end / device never connects | (fixed 2026-09-07) the server had no device signing key in production, so fetching it returned 500 | Update, then **pair again** on the phone and the Mac app; the old "This phone" rows can be revoked in Devices |
| "Hey Jarvis" toggle shows an error | No Picovoice AccessKey saved | Settings → the key field under the toggle → Save → toggle again |
| Screenshot never arrives on Telegram | No owner chat id / identity linked | §3 |
| Groq errors in logs | Key invalid | New key at console.groq.com → Settings |
| `healthz` fine, `readyz` degraded | Postgres restarting | `sudo make prod-logs` on the VM; it self-heals |

## 13. Phase 10 — the Jarvis everyone films (routines, calls, eyes, hands, focus)

Everything here ships in the same deploy; the migration runs itself. What each piece
needs from you, in the order the app shows them:

**Home and Routines** — nothing to set up. Routines → switch on *Morning briefing*; pick
*App*, *Telegram* or *Call me* as the channel. *Call me* needs §8 (Twilio) and a `call`
endpoint (Settings → Notifications → your number).

**Approval by phone call** — with Twilio set up, an approval that nobody answers on push
or Telegram for `JARVIS_TWILIO_CALL_FOR_APPROVAL_AFTER_MINUTES` (default 5; 0 = never)
rings you and takes *yes / 1* or *no / 2*. Twilio fetches the script from your server, so
`JARVIS_BASE_URL` must be the public https URL. The daily call cap and quiet hours apply.

**Call Jarvis** — in the Twilio console, on your number, set *A call comes in* to
`POST https://<your-host>/webhooks/twilio/inbound`. Only a caller whose number is your
`call` endpoint gets an answer; each turn lands in the *Phone calls* thread.

**Eyes** — `mac.describe_screen` and `phone.camera` use Gemini for the picture; keep
`JARVIS_GEMINI_API_KEY` set. Nothing streams; one frame per action.

**Mac settings and gestures** — `mac.set_setting`: Wi-Fi and Dark Mode work out of the
box; Bluetooth needs `brew install blueutil`, brightness needs `brew install brightness`,
Do Not Disturb needs two Shortcuts named exactly *Jarvis DND On* and *Jarvis DND Off*
(one action each: *Set Focus*). Gestures: `uv sync --extra gestures` then
`python -m macnode gestures` — open palm stops speech, thumbs decide the one pending
approval, pinch mutes. Grant the camera permission when macOS asks.

**Phone hands** — Devices shows a card per paired phone: settings panels, the dialer, an
SMS draft, an app link. A *call* opens the dialer with the number; you press the button.

**Notification mirror** (opt-in) — Devices → *This phone* → *Mirror notifications*: the
app opens Android's *Notification access* page; enable JARVIS X there, come back, switch
it on. Titles from WhatsApp, Gmail, Slack, Messages, Phone and Calendar become events a
routine can react to ("when a message arrives from …"). *Include message text* is off
by default.

**Activity and the focus guard** (opt-in) — on the Mac: `python -m macnode run
--share-activity` (app name and window title every 30 s, kept 30 days). On the phone:
Devices → *Share which app is in front* (app names only). Then Settings → *Focus guard*:
`focus_guard_enabled=true`, the distracting-apps list, and the minutes threshold. The
guard only acts inside a focus block you started (say "start a focus session on X").

**What Jarvis noticed** — after 21:00 the day is distilled into a few profile suggestions
under Train. Nothing changes until you accept one.

**Weather** — Settings → *Owner* → `owner_location` (e.g. `Hyderabad`). No key needed.

**Standing permissions** — on an approval card for an upload, a calendar invite or a
screenshot, *Always allow this for 7 days* runs that tool without asking until it
expires; the list under Approvals revokes any of them in one tap.
