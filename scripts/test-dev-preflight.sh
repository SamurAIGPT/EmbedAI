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
