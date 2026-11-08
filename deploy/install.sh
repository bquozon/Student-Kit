#!/usr/bin/env bash
# Install or update Swiss Student Kit on the VPS (same setup as polyuni.ch: Docker behind the existing Caddy container).
#
#   sudo bash deploy/install.sh kit.example.ch [www.kit.example.ch …]   # main address first; others redirect (301)
#
# Safe to re-run: updates the code, rebuilds the image, keeps data/ (the SQLite file, backups) and the settings file.
set -euo pipefail

DOMAIN="${1:-}"
REDIRECTS=("${@:2}")
APP=/opt/student-kit
SVC_USER=studentkit
ENV_FILE=/etc/student-kit.env
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[[ $EUID -eq 0 ]] || { echo "Please run with sudo."; exit 1; }
[[ -n "$DOMAIN" ]] || { echo "usage: sudo bash deploy/install.sh kit.example.ch [other.address …]"; exit 1; }
[[ -f "$SRC/kit/server.py" ]] || { echo "Run this from the project folder."; exit 1; }
command -v docker >/dev/null || { echo "Docker is not installed."; exit 1; }

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
compose() { if docker compose version >/dev/null 2>&1; then docker compose "$@"; else docker-compose "$@"; fi; }

say "Service user '$SVC_USER'"
id -u "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin "$SVC_USER"

say "Copying code to $APP"
command -v rsync >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq rsync >/dev/null
mkdir -p "$APP/data"
rsync -a --delete --exclude '.git/' --exclude '.venv/' --exclude '__pycache__/' --exclude 'data/' \
  --exclude 'deploy/.env' --exclude '.env' "$SRC"/ "$APP"/
mkdir -p "$APP/data" && cp "$SRC/data/seed.json" "$APP/data/seed.json"
chown -R "$SVC_USER:$SVC_USER" "$APP/data"

say "Settings file $ENV_FILE"
if [[ ! -f "$ENV_FILE" ]]; then
  grep -v '^DATA_DIR=' "$SRC/.env.example" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi
sed -i '/^SITE_URL=/d' "$ENV_FILE" && echo "SITE_URL=https://$DOMAIN" >> "$ENV_FILE"
grep -q '^SMTP_HOST=.' "$ENV_FILE" || echo "  ⚠ SMTP_HOST is empty: login emails won't go out. Edit $ENV_FILE, then re-run."
grep -q '^ADMIN_EMAIL=.' "$ENV_FILE" || echo "  ⚠ ADMIN_EMAIL is empty: nobody is a moderator yet."

say "Container"
proxy="${PROXY_CONTAINER:-}"
[[ -n "$proxy" ]] || proxy=$(docker ps --format '{{.Names}} {{.Ports}}' | awk '/:(80|443)->/ {print $1; exit}' || true)
[[ -n "$proxy" ]] || { echo "  Could not find the proxy container. Re-run with PROXY_CONTAINER=<name> in front."; exit 1; }
net=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$proxy" | awk '{print $1}')
cat > "$APP/deploy/.env" <<EOF
PROXY_NETWORK=$net
APP_DIR=$APP
ENV_FILE=$ENV_FILE
KIT_UID=$(id -u "$SVC_USER")
KIT_GID=$(id -g "$SVC_USER")
EOF
chmod 600 "$APP/deploy/.env"
# a failed build stops here and leaves the running container as it was
(cd "$APP/deploy" && compose build && compose up -d --force-recreate --remove-orphans)
for _ in $(seq 30); do docker exec student-kit wget -qO- http://127.0.0.1:8800/health >/dev/null 2>&1 && break; sleep 1; done

say "Database: schema + seed (idempotent)"
docker exec student-kit python -m kit.seed

say "Scheduled jobs (digest daily, link check weekly, backup nightly)"
cp "$APP"/deploy/systemd/kit-*.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now kit-digest.timer kit-linkcheck.timer kit-backup.timer

say "Caddy site block for $DOMAIN (proxy: $proxy)"
img=$(docker inspect -f '{{.Config.Image}}' "$proxy")
if [[ "$img" != *caddy* ]]; then
  echo "  '$proxy' ($img) is not a plain Caddy container: point $DOMAIN at student-kit:8800 yourself."
else
  inside=$(docker inspect -f '{{json .Config.Cmd}}' "$proxy" | grep -oP '"--config","\K[^"]+' || true)
  [[ -n "$inside" ]] || inside=/etc/caddy/Caddyfile
  mounts=$(docker inspect -f '{{range .Mounts}}{{.Destination}}|{{.Source}}{{"\n"}}{{end}}' "$proxy")
  cf=$(awk -F'|' -v f="$inside" '$1==f {print $2; exit}' <<< "$mounts" || true)
  if [[ -z "$cf" ]]; then
    mnt=$(awk -F'|' -v d="$(dirname "$inside")" '$1==d {print $2; exit}' <<< "$mounts" || true)
    [[ -n "$mnt" && -f "$mnt/$(basename "$inside")" ]] && cf="$mnt/$(basename "$inside")"
  fi
  reload=(docker exec "$proxy" caddy reload --config "$inside" --adapter caddyfile)
  if [[ -z "$cf" || ! -f "$cf" ]]; then
    echo "  Could not find the Caddyfile on disk. Add this block, then reload Caddy:"
    echo "    $DOMAIN { reverse_proxy student-kit:8800 { header_up X-Real-IP {remote_host} } }"
  else
    cp "$cf" "$cf.bak-student-kit"
    python3 - "$cf" "$DOMAIN" "${REDIRECTS[@]}" <<'PY'
import re, sys
path, main, olds = sys.argv[1], sys.argv[2], sys.argv[3:]
text = open(path).read()
def put(host, body):
    global text
    block = f"{host} {{\n{body}\n}}\n"
    pat = re.compile(rf"^{re.escape(host)} \{{\n.*?^\}}\n?", re.M | re.S)
    text = pat.sub(lambda _: block, text, count=1) if pat.search(text) else text.rstrip("\n") + "\n\n" + block
# the app reads the visitor's IP from X-Real-IP (rate limits, anonymous stats), never from X-Forwarded-For
put(main, "\tencode zstd gzip\n\treverse_proxy student-kit:8800 {\n\t\theader_up X-Real-IP {remote_host}\n\t}")
for old in olds:
    put(old, f"\tredir https://{main}{{uri}} permanent")
open(path, "w").write(text)
PY
    if cmp -s "$cf" "$cf.bak-student-kit"; then
      echo "  $cf already has the block for $DOMAIN."
    elif docker exec "$proxy" caddy validate --config "$inside" --adapter caddyfile >/dev/null 2>&1 && "${reload[@]}" >/dev/null 2>&1; then
      echo "  Added $DOMAIN to $cf and reloaded Caddy (backup: $cf.bak-student-kit)."
    else
      cat "$cf.bak-student-kit" > "$cf"
      "${reload[@]}" >/dev/null 2>&1 || true
      echo "  Caddy rejected the change; the original Caddyfile is restored. Add the block by hand:"
      echo "    $DOMAIN { reverse_proxy student-kit:8800 { header_up X-Real-IP {remote_host} } }"
    fi
  fi
fi

say "Done"
echo "  Website:   https://$DOMAIN   (DNS must point here; Caddy fetches the certificate on the first visit)"
echo "  Mod panel: https://$DOMAIN/mod   (stats: /mod/stats)"
echo "  Settings:  sudo nano $ENV_FILE   then re-run this script"
echo "  Logs:      docker logs -f student-kit"
echo "  Jobs:      systemctl list-timers 'kit-*'   ·   journalctl -u 'kit-job@*' -n 50"
echo "  Backups:   $APP/data/backups/ (14 nightly copies)"
