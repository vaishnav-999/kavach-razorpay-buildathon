# ─────────────────────────────────────────────────────────────────────────────
# STAGE 1 — frontend build. Added at M7 (BUILD_SPEC §19.1):
#
#   FROM node:20-alpine AS frontend
#   WORKDIR /build
#   COPY frontend/package*.json ./
#   RUN npm ci
#   COPY frontend/ ./
#   RUN npm run build                 # vite build.outDir = '../app/static'
#
# Stage 2 then does:  COPY --from=frontend /app/static ./app/static
# Until M7, app/static/ holds only the placeholder index.html.
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

EXPOSE 8000

# Migrate, then serve. Bind 0.0.0.0 and honour $PORT — Render sets it.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
