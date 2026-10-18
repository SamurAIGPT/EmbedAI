#!/usr/bin/env bash
# Exercise the real launcher without Docker, dependencies, or credentials.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
task_tmp="$(mktemp -d)"
trap 'rm -rf "$task_tmp"' EXIT
mkdir -p "$task_tmp/scripts" "$task_tmp/backend" "$task_tmp/bin"
cp "$ROOT/scripts/dev.sh" "$task_tmp/scripts/dev.sh"
cat > "$task_tmp/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
printf invoked > "$DOCKER_MARKER"
exit 23
DOCKER
chmod +x "$task_tmp/bin/docker"
for missing_key in HOOK_TOKEN PERMISSION_HOOK_BASE_URL none; do
  cat > "$task_tmp/backend/.env" <<'ENV'
BOAT_API_KEY=owned-fixture
TOKEN_ENCRYPTION_KEYS=owned-fixture
HOOK_TOKEN=owned-fixture
PERMISSION_HOOK_BASE_URL=http://127.0.0.1:1
ENV
  if [ "$missing_key" != none ]; then
    printf '%s=\n' "$missing_key" >> "$task_tmp/backend/.env"
  fi
  rm -f "$task_tmp/docker-marker"
  status=0
  env -u HOOK_TOKEN -u PERMISSION_HOOK_BASE_URL PATH="$task_tmp/bin:$PATH" DOCKER_MARKER="$task_tmp/docker-marker" \
    bash "$task_tmp/scripts/dev.sh" --no-bot --no-frontend > "$task_tmp/output" 2>&1 || status=$?
  if [ "$missing_key" = none ]; then
    [ "$status" -eq 23 ] && [ -f "$task_tmp/docker-marker" ]
  else
    if [ "$status" -ne 1 ] || [ -f "$task_tmp/docker-marker" ]; then
      printf 'FAIL %s: expected preflight rejection before Docker; got exit %s\n' "$missing_key" "$status" >&2
      cat "$task_tmp/output" >&2
      exit 1
    fi
    grep -F "$missing_key" "$task_tmp/output" > /dev/null
  fi
  printf 'PASS %s\n' "$missing_key"
done

# Which database the launcher starts. A fake `uv` stops the launcher right after
# the database step, so only the Docker decision is observed.
cat > "$task_tmp/bin/uv" <<'UV'
#!/usr/bin/env bash
exit 24
UV
chmod +x "$task_tmp/bin/uv"

check_database() { # name, expect-docker (yes|no), env lines...
  local name="$1" expect="$2"; shift 2
  printf 'BOAT_API_KEY=owned-fixture
TOKEN_ENCRYPTION_KEYS=owned-fixture
HOOK_TOKEN=owned-fixture
PERMISSION_HOOK_BASE_URL=http://127.0.0.1:1
' > "$task_tmp/backend/.env"
  for line in "$@"; do printf '%s
' "$line" >> "$task_tmp/backend/.env"; done
  rm -f "$task_tmp/docker-marker"
  status=0
  env -u DATABASE_BACKEND -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT     PATH="$task_tmp/bin:$PATH" DOCKER_MARKER="$task_tmp/docker-marker"     bash "$task_tmp/scripts/dev.sh" --no-bot --no-frontend > "$task_tmp/output" 2>&1 || status=$?
  if [ "$expect" = yes ]; then
    [ "$status" -eq 23 ] && [ -f "$task_tmp/docker-marker" ] || { printf 'FAIL %s: expected Docker to be used (exit %s)
' "$name" "$status" >&2; cat "$task_tmp/output" >&2; exit 1; }
  elif [ "$expect" = no ]; then
    [ "$status" -eq 24 ] && [ ! -f "$task_tmp/docker-marker" ] || { printf 'FAIL %s: expected SQLite without Docker (exit %s)
' "$name" "$status" >&2; cat "$task_tmp/output" >&2; exit 1; }
    grep -F "SQLite" "$task_tmp/output" > /dev/null
  else
    [ "$status" -eq 1 ] && [ ! -f "$task_tmp/docker-marker" ] && grep -F "DATABASE_BACKEND" "$task_tmp/output" > /dev/null       || { printf 'FAIL %s: expected a DATABASE_BACKEND error (exit %s)
' "$name" "$status" >&2; cat "$task_tmp/output" >&2; exit 1; }
  fi
  printf 'PASS %s
' "$name"
}

check_database "nothing set -> Postgres via Docker, as before" yes
check_database "DATABASE_BACKEND=sqlite -> SQLite, no Docker" no DATABASE_BACKEND=sqlite
check_database "DATABASE_BACKEND=SQLite is normalised" no 'DATABASE_BACKEND=" SQLite "'
check_database "SQLite wins over existing POSTGRES_*" no DATABASE_BACKEND=sqlite POSTGRES_HOST=localhost
check_database "existing POSTGRES_* values -> Postgres" yes POSTGRES_HOST=localhost POSTGRES_PORT=5433
check_database "DATABASE_BACKEND=postgres -> Postgres" yes DATABASE_BACKEND=postgres
check_database "DATABASE_BACKEND=PostgreSQL is normalised" yes DATABASE_BACKEND=PostgreSQL
check_database "unknown DATABASE_BACKEND is rejected" error DATABASE_BACKEND=mysql
