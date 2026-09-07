#!/usr/bin/env bash
# Zero-cost "deploy": your Mac runs the stack, Cloudflare gives it a public HTTPS URL.
#
#   make tunnel           # in one terminal; keep it open
#   make stack            # in another: api + worker + scheduler
#
# A quick tunnel needs no account, no card, no domain, no open router port (works behind
# CGNAT). Its URL changes each time it starts — so this script writes the new one into
# .env as JARVIS_BASE_URL, re-registers the Telegram webhook, and prints what to paste
# into Slack / WhatsApp / Alexa. Ctrl-C stops it; the URL dies with it.
set -euo pipefail

command -v cloudflared >/dev/null || { echo "brew install cloudflared" >&2; exit 1; }
[ -f .env ] || { echo "run from the repo root" >&2; exit 1; }
mkdir -p var
LOG="var/tunnel.log"
: > "$LOG"

cloudflared tunnel --url http://localhost:8000 --no-autoupdate >"$LOG" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null; echo; echo "tunnel closed"' EXIT

URL=""
for _ in $(seq 1 60); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)
  [ -n "$URL" ] && break
  sleep 1
done
[ -n "$URL" ] || { echo "cloudflared did not report a URL; see $LOG" >&2; exit 1; }
echo "$URL" > var/tunnel-url

# Pin it into .env so OAuth redirects and the agent card use the public host.
if grep -q '^JARVIS_BASE_URL=' .env; then
  sed -i.bak "s|^JARVIS_BASE_URL=.*|JARVIS_BASE_URL=$URL|" .env && rm -f .env.bak
else
  echo "JARVIS_BASE_URL=$URL" >> .env
fi

value() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'"; }
TG_TOKEN=$(value JARVIS_TELEGRAM_BOT_TOKEN)
TG_SECRET=$(value JARVIS_TELEGRAM_WEBHOOK_SECRET)
if [ -n "$TG_TOKEN" ]; then
  curl -fsS "https://api.telegram.org/bot$TG_TOKEN/setWebhook" \
    --data-urlencode "url=$URL/webhooks/telegram" \
    ${TG_SECRET:+--data-urlencode "secret_token=$TG_SECRET"} >/dev/null \
    && echo "✓ Telegram webhook → $URL/webhooks/telegram" \
    || echo "! Telegram setWebhook failed (token?)"
fi

cat <<EOF

  ✓ public URL:  $URL        (also in var/tunnel-url; JARVIS_BASE_URL updated)

  Slack     Event Subscriptions → Request URL:  $URL/webhooks/slack
  WhatsApp  Webhook callback URL:              $URL/webhooks/whatsapp
  Alexa     Endpoint:                          $URL/alexa
  Google    OAuth redirect URI:                $URL/v1/connectors/google/callback
  Apps      server URL on the sign-in screen:  $URL

  Restart the API once (.env changed), then keep this terminal open. Ctrl-C to stop.
EOF
wait $PID
