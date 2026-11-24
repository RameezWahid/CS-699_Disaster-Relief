#!/usr/bin/env bash
# Start the whole project: containers, health check, demo data and a public HTTPS tunnel.
#
#   ./demo.sh                 start everything (containers + demo data + tunnel for phones)
#   ./demo.sh --no-tunnel     start without the tunnel (laptop only)
#   ./demo.sh --no-seed       start without adding the demo data
#   ./demo.sh status          show services, health and the current tunnel address
#   ./demo.sh stop            stop the tunnel (containers keep running)
#   ./demo.sh stop --all      stop the tunnel and all containers (data is kept)
#
# Demo logins use the password in DEMO_PASSWORD (default demo-pass-123). Local demo only.

set -euo pipefail
cd "$(dirname "$0")"

STATE_DIR=.demo
TUNNEL_PID="$STATE_DIR/tunnel.pid"
TUNNEL_LOG="$STATE_DIR/tunnel.log"
LOCAL_URL=http://localhost:8080
DEMO_PASSWORD=${DEMO_PASSWORD:-demo-pass-123}

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

tunnel_url() { grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1; }

tunnel_running() { [[ -f $TUNNEL_PID ]] && kill -0 "$(cat "$TUNNEL_PID")" 2>/dev/null; }

find_cloudflared() {
  if command -v cloudflared >/dev/null; then command -v cloudflared; return; fi
  if [[ -x $HOME/.local/bin/cloudflared ]]; then echo "$HOME/.local/bin/cloudflared"; return; fi
  local arch
  case "$(uname -m)" in
    x86_64) arch=amd64 ;;
    aarch64 | arm64) arch=arm64 ;;
    *) die "no cloudflared for $(uname -m); install it from https://github.com/cloudflare/cloudflared" ;;
  esac
  say "Downloading cloudflared to ~/.local/bin (one time)" >&2
  mkdir -p "$HOME/.local/bin"
  curl -fsSL -o "$HOME/.local/bin/cloudflared" \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$arch"
  chmod +x "$HOME/.local/bin/cloudflared"
  echo "$HOME/.local/bin/cloudflared"
}

wait_for() { # url, seconds
  local url=$1 left=$2
  until curl -fsS -m 5 -o /dev/null "$url" 2>/dev/null; do
    ((left -= 2)) || true
    ((left > 0)) || return 1
    sleep 2
  done
}

ensure_env() {
  [[ -f .env ]] && return
  say "No .env found: creating one from .env.example with random secrets"
  cp .env.example .env
  for key in APP_DB_PASSWORD SENDER_HASH_SECRET JWT_SECRET; do
    sed -i "s|^$key=.*|$key=$(openssl rand -hex 32)|" .env
  done
}

start_containers() {
  command -v docker >/dev/null || die "Docker is not installed"
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is needed (docker compose ...)"
  ensure_env
  [[ -f data/tiles/mumbai.pmtiles ]] || die "data/tiles/mumbai.pmtiles is missing (the offline map)"
  [[ -f data/models/muril-flood/model.onnx ]] ||
    warn "no trained classifier in data/models/muril-flood/; reports are classified by keyword rules"

  say "Building and starting containers (the first run takes a few minutes)"
  docker compose up -d --build
  # nginx resolves the api container's address once at start; restart it after api is recreated.
  docker compose restart web >/dev/null

  say "Waiting for the app on $LOCAL_URL"
  wait_for "$LOCAL_URL/api/wards" 180 || die "no answer from $LOCAL_URL; see: docker compose logs api web"
}

seed_demo() {
  say "Adding demo data (logins, reports, offers; existing data is kept)"
  docker compose exec -T -e DEMO_PASSWORD="$DEMO_PASSWORD" api python -m scripts.seed_demo
}

start_tunnel() {
  mkdir -p "$STATE_DIR"
  if tunnel_running; then
    say "Tunnel already running"
    return
  fi
  local bin
  bin=$(find_cloudflared)
  say "Starting the Cloudflare tunnel"
  : >"$TUNNEL_LOG"
  nohup "$bin" tunnel --no-autoupdate --url "$LOCAL_URL" >"$TUNNEL_LOG" 2>&1 &
  echo $! >"$TUNNEL_PID"
  local left=60
  until [[ -n $(tunnel_url) ]]; do
    tunnel_running || die "tunnel exited; see $TUNNEL_LOG"
    ((left -= 1)) || true
    ((left > 0)) || die "no tunnel address after 60 s; see $TUNNEL_LOG"
    sleep 1
  done
  # A new address can take a few seconds to appear in DNS.
  wait_for "$(tunnel_url)/api/wards" 60 || warn "the tunnel address is not answering yet; try again in a minute"
}

stop_tunnel() {
  if tunnel_running; then
    kill "$(cat "$TUNNEL_PID")" && say "Tunnel stopped"
  else
    say "No tunnel running"
  fi
  rm -f "$TUNNEL_PID"
}

summary() {
  local url
  url=$(tunnel_running && tunnel_url || true)
  echo
  say "Ready"
  echo "  Dashboard (laptop):   $LOCAL_URL"
  echo "  Report form:          $LOCAL_URL/#report"
  echo "  Responder app:        $LOCAL_URL/#responder"
  if [[ -n $url ]]; then
    echo "  Phone, responder app: $url/#responder"
    echo "  Phone, report form:   $url/#report"
    echo "  (new address each time the tunnel starts; open it once online so it works offline)"
  fi
  echo "  Logins: demo_admin / demo_coord / demo_resp, password $DEMO_PASSWORD"
  echo "  New reports appear within seconds; proposals and the silent-zone alert within a minute."
  echo "  Stop the tunnel: ./demo.sh stop    Everything: ./demo.sh stop --all"
}

status() {
  docker compose ps --format '  {{.Service}}: {{.Status}}'
  if curl -fsS -m 5 "$LOCAL_URL/api/wards" >/dev/null 2>&1; then
    echo "  app: answering on $LOCAL_URL"
  else
    echo "  app: not answering"
  fi
  if tunnel_running; then echo "  tunnel: $(tunnel_url)"; else echo "  tunnel: not running"; fi
}

tunnel=1
seed=1
all=0
command=start
for arg in "$@"; do
  case "$arg" in
    start | stop | status) command=$arg ;;
    --no-tunnel) tunnel=0 ;;
    --no-seed) seed=0 ;;
    --all) all=1 ;;
    -h | --help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "unknown option $arg (see ./demo.sh --help)" ;;
  esac
done

case "$command" in
  start)
    start_containers
    if ((seed)); then seed_demo; fi
    if ((tunnel)); then start_tunnel; fi
    summary
    ;;
  stop)
    stop_tunnel
    if ((all)); then
      say "Stopping containers (data is kept; 'docker compose down -v' would delete it)"
      docker compose stop
    fi
    ;;
  status) status ;;
esac
