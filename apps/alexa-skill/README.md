# JARVIS X — Alexa custom skill

Locale **en-IN**. Seven custom intents plus the required built-ins.

The skill has **no Lambda**. `interaction-model.json` is uploaded to the developer
console; the endpoint is the running API. That is a deliberate substitution of the
blueprint's ASK SDK Lambda (PLAN.md §5): Alexa supports an HTTPS endpoint, and this
removes a second language, a second deployment and an AWS account from the critical path.
What the ASK SDK would have done for us — request authenticity — lives in
`apps/api/jarvis/connectors/alexa/verify.py` and is covered by `tests/unit/test_alexa.py`.

## Endpoint

| Setting | Value |
|---|---|
| Service endpoint type | HTTPS |
| Default region | `https://<your-host>/alexa` |
| Certificate | *My development endpoint is a sub-domain of a domain that has a wildcard certificate from a certificate authority* — Caddy issues a real Let's Encrypt certificate, so this is true. |

Set `JARVIS_ALEXA_SKILL_ID` to the skill id from the console. A request naming any other
skill id is rejected before it is interpreted.

## Account linking

Against our own OAuth2 authorization server — the same one the phone and the Mac use.

| Field | Value |
|---|---|
| Authorization URI | `https://<your-host>/oauth/authorize` |
| Access token URI | `https://<your-host>/oauth/token` |
| Client id / secret | from `POST /oauth/clients` (see `docs/SETUP.md`) |
| Client authentication scheme | HTTP Basic |
| Scope | `tasks.read tasks.write approvals.decide` |

Alexa then sends `session.user.accessToken` on every request; the skill resolves it to a
user exactly as any other bearer token. **No token, no capability** — an unlinked request
gets a LinkAccount card and nothing else.

## Reminders and Proactive Events

Both need consent that is separate from account linking:

* **Reminders** — ask for `alexa::alerts:reminders:skill:readwrite` on the permissions
  page. A user who declines still has a fully working skill; the escalation ladder
  (push → Telegram → WhatsApp → call) never depended on Alexa.
* **Proactive Events** — client-credentials token from
  `https://api.amazon.com/auth/O2/token`, then `POST /v1/proactiveEvents`. Use
  `AMAZON.MessageAlert.Activated` for deadline escalation. Per-day caps are the same
  caps the notification policy already enforces; Alexa is one more channel, not an
  exception to the ladder.

## Certification checklist

- [ ] Invocation name is two or more words and is not a wake word.
- [ ] Every sample utterance parses; the simulator resolves each of the seven intents.
- [ ] `AMAZON.HelpIntent`, `AMAZON.StopIntent`, `AMAZON.CancelIntent` and
      `AMAZON.FallbackIntent` are all handled.
- [ ] Account linking is exercised end to end: link → authorized intent → unlink.
- [ ] Privacy policy and terms URLs are reachable (`docs/THREAT-MODEL.md` §privacy is the
      source text).
- [ ] The skill does not collect personal information through speech.
- [ ] Approving an action by voice is confirmed by Alexa first, and an R3 action that
      needs a local Mac confirmation is refused by voice — read that back aloud.

Certification is **not** required to demo: the developer console simulator exercises the
whole skill against the live endpoint.
