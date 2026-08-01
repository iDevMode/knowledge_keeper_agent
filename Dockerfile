# ---- Stage 1: Build frontend ----
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python app ----
FROM python:3.12-slim
WORKDIR /app

# System deps for python-docx (minimal — no WeasyPrint for now)
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt anthropic

# Copy app code
COPY . .

# Copy built frontend
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Expose port (Railway uses $PORT env var)
EXPOSE 8321

# Worker count is configurable and defaults to 1.
#
# More than one worker is only safe with DATABASE_URL set (review finding H3).
# Without it, sessions, LangGraph checkpoints and generated documents live in
# process memory, so a session created by worker A is invisible to worker B and
# the next message 404s. With it, all state is shared and a session's graph run
# is serialised across workers by a Postgres advisory lock.
#
# The default stays 1 so that raising it is a deliberate act, taken once
# Postgres is provisioned and after checking the connection pool has room:
# each worker opens its own pools, so total connections scale with WEB_CONCURRENCY.
CMD ["sh", "-c", "python -m uvicorn api.routes:app --host 0.0.0.0 --port ${PORT:-8321} --timeout-keep-alive 600 --workers ${WEB_CONCURRENCY:-1}"]
