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

# Run with uvicorn — single worker is fine for MVP
CMD ["sh", "-c", "python -m uvicorn api.routes:app --host 0.0.0.0 --port ${PORT:-8321} --timeout-keep-alive 600"]
