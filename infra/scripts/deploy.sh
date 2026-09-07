#!/usr/bin/env bash
# One-command deploy to a fresh Ubuntu VM (Oracle Always Free ARM, Hetzner, anything).
#
#   infra/scripts/deploy.sh ubuntu@1.2.3.4 ~/.ssh/oracle.key jarvis.duckdns.org
#
# Idempotent: run it again to update. It installs Docker, opens 80/443 (Oracle's Ubuntu
# image ships iptables rules that block everything but SSH — the single most common
# "Caddy never gets a certificate" cause), copies .env, pins the domain into it, and
# runs `make prod-up` on the VM. Nothing here needs sudo on your laptop.
set -euo pipefail

TARGET="${1:?usage: deploy.sh user@host key-path domain}"
KEY="${2:?usage: deploy.sh user@host key-path domain}"
DOMAIN="${3:?usage: deploy.sh user@host key-path domain}"
REPO="${REPO_URL:-https://github.com/Bitshifter-9/JARVIS-X.git}"
BRANCH="${BRANCH:-main}"
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 $TARGET"

[ -f .env ] || { echo "run from the repo root: .env not found" >&2; exit 1; }

# A fresh instance reports "running" before sshd answers; wait for it rather than fail.
echo "▸ waiting for ssh on $TARGET"
for _ in $(seq 1 18); do
  $SSH -o ConnectTimeout=8 true 2>/dev/null && break
  sleep 10
done

# DuckDNS: if the token is in .env, point the name at this host before Caddy asks for a
# certificate — Let's Encrypt has to resolve the name to *this* IP.
DUCK_TOKEN=$(grep -E '^JARVIS_DUCKDNS_TOKEN=' .env | cut -d= -f2- | tr -d '"' || true)
if [ -n "$DUCK_TOKEN" ] && [[ "$DOMAIN" == *.duckdns.org ]]; then
  HOST_IP="${TARGET#*@}"
  SUB="${DOMAIN%.duckdns.org}"
  RESULT=$(curl -fsS "https://www.duckdns.org/update?domains=$SUB&token=$DUCK_TOKEN&ip=$HOST_IP" || echo KO)
  echo "▸ duckdns $DOMAIN → $HOST_IP: $RESULT"
fi

echo "▸ preparing $TARGET"
$SSH 'bash -s' <<'REMOTE'
set -euo pipefail
if ! command -v docker >/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq docker.io docker-compose-v2 git make
  sudo usermod -aG docker "$USER"
fi
# 3 GB of swap: the image build (uv sync + Chromium) and a Chromium page both spike past
# 2 GB, and a t4g.small has exactly 2 GB. Swap turns an OOM kill into a slow minute.
if ! swapon --show | grep -q swapfile; then
  sudo fallocate -l 3G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
# Oracle's Ubuntu image blocks 80/443 in iptables regardless of the cloud security list.
for port in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT 1 -p tcp --dport "$port" -j ACCEPT
done
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iptables-persistent >/dev/null 2>&1 || true
sudo netfilter-persistent save >/dev/null 2>&1 || true
REMOTE

# What you have is what you deploy: the working tree, not whatever GitHub has. Set
# DEPLOY_FROM=git to clone/pull the repo on the VM instead.
if [ "${DEPLOY_FROM:-tree}" = "git" ]; then
  echo "▸ syncing code from $REPO ($BRANCH)"
  $SSH "bash -c 'if [ -d JARVIS-X/.git ]; then cd JARVIS-X && git fetch -q && git checkout -q $BRANCH && git pull -q; else git clone -q -b $BRANCH $REPO JARVIS-X; fi'"
else
  echo "▸ syncing the working tree"
  rsync -az --delete -e "ssh -i $KEY -o StrictHostKeyChecking=accept-new" \
    --exclude .git --exclude .venv --exclude var --exclude vendor --exclude legacy \
    --exclude secrets \
    --exclude '.env' --exclude '__pycache__' --exclude '.pytest_cache' \
    --exclude 'apps/mobile/build' --exclude 'apps/mobile/.dart_tool' \
    --exclude 'apps/mobile/macos/Pods' --exclude 'apps/mobile/android/.gradle' \
    ./ "$TARGET:JARVIS-X/"
fi

echo "▸ writing .env (domain pinned, JWT secret real)"
TMP_ENV="$(mktemp)"
trap 'rm -f "$TMP_ENV"' EXIT
grep -vE '^(JARVIS_DOMAIN|JARVIS_BASE_URL|JARVIS_OAUTH_ISSUER|JARVIS_ENV)=' .env > "$TMP_ENV"
{
  echo "JARVIS_ENV=cloud"
  echo "JARVIS_DOMAIN=$DOMAIN"
  echo "JARVIS_BASE_URL=https://$DOMAIN"
  echo "JARVIS_OAUTH_ISSUER=https://$DOMAIN"
} >> "$TMP_ENV"
if grep -qE '^JARVIS_JWT_SECRET=("?)(dev-only-insecure-secret-change-me)?\1$' "$TMP_ENV"; then
  # Generated once and written back locally: a secret that changed on every deploy
  # would sign every phone and Mac out each time you ship.
  NEW_SECRET=$(openssl rand -hex 32)
  sed -i.bak '/^JARVIS_JWT_SECRET=/d' "$TMP_ENV"
  echo "JARVIS_JWT_SECRET=$NEW_SECRET" >> "$TMP_ENV"
  sed -i.bak '/^JARVIS_JWT_SECRET=/d' .env && echo "JARVIS_JWT_SECRET=$NEW_SECRET" >> .env && rm -f .env.bak
  echo "  generated a real JWT secret and saved it to your local .env (it is reused from now on)"
fi
# Files referenced by path in .env live on this laptop; the server needs the bytes. Ship
# the Firebase service account into a read-only secrets dir and point the variable at
# the path inside the container.
FCM_LOCAL=$(grep -E '^JARVIS_FCM_CREDENTIALS_PATH=' .env | tail -1 | cut -d= -f2- | tr -d '"' || true)
FCM_LOCAL="${FCM_LOCAL/#\~/$HOME}"
if [ -n "$FCM_LOCAL" ] && [ -f "$FCM_LOCAL" ]; then
  # The bind mount may already have created the directory as root; own it explicitly.
  $SSH 'sudo mkdir -p JARVIS-X/secrets && sudo chown "$USER" JARVIS-X/secrets && chmod 755 JARVIS-X/secrets'
  scp -q -i "$KEY" "$FCM_LOCAL" "$TARGET:JARVIS-X/secrets/fcm.json"
  $SSH 'chmod 644 JARVIS-X/secrets/fcm.json'
  FCM_PROJECT=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('project_id',''))" "$FCM_LOCAL" 2>/dev/null || true)
  if [ -n "$FCM_PROJECT" ] && ! grep -qE '^JARVIS_FCM_PROJECT_ID=.+' "$TMP_ENV"; then
    sed -i.bak '/^JARVIS_FCM_PROJECT_ID=/d' "$TMP_ENV"
    echo "JARVIS_FCM_PROJECT_ID=$FCM_PROJECT" >> "$TMP_ENV"
  fi
  sed -i.bak '/^JARVIS_FCM_CREDENTIALS_PATH=/d' "$TMP_ENV"
  echo "JARVIS_FCM_CREDENTIALS_PATH=/app/secrets/fcm.json" >> "$TMP_ENV"
  echo "  shipped $(basename "$FCM_LOCAL") → secrets/fcm.json"
fi
# The device signing key is what every paired phone and Mac verifies jobs against. Like
# the JWT secret it is generated once and written back locally so it never changes.
if ! grep -qE '^JARVIS_DEVICE_SIGNING_KEY_PEM=.+' "$TMP_ENV"; then
  PEM_LINE=$(uv run python -c "from jarvis.services.device.keys import generate_keypair; print(generate_keypair()[0].replace(chr(10), '\\\\n'))" 2>/dev/null || true)
  if [ -n "$PEM_LINE" ]; then
    sed -i.bak '/^JARVIS_DEVICE_SIGNING_KEY_PEM=/d' "$TMP_ENV"
    echo "JARVIS_DEVICE_SIGNING_KEY_PEM=\"$PEM_LINE\"" >> "$TMP_ENV"
    sed -i.bak '/^JARVIS_DEVICE_SIGNING_KEY_PEM=/d' .env && echo "JARVIS_DEVICE_SIGNING_KEY_PEM=\"$PEM_LINE\"" >> .env && rm -f .env.bak
    echo "  generated the device signing key and saved it to your local .env"
  fi
fi
# Secrets you set directly on the server (a Slack bot token, a webhook secret you pasted
# into the box and never into this laptop's .env) must survive a deploy. Shipping the
# local .env verbatim would wipe them. So: for any key that is empty or absent locally
# but present on the server, keep the server's value; local non-empty always wins.
SRV_ENV="$(mktemp)"
scp -q -i "$KEY" "$TARGET:JARVIS-X/.env" "$SRV_ENV" 2>/dev/null || : > "$SRV_ENV"
MERGED_ENV="$(mktemp)"
trap 'rm -f "$TMP_ENV" "$SRV_ENV" "$MERGED_ENV"' EXIT
python3 - "$TMP_ENV" "$SRV_ENV" > "$MERGED_ENV" <<'PY'
import sys
def load(path):
    d = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        s = line.rstrip("\n")
        if not s or s.lstrip().startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        d[k.strip()] = v
    return d
local_path, server_path = sys.argv[1], sys.argv[2]
local, server = load(local_path), load(server_path)
carried = []
out = []
for line in open(local_path, encoding="utf-8", errors="replace"):
    s = line.rstrip("\n")
    if "=" in s and not s.lstrip().startswith("#"):
        k, v = s.split("=", 1)
        k = k.strip()
        if v.strip().strip('"') == "" and server.get(k, "").strip().strip('"') != "":
            out.append(f"{k}={server[k]}")
            carried.append(k)
            continue
    out.append(s)
for k, v in server.items():
    if k not in local and v.strip().strip('"') != "":
        out.append(f"{k}={v}")
        carried.append(k)
sys.stdout.write("\n".join(out) + "\n")
if carried:
    sys.stderr.write("  kept server-set values: " + ", ".join(sorted(set(carried))) + "\n")
PY
scp -q -i "$KEY" "$MERGED_ENV" "$TARGET:JARVIS-X/.env"

echo "▸ building and starting (first run downloads Chromium and the models — minutes)"
$SSH "bash -c 'cd JARVIS-X && sudo -E make prod-up'"

echo "▸ waiting for https://$DOMAIN/healthz"
for _ in $(seq 1 40); do
  if curl -fsS "https://$DOMAIN/healthz" >/dev/null 2>&1; then
    echo "✓ up: $(curl -fsS "https://$DOMAIN/healthz")"
    echo
    echo "Next: point the apps and the Mac helper at https://$DOMAIN, then finish Slack"
    echo "(Event Subscriptions → https://$DOMAIN/webhooks/slack) and Telegram's setWebhook."
    exit 0
  fi
  sleep 15
done
echo "not answering yet — check: $SSH 'cd JARVIS-X && sudo make prod-logs'" >&2
exit 1
