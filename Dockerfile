# =============================================================================
# WebUI image — multi-stage: builds the SvelteKit SPA, then runs FastAPI
# serving the SPA + API same-origin (no CORS).
# =============================================================================

# ---- Stage 1: build the SvelteKit SPA (adapter-static) ---------------------
FROM node:22-slim AS frontend
WORKDIR /build
# CI install first for layer caching.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
# Output: /build/build/{index.html, _app/...}

# ---- Stage 2: python runtime with docker-cli ------------------------------
FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ----------------------------------------------------------------------------
# docker-cli: the WebUI spawns per-user agent containers via `docker run` /
# `docker exec`. We install ONLY the CLI, not the daemon. The daemon stays on
# the host; the WebUI reaches it through /var/run/docker.sock bind-mounted by
# compose. On python:3.13-slim (debian trixie) the right package is
# `docker-cli` — `docker.io` pulls containerd + dockerd but NO `docker`
# binary at /usr/bin/docker. Found via `apt-cache search docker-cli` and
# `dpkg -L docker-cli`.
# ----------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
      docker-cli \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && docker --version

# ----------------------------------------------------------------------------
# Runs as root intentionally. Two reasons:
#   1. container_service.os.chown(ws_path, 1000, 1000) requires CAP_CHOWN to
#      hand the host-side workspace dir to uid 1000 (the agent user).
#   2. The bind-mounted /var/run/docker.sock is owned by root:docker on the
#      host; root inside the container can write to it without group juggling.
# Hardening (post-Iter 4): drop to a non-root user and add CAP_CHOWN + the
# host docker GID. Keep that change small by centralizing chown in a tiny
# setuid helper or by passing --user 1000 with supplemental --group <docker_gid>.
# ----------------------------------------------------------------------------
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/

# SPA static assets from stage 1 (SvelteKit adapter-static output).
COPY --from=frontend /build/build ./static

# SQLite + checkpointer + container registry live here. compose bind-mounts
# ./data on the host to /app/data so DBs survive image rebuilds.
RUN mkdir -p /app/data

EXPOSE 8080

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
