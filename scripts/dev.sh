#!/usr/bin/env bash
# Starts the whole stack for local development: Postgres (docker), migrations,
# the FastAPI app, the Telegram bot, and the web UI. Works under bash on
# Linux/macOS and Git Bash on Windows.
#
# Usage:
#   ./scripts/dev.sh                 # start everything
#   ./scripts/dev.sh --no-bot        # skip the Telegram bot
#   ./scripts/dev.sh --no-frontend   # skip the web UI

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

RUN_BOT=1
RUN_FRONTEND=1
for arg in "$@"; do
  case "$arg" in
    --no-bot) RUN_BOT=0 ;;
    --no-frontend) RUN_FRONTEND=0 ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

if [ "$RUN_FRONTEND" -eq 1 ] && [ ! -d "$FRONTEND_DIR" ]; then
  RUN_FRONTEND=0
fi

cd "$BACKEND_DIR"

if [ ! -f .env ]; then
  echo "Missing backend/.env. Copy backend/.env.example to backend/.env and fill in the values first." >&2
  exit 1
fi

# Load .env into this shell so we can sanity-check required vars up front.
set -a
# shellcheck disable=SC1091
source .env
set +a

missing=()
[ -z "${BOAT_API_KEY:-}" ] && missing+=("BOAT_API_KEY")
[ -z "${TOKEN_ENCRYPTION_KEYS:-}" ] && missing+=("TOKEN_ENCRYPTION_KEYS")
[ -z "${HOOK_TOKEN:-}" ] && missing+=("HOOK_TOKEN")
[ -z "${PERMISSION_HOOK_BASE_URL:-}" ] && missing+=("PERMISSION_HOOK_BASE_URL")
if [ "$RUN_BOT" -eq 1 ] && [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  missing+=("TELEGRAM_BOT_TOKEN (or rerun with --no-bot)")
fi

if [ "${#missing[@]}" -gt 0 ]; then
  echo "Missing required backend/.env values:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  exit 1
fi

echo "==> Starting Postgres (docker compose)"
docker compose up -d

echo "==> Waiting for Postgres to be healthy"
POSTGRES_CONTAINER="$(docker compose ps -q postgres)"
for _ in $(seq 1 30); do
  status="$(docker inspect --format='{{.State.Health.Status}}' "$POSTGRES_CONTAINER" 2>/dev/null || echo "starting")"
  [ "$status" = "healthy" ] && break
  sleep 1
done
if [ "$status" != "healthy" ]; then
  echo "Postgres did not become healthy in time." >&2
  exit 1
fi

echo "==> Installing backend dependencies (uv sync)"
uv sync

echo "==> Running migrations (alembic upgrade head)"
uv run alembic upgrade head

# Give each service its own process group, including reloaders/npm children.
set -m
pids=()
cleanup() {
  echo
  echo "==> Shutting down"
  for pid in "${pids[@]}"; do
    kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "==> Starting FastAPI app on http://127.0.0.1:8000"
uv run fastapi dev app/main.py --host 127.0.0.1 --port 8000 &
pids+=("$!")

if [ "$RUN_BOT" -eq 1 ]; then
  echo "==> Starting Telegram bot"
  uv run python -m app.telegram_bot &
  pids+=("$!")
else
  echo "==> Skipping Telegram bot (--no-bot)"
fi

if [ "$RUN_FRONTEND" -eq 1 ]; then
  if [ ! -f "$FRONTEND_DIR/.env.local" ] && [ -f "$FRONTEND_DIR/.env.example" ]; then
    cp "$FRONTEND_DIR/.env.example" "$FRONTEND_DIR/.env.local"
  fi
  if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    echo "==> Installing frontend dependencies (npm install)"
    (cd "$FRONTEND_DIR" && npm install)
  fi
  echo "==> Starting web UI on http://127.0.0.1:3000"
  # Replace the subshell so its PID remains the frontend process-group leader.
  # Otherwise cleanup can kill the shell while its npm child is still starting.
  (cd "$FRONTEND_DIR" && exec npm run dev) &
  pids+=("$!")
else
  echo "==> Skipping web UI (--no-frontend)"
fi

echo "==> Everything is up. Press Ctrl+C to stop."
# Plain `wait` ignores an individual service failure while siblings keep
# running, and ultimately discards their exit statuses. Monitor each child
# with Bash 3-compatible primitives so a failed stack stops promptly.
while true; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      if wait "$pid"; then
        status=1 # Even a normal exit is unexpected for a development server.
      else
        status=$?
      fi
      echo "Development service (PID $pid) exited; stopping the stack." >&2
      exit "$status"
    fi
  done
  sleep 1
done
