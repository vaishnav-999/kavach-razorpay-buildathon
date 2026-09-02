# ─────────────────────────────────────────────────────────────────────────────
# STAGE 1 — the frontend (BUILD_SPEC §14, §19.1).
#
# Vite writes to `build.outDir = '../app/static'`, so from /build the bundle
# lands at /app/static. The python stage copies that directory; nothing about
# the frontend — node, npm, node_modules — survives into the runtime image.
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS frontend

WORKDIR /build

# Dependencies first, so a source-only change does not re-resolve the tree.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# ─────────────────────────────────────────────────────────────────────────────
# STAGE 2 — the application.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# psycopg2-binary needs libpq at runtime; curl is used by the compose healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY app/ ./app/

# The built console. Copied after app/ so the image always carries the bundle
# this build produced, never one left over in a developer's working tree.
COPY --from=frontend /app/static ./app/static

EXPOSE 8000

# Migrate, then serve. Bind 0.0.0.0 and honour $PORT — Render sets it.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
