#!/usr/bin/env bash
# Starts the whole stack for local development: the database (SQLite, or Postgres in
# docker when configured), migrations,
# the FastAPI app, the Telegram bot, and the web UI. Works under bash on
# Linux/macOS and Git Bash on Windows.
#
# Usage:
#   ./scripts/dev.sh                 # start everything
#   ./scripts/dev.sh --no-bot        # skip the Telegram bot
#   ./scripts/dev.sh --no-frontend   # skip the web UI

# An explicit `sh dev.sh` ignores the shebang. Re-enter Bash before using
# Bash-specific syntax on systems where /bin/sh is dash.
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

RUN_BOT=1
RUN_FRONTEND=1
BOT_SKIP_REASON="--no-bot"
for arg in "$@"; do
  case "$arg" in
    --no-bot) RUN_BOT=0 ;;
    --no-frontend) RUN_FRONTEND=0 ;;
    --help|-h)
      echo "Usage: $0 [--no-bot] [--no-frontend]"
      echo "Prepares local configuration and starts the API, optional bot, and web UI."
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

if [ "$RUN_FRONTEND" -eq 1 ] && [ ! -d "$FRONTEND_DIR" ]; then
  RUN_FRONTEND=0
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Missing required tool: uv. Install it before starting development." >&2
  exit 1
fi
if [ "$RUN_FRONTEND" -eq 1 ] && ! command -v npm >/dev/null 2>&1; then
  echo "Missing required tool: npm. Install Node.js or rerun with --no-frontend." >&2
  exit 1
fi

cd "$BACKEND_DIR"

if [ ! -f .env ]; then
  echo "==> Creating backend/.env from .env.example"
  (umask 077; cp .env.example .env)
fi

echo "==> Installing backend dependencies (uv sync)"
uv sync

echo "==> Preparing local development configuration"
# The helper prints only shell-quoted export statements; dotenv values remain data.
env_exports="$(uv run --no-sync python "$SCRIPT_DIR/dev-env.py" "$BACKEND_DIR/.env")"
eval "$env_exports"
unset env_exports
echo "==> Using CA certificates from $SSL_CERT_FILE"

if [ -n "${BOAT_API_KEY:-}" ]; then
  echo "==> Checking Boat HTTPS certificates"
  uv run --no-sync python "$SCRIPT_DIR/dev-env.py" --check-tls
else
  echo "==> BOAT_API_KEY is not set. Sandbox tasks require a Boat API key in backend/.env."
fi

missing=()
[ -z "${TOKEN_ENCRYPTION_KEYS:-}" ] && missing+=("TOKEN_ENCRYPTION_KEYS")
# Boat is optional. If a Boat key is set, its permission hook needs both of these.
if [ -n "${BOAT_API_KEY:-}" ]; then
  if [ -z "${HOOK_TOKEN:-}" ] || [ "$HOOK_TOKEN" = "your-secret-token-here" ]; then
    missing+=("HOOK_TOKEN (required with BOAT_API_KEY)")
  fi
  if [ -z "${PERMISSION_HOOK_BASE_URL:-}" ] || [ "$PERMISSION_HOOK_BASE_URL" = "your-public-url-here" ]; then
    missing+=("PERMISSION_HOOK_BASE_URL (required with BOAT_API_KEY)")
  fi
fi
if [ "$RUN_BOT" -eq 1 ] && [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  RUN_BOT=0
  BOT_SKIP_REASON="TELEGRAM_BOT_TOKEN is not set"
elif [ "$RUN_BOT" -eq 1 ] && [ -z "${BOAT_API_KEY:-}" ]; then
  RUN_BOT=0
  BOT_SKIP_REASON="BOAT_API_KEY is not set"
fi

if [ "${#missing[@]}" -gt 0 ]; then
  echo "Missing required backend/.env values:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  exit 1
fi

# Which database to use. This mirrors Settings.DATABASE_BACKEND in
# backend/app/core/config.py: SQLite only when DATABASE_BACKEND=sqlite is set;
# otherwise PostgreSQL, as before, so an existing setup never changes database.
db_backend="$(printf '%s' "${DATABASE_BACKEND:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
case "$db_backend" in
  sqlite) USE_POSTGRES=0 ;;
  ""|postgres|postgresql) USE_POSTGRES=1 ;;
  *)
    echo "DATABASE_BACKEND must be sqlite, postgres or empty (got: $db_backend)." >&2
    exit 1
    ;;
esac

if [ "$USE_POSTGRES" -eq 1 ]; then
  for tool in docker; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      echo "Missing required tool: $tool. Install it or select DATABASE_BACKEND=sqlite." >&2
      exit 1
    fi
  done
  if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose is unavailable. Install the Docker Compose plugin." >&2
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "Docker is unavailable. Start Docker and check that your user can access it." >&2
    exit 1
  fi
  echo "==> Starting Postgres (docker compose)"
  docker compose up -d

  echo "==> Waiting for Postgres to be healthy"
  POSTGRES_CONTAINER="$(docker compose ps -q postgres)"
  if [ -z "$POSTGRES_CONTAINER" ]; then
    echo "Postgres container was not created by Docker Compose." >&2
    exit 1
  fi
  for _ in $(seq 1 30); do
    status="$(docker inspect --format='{{.State.Health.Status}}' "$POSTGRES_CONTAINER" 2>/dev/null || echo "starting")"
    [ "$status" = "healthy" ] && break
    sleep 1
  done
  if [ "$status" != "healthy" ]; then
    echo "Postgres did not become healthy in time." >&2
    exit 1
  fi
else
  echo "==> Using SQLite (no Docker needed)"
fi

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
  echo "==> Skipping Telegram bot ($BOT_SKIP_REASON)"
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
