# Self-hosting on a server (one owner)

Run Open Duet on a Linux machine you rent or own, so your agents and routines keep working with your laptop closed, and reach it from a browser or a phone over HTTPS. One person owns the server: you sign in with a passphrase and pair other devices with one-time codes. It is not a multi-user or hosted service (see [AUTH.md](AUTH.md) "Limits").

There are two ways to run it. Both use the same server, the same settings and the same commands.

| | Docker Compose | Direct install |
|---|---|---|
| What you install | Docker | Node.js 22, [uv](https://docs.astral.sh/uv/), a service account |
| HTTPS | nginx or Caddy overlay included | a proxy you set up on the host |
| Runs as a service | `restart: unless-stopped` | a systemd unit the server writes for you |
| Updates | rebuild the image | pull, rebuild the pages, restart |

> **Status: experimental.** What was run, and what was not, is listed in [What has been checked](#what-has-been-checked). Read it before you rely on this for anything that matters.

## Before you start

- A Linux server with `sudo`, a domain name whose address points at it, and ports 80 and 443 open.
- At least one connected engine: Claude Code, Codex, Gemini CLI, Cursor, OpenRouter, a custom ACP command or an OpenAI-compatible endpoint. CLI engines must be installed and signed in **on the server**, as the same account that runs Open Duet: see [ENGINES.md](ENGINES.md) and [Engines](#engines). Agents run there, with that account's files.
- Two secrets you must keep: your **passphrase**, and the **encryption key** (`TOKEN_ENCRYPTION_KEYS`). The key encrypts the credentials the server stores. Back it up somewhere else: without it those credentials cannot be read, and a backup archive does not contain it.

Agents only run when the settings say `LOCAL_EXECUTION_ENABLED=true` (both routes below set it). Tools (file edits and commands) stay off until you also set `LOCAL_TOOLS_ENABLED=true`; every command still asks for your approval.

## Route 1: Docker Compose

Needs Docker with the Compose plugin. The app listens only on the host's loopback (`127.0.0.1:8000`); a proxy container puts it on HTTPS.

1. **Get the code and build the image**

   <!-- verify:docker -->
   ```sh
   git clone https://github.com/Anil-matcha/open-duet
   cd open-duet
   touch open-dots.env && chmod 600 open-dots.env
   docker compose build
   ```

2. **Write the settings** (the file stays outside the image and outside Git)

   <!-- verify:docker -->
   ```sh
   KEY=$(docker compose run --rm --no-deps --entrypoint python open-dots -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
   printf 'TOKEN_ENCRYPTION_KEYS=%s\nLOCAL_EXECUTION_ENABLED=true\nOPEN_DOTS_PUBLIC_URL=https://dots.example.com\n' "$KEY" > open-dots.env
   ```

   Keep a copy of that file somewhere else: it holds the key.

3. **Start it with a proxy in front.** Pick one and set the domain you own:

   ```sh
   export OPEN_DOTS_DOMAIN=dots.example.com
   docker compose -f compose.yaml -f deploy/proxy/compose.caddy.yaml up -d      # Caddy: gets its own certificate
   # or: docker compose -f compose.yaml -f deploy/proxy/compose.nginx.yaml up -d  # nginx: you provide the certificate, see the file
   ```

   Without a proxy (a tunnel, or a proxy on the host) run `docker compose up -d`; the app is then only on `127.0.0.1:8000`. See [Who is believed to be the proxy](#who-is-believed-to-be-the-proxy) before you point a host-side proxy at it.

4. **Set your passphrase, then check the machine**

   <!-- verify:docker -->
   ```sh
   docker compose exec open-dots python -m app.auth.cli set-passphrase
   ```

   The first passphrase is set from the server's own shell on purpose: nobody who can merely reach the web address can claim the server. Then check the machine:

   <!-- verify:docker doctor -->
   ```sh
   docker compose exec open-dots python -m app.server doctor
   ```

   Until an engine is installed and signed in (see [Engines](#engines)) `doctor` reports one problem, "No engine is ready", and exits with 1; everything else should say `ok`. Then open `https://dots.example.com/login/` and sign in.

The container has no access to the host's Docker, so agent computers ([COMPUTERS.md](COMPUTERS.md)) are not available in this route; use the direct install if you need them.

Data lives in the Docker volume `open-dots_data` (the container's `/data`). Update with `git pull`, then `docker compose build` and `up -d` again with the same `-f` files; the database is updated when the container starts, and a SQLite file is copied aside first.

## Route 2: direct install with systemd

Install [Node.js 22](https://nodejs.org) and [uv](https://docs.astral.sh/uv/) first (uv downloads the Python the backend needs). Then:

1. **Make an account and get the code**

   <!-- verify:root -->
   ```sh
   sudo useradd --create-home --shell /bin/bash opendots
   sudo install -d -o opendots -g opendots /opt/open-dots
   ```

   <!-- verify:skip -->
   ```sh
   sudo -u opendots git clone https://github.com/Anil-matcha/open-duet /opt/open-dots
   ```

2. **Build the web pages and install the backend** (as `opendots`: `sudo -iu opendots`; repeat after each update)

   <!-- verify:account -->
   ```sh
   cd /opt/open-dots/frontend && npm ci && cd ..
   node scripts/build-ui.js            # writes frontend/out, a build that talks to whatever host serves it
   cd backend && uv sync --frozen
   ```

3. **Write the settings** (back at your own account; the file is readable only by `opendots`)

   <!-- verify:root -->
   ```sh
   sudo install -d /etc/open-dots
   sudo install -m 0600 -o opendots -g opendots /dev/null /etc/open-dots/open-dots.env
   KEY=$(/opt/open-dots/backend/.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
   sudo tee /etc/open-dots/open-dots.env >/dev/null <<EOF
   OPEN_DOTS_PUBLIC_URL=https://dots.example.com
   TOKEN_ENCRYPTION_KEYS=$KEY
   OPEN_DOTS_UI_DIR=/opt/open-dots/frontend/out
   DATABASE_BACKEND=sqlite
   LOCAL_EXECUTION_ENABLED=true
   EOF
   ```

   Without `DATABASE_BACKEND=sqlite` the server looks for a PostgreSQL database (the Docker image sets SQLite for you). [`deploy/open-dots.env.example`](../deploy/open-dots.env.example) lists every other setting you may want. **Back that file up** (it holds the key).

4. **Prepare the database, set your passphrase, check the machine** (as `opendots`)

   <!-- verify:account -->
   ```sh
   cd /opt/open-dots/backend
   set -a; . /etc/open-dots/open-dots.env; set +a
   .venv/bin/python -m app.server migrate
   .venv/bin/python -m app.auth.cli set-passphrase
   ```

   The first passphrase is set from the server's own shell on purpose: nobody who can merely reach the web address can claim the server. Then check the machine:

   <!-- verify:account doctor -->
   ```sh
   cd /opt/open-dots/backend && set -a; . /etc/open-dots/open-dots.env; set +a
   .venv/bin/python -m app.server doctor
   ```

   `doctor` changes nothing and says, for each problem, what to do. Until an engine is installed and signed in (see [Engines](#engines)) it reports one problem, "No engine is ready", and exits with 1; everything else should say `ok`.

5. **Start it once by hand to see it work** (as `opendots`)

   <!-- verify:account serve -->
   ```sh
   cd /opt/open-dots/backend && set -a; . /etc/open-dots/open-dots.env; set +a
   .venv/bin/python -m app.server       # listens on 127.0.0.1:8000 only; stop with Ctrl-C
   ```

   In another terminal:

   <!-- verify:account check -->
   ```sh
   curl -fsS http://127.0.0.1:8000/api/v1/health
   ```

6. **Run it for good.** The server writes a systemd unit for this installation (it migrates the database before each start, restarts on failure and gives a stop time to finish conversations):

   <!-- verify:root -->
   ```sh
   cd /opt/open-dots/backend && sudo .venv/bin/python -m app.server service --user opendots --write
   ```

   <!-- verify:skip -->
   ```sh
   sudo systemctl daemon-reload && sudo systemctl enable --now open-dots
   journalctl -u open-dots -f
   ```

7. **Put a reverse proxy in front.** Start from [`deploy/Caddyfile`](../deploy/Caddyfile) or [`deploy/nginx.conf.example`](../deploy/nginx.conf.example) and follow [the proxy rules](#the-proxy-rules). Then open `https://dots.example.com/login/` and sign in.

Update:

<!-- verify:skip -->
```sh
sudo systemctl stop open-dots
sudo -u opendots -H bash -c 'cd /opt/open-dots && git pull --ff-only && (cd frontend && npm ci) && node scripts/build-ui.js && (cd backend && uv sync --frozen)'
sudo systemctl start open-dots       # migrates first; a SQLite file is copied aside before a change
```

If the migration fails, the server does not start and the journal says why; the copy made before is named in the message.

## Pair your phone

In the web app: **Settings → Devices → Create pairing code**, choose *Chat only* for a phone, and scan the QR code or open the link. Or from the server's shell (inside the container for Route 1, prefixed with `docker compose exec open-dots`):

<!-- verify:account -->
```sh
cd /opt/open-dots/backend && set -a; . /etc/open-dots/open-dots.env; set +a
.venv/bin/python -m app.auth.cli pairing-code --scope chat
```

It prints a full link when `OPEN_DOTS_PUBLIC_URL` is set. A code works once and expires.

## Engines

Connect engines in **Settings → Engines**. Saved keys are encrypted. CLI browser sign-in runs from a terminal as the account running Open Duet; use API keys on headless servers. OpenRouter and OpenAI-compatible endpoints need no CLI.

- **Direct install:** install the CLI as `opendots` (see [ENGINES.md](ENGINES.md)), then `claude auth login` or `codex login --device-auth`. A service has a short `PATH`; the server also looks in `~/.local/bin`, `~/.npm-global/bin` and `/usr/local/bin`, and `OPEN_DOTS_EXTRA_PATH` adds more folders.
- **Docker:** the image carries no engine and no credentials. To add one, name its package when you build, for example `OPEN_DOTS_ENGINES="@anthropic-ai/claude-code" docker compose build`, then sign in inside the container: `docker compose exec -it open-dots claude auth login`. The sign-in is kept in the data volume. Installing a CLI into a running container is lost when it is recreated; rebuild with `OPEN_DOTS_ENGINES` instead.

Preset connection options:

| Engine | Headless connection | Optional npm package in the image |
|---|---|---|
| Gemini CLI | Save a key in Settings, or `GEMINI_API_KEY`; Vertex environment names must be opted in with `OPEN_DOTS_ENGINE_ENV` | `@google/gemini-cli` |
| Cursor | Save a key in Settings, or `CURSOR_API_KEY` / `CURSOR_AUTH_TOKEN` | None; install Cursor separately |
| OpenCode (experimental) | `opencode auth login` or its hosted API key | `opencode-ai` |
| OpenRouter | Save an OpenRouter key in Settings | None |

For example, `OPEN_DOTS_ENGINES="@google/gemini-cli opencode-ai" docker compose build` installs those packages; a direct image build uses `--build-arg ENGINES="@google/gemini-cli opencode-ai"`. These optional package builds have not been run. Cursor's installer is outside the supported image build. Preset CLIs on Windows have not been enabled.

`OPEN_DOTS_PRESET_ENGINES` is a comma-separated selection of presets. Unset means Gemini, Cursor and OpenRouter. Set it to an empty string to disable all presets, or `gemini,cursor,openrouter,opencode` to also expose experimental OpenCode. OpenCode must remain opt-in until a real adversarial configuration check proves its permission override. Custom ACP and endpoint engines remain available.

Saved keys are injected only into their engine's process and are readable by shell commands that process starts. Cursor credentials are not added to other engines' environments; existing globally allowed provider prefixes remain as described below. OpenCode strips ambient foreign API keys, removes configuration override variables, and pins project configuration off. Additional OpenCode provider-key maps are not implemented.

`python -m app.server doctor` reports which engines it found and whether they are signed in.

Engine processes (and any shell command or tool server they start) get a limited environment, not the server's: standard shell and OS names, proxy and certificate names, and the engines' own settings and provider keys (`ANTHROPIC_*`, `CLAUDE_*`, `CODEX_*`, `OPENAI_*`, `GEMINI_*`, `GOOGLE_API_KEY`). Cloud-account names (`AWS_*` for Bedrock, `GOOGLE_APPLICATION_CREDENTIALS` for Vertex, `AZURE_*`) and `SSH_AUTH_SOCK` are not passed by default. To give a turn another variable, such as `AWS_*` for Bedrock or a `GH_TOKEN` for `gh`, list it in `OPEN_DOTS_ENGINE_ENV` (comma-separated names, or a prefix ending in `*`). The app's own secrets, database settings and `OPEN_DOTS_*` names are never passed, even when listed. This limits what a steered turn can read from its environment; it does not stop it reading files the server's account can read.

## The proxy rules

Any reverse proxy works if it:

1. keeps the real `Host` header (never rewrite it to `127.0.0.1`);
2. sets `X-Forwarded-For` to the visitor's address and drops any the visitor sent. Without it every visitor looks like the proxy, so they share one sign-in lockout and one person's wrong guesses can lock you out;
3. sets `X-Forwarded-Proto` (`https`). Without it the sign-in cookie is not marked `Secure`;
4. does not buffer the live update stream (server-sent events) and does not cut a quiet stream: `flush_interval -1` in Caddy, `proxy_buffering off` in nginx;
5. lets uploads of at least 12 MB through (the server's own limit for an image is 10 MB);
6. forwards `/hooks/*` to the same place, or to the optional hooks-only process ([ROUTINES.md](ROUTINES.md) "Ingress").

The configurations in [`deploy/proxy/`](../deploy/proxy/) do all of this and are the ones that were tested. The server logs a warning, once, if a visit to your public name arrives without the first three.

## Who is believed to be the proxy

The server believes `X-Forwarded-For` and `X-Forwarded-Proto` only from the addresses in `TRUSTED_PROXIES`, and by default that is loopback (`127.0.0.1` and `::1`) and nothing else. Anyone else who sends those headers is ignored, so a visitor cannot pick which address their failed sign-ins are counted against, or make the server think a plain request was HTTPS.

- **A proxy on the same machine** connects from loopback: set nothing.
- **A proxy container from `deploy/proxy/`**: the overlay gives it a fixed address and trusts exactly that address. Do not attach other containers to that network.
- **A proxy on the host, with the Docker route:** the app sees it as the network's gateway address. Set `OPEN_DOTS_TRUSTED_PROXIES` to that address (`172.31.250.1` by default). On a native Linux engine that stands for the host. **On Docker Desktop, other containers can reach the app and arrive as the same address, so do not use it there for anything but your own machine.** `scripts/probe-docker-trust.sh` shows what your Docker host does.
- **A proxy on another machine:** name its address.

Name the proxy's own address, never a whole network: a network is every container ever attached to it. `*`, `/0` networks and host names are refused at start-up, and `FORWARDED_ALLOW_IPS` is ignored.

Never open the server's own port (8000) to the internet. It listens on the machine's loopback only; in a container it listens on the container's address, and Docker publishes it on `127.0.0.1`. Do not change `OPEN_DOTS_BIND` to a public address without a proxy that terminates HTTPS in front.

## Backing up and restoring

One command makes an archive of the database (a consistent copy while the server runs), attachments, decision log, notes and the agents' workspace folder:

<!-- verify:account -->
```sh
cd /opt/open-dots/backend && set -a; . /etc/open-dots/open-dots.env; set +a
.venv/bin/python -m app.server backup      # in Docker: docker compose exec open-dots python -m app.server backup
```

It lands in `backups/` beside the database (in Docker, `/data/backups`). Copy it off the server. **It does not contain** the encryption key, the engines' own sign-ins, folders outside the workspace root that an agent was pointed at, or an engine's memory of a conversation. Keep the archive as private as the server: it holds your conversations in clear.

Restore on a machine with an empty data folder, with the same `TOKEN_ENCRYPTION_KEYS` in the settings and the server stopped:

<!-- verify:account HOME=/tmp/restored -->
```sh
cd /opt/open-dots/backend && set -a; . /etc/open-dots/open-dots.env; set +a
.venv/bin/python -m app.server restore /path/to/open-dots-backup-….tar.gz
.venv/bin/python -m app.server migrate     # needed if the archive is from an older version
```

A restore refuses to touch anything that exists and refuses while a server is running. If the workspace folder is somewhere else on the new machine, the stored paths are moved to match. Sign in to the engines again afterwards. PostgreSQL is not covered: use `pg_dump`.

## Safety

- Anyone with your passphrase, or a paired `admin` device, can run commands on the server through your agents. Use a long passphrase and pair phones with *Chat only*; revoke a lost device at once (**Settings → Devices**, or `python -m app.auth.cli revoke ID`).
- With tools on, every command still asks for your approval in the app unless you saved a rule. Consider an agent computer ([COMPUTERS.md](COMPUTERS.md)) for agents that run commands.
- Keep `LOOPBACK_TRUST=off`. Run one server process per data folder (a second one is refused). The sign-in lockout counters are in memory, so also rate-limit sign-in and pairing in the proxy if you want more than that.
- The server answers only to your public name and the machine's own names, and refuses other `Host` headers.

## Troubleshooting

Start with `python -m app.server doctor`. Then:

- **"Refusing to listen on …"**: `OPEN_DOTS_HOST` is not loopback. Use `127.0.0.1` behind a proxy, or `OPEN_DOTS_ALLOW_PUBLIC_BIND=1` if you mean it (the Docker image does).
- **"… has no index.html"**: the pages are not built, or `OPEN_DOTS_UI_DIR` points elsewhere. Run `node scripts/build-ui.js`.
- **"The database has no tables yet / is from an older version"**: run `python -m app.server migrate` (the service and the container do this on every start).
- **"cannot be decrypted with the encryption key"**: `TOKEN_ENCRYPTION_KEYS` is not the key this database was made with. Restore the right key; nothing was changed.
- **Agents do not run**: `doctor` reports local execution off. Set `LOCAL_EXECUTION_ENABLED=true`.
- **`untrusted_host` (421)**: the name in the address bar is not your public name. Check `OPEN_DOTS_PUBLIC_URL` and that the proxy passes the real `Host`.
- **A sign-in loop, or cookie trouble**: the proxy is not sending `X-Forwarded-Proto: https`, or the server does not believe it (see [Who is believed to be the proxy](#who-is-believed-to-be-the-proxy)), or you opened the site over plain `http://`.
- **The log warns about `X-Forwarded-For`**: see [the proxy rules](#the-proxy-rules).
- **Live updates stall or the chat seems frozen until you reload**: the proxy is buffering the event stream.

## What has been checked

Each line says what was run. "Not run" means exactly that: do not assume it works.

| Claim | Status |
|---|---|
| Server mode, `migrate`, `doctor`, `backup`, `restore`, `service`, sign-in, pairing, a real conversation with approvals and routines, SIGTERM and restart | Run on every change in CI (`scripts/smoke-server.py`, `scripts/smoke-server-chat.py`, the backend suite on SQLite and PostgreSQL) |
| The Docker image and compose file | Run on Docker Desktop (`scripts/smoke-docker.py`) |
| nginx and Caddy overlays with TLS, the headers the server relies on, a forged header not believed, streaming, uploads, pairing | Run on Docker Desktop with real TLS, both proxies (`scripts/smoke-proxy.py`) |
| The steps above in the guide | Each command in the marked blocks was run from this file's own text: `scripts/verify-guide.py` |
| Gateway address means "the host" for a host-side proxy | Measured on Docker Desktop (it does not) and inside Docker-in-Docker (it does); **not run on a real Linux host** |
| Real public certificates (Caddy or certbot) | **Not run** |
| The systemd unit actually starting and stopping the service | **Not run** (the unit is checked with `systemd-analyze verify` only) |
| Ubuntu 26, rootless Docker, Podman, Traefik, a tunnel | **Not run** |
| Agent computers on a server (direct install) | **Not run**; not possible in the Docker route by design |
| Signing in to a real engine inside the container | **Not run** (the build step that installs a package was run with a harmless one) |
| PostgreSQL as the server's database | The backend suite passes on it; the server routes above were run on SQLite |

See [ROADMAP.md](ROADMAP.md) for what is next.
