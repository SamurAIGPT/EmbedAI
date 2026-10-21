# Open Dots as a server: the backend and its web pages in one image.
#
#   docker build -t open-dots .
#   docker build --build-arg ENGINES="@anthropic-ai/claude-code @openai/codex" -t open-dots .
#
# The image listens on 0.0.0.0:8000 inside the container (the container's own address; nothing is
# public until you publish a port: compose.yaml publishes it on the host's loopback only). Put any
# reverse proxy in front, and list the proxy's network in TRUSTED_PROXIES so its
# X-Forwarded-For and X-Forwarded-Proto are believed (compose.yaml does this for its own network).
#
# What is NOT in the image, on purpose:
#   - the encryption key (TOKEN_ENCRYPTION_KEYS) and every other setting: they come from the
#     environment at run time (compose.yaml reads a settings file you keep outside the image);
#   - any engine CLI, unless you name it in ENGINES at build time (space-separated npm packages).
#     Only those packages are installed. No credentials are ever baked in: the build runs with a
#     throwaway home folder that is discarded, and an engine's sign-in lives under /data (the
#     volume) once you sign in inside the running container;
#   - Docker access, so agent computers (Docker containers) are not available inside this image.
# Installing an engine into a running container is lost when the container is replaced, unless its
# folder is on the volume; rebuilding with ENGINES is the supported route.
#
# Layers are ordered by how often they change: the base images, then the dependencies, then the
# engines, then the app files, so a code change rebuilds only the small last layers.

ARG PYTHON_IMAGE=python:3.14-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim

# 1. The web pages: a static export that talks to the server it is loaded from.
FROM ${NODE_IMAGE} AS pages
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json frontend/
RUN cd frontend && npm ci --no-audit --no-fund
COPY frontend frontend
COPY scripts/build-ui.js scripts/build-ui.js
RUN node scripts/build-ui.js

# 2. The backend's dependencies, from the lock file, without the development group.
FROM ${PYTHON_IMAGE} AS backend
COPY --from=ghcr.io/astral-sh/uv:0.9.30 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 3. Engine CLIs, only the ones named at build time. Built in a throwaway home folder and copied as
#    a plain tree, so no cache or login can reach the final image. Node is copied along only when an
#    engine is installed: the engines are Node programs, the rest of the image is not.
FROM ${NODE_IMAGE} AS engines
ARG ENGINES=""
RUN mkdir -p /out/bin /out/lib \
 && if [ -n "$ENGINES" ]; then \
      HOME=/tmp/build-home npm install -g --prefix /out --no-audit --no-fund $ENGINES \
      && cp "$(command -v node)" /out/bin/node; \
    fi \
 && rm -rf /tmp/build-home /root/.npm

# 4. The server.
FROM ${PYTHON_IMAGE} AS runtime
# tini: collects the processes an engine leaves behind and passes the stop signal on, however the
# container is started. git and ca-certificates: agents run git and talk to HTTPS endpoints.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home --home-dir /data --shell /bin/bash opendots \
 && chown opendots:opendots /data
COPY --from=backend /opt/venv /opt/venv
COPY --from=engines /out /opt/engines
COPY backend /app/backend
COPY --from=pages /src/frontend/out /app/ui

# HOME is the volume, so an engine's sign-in (~/.claude, ~/.codex) and everything the app keeps
# survive replacing the container. TOKEN_ENCRYPTION_KEYS and the rest come at run time.
ENV PATH=/opt/venv/bin:/opt/engines/bin:$PATH \
    HOME=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPEN_DOTS_HOST=0.0.0.0 \
    OPEN_DOTS_PORT=8000 \
    OPEN_DOTS_ALLOW_PUBLIC_BIND=1 \
    OPEN_DOTS_UI_DIR=/app/ui \
    DATABASE_BACKEND=sqlite \
    SQLITE_PATH=/data/open-dots.db \
    ATTACHMENTS_DIR=/data/attachments \
    DECISION_LOG_DIR=/data/decisions \
    LOCAL_WORKSPACE_ROOT=/data/workspace \
    ENGINE_PROCESS_DIR=/data/engines
WORKDIR /app/backend
VOLUME ["/data"]
EXPOSE 8000
USER opendots

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"]

# Prepare the database before serving, as the systemd unit does: creates it, or updates it after an
# upgrade (a SQLite file is copied aside first). A database that is not ready stops the container
# here, with the reason in its log, instead of serving.
ENTRYPOINT ["tini", "--"]
CMD ["sh", "-c", "python -m app.server migrate && exec python -m app.server"]
