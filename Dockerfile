# ─── Stage 1: Builder ────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ─── Stage 2: Runtime ────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Runtime system deps (libpq for psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy project source
COPY . .

# Create the db and logs directories
RUN mkdir -p db logs

# Expose API port
EXPOSE 8000

# Environment defaults (override via docker-compose or cloud platform)
ENV PORT=8000 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# Health-check every 30s
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Run with Gunicorn + Uvicorn workers (production ASGI)
CMD gunicorn backend.api:app \
        --workers ${GUNICORN_WORKERS:-2} \
        --worker-class uvicorn.workers.UvicornWorker \
        --bind 0.0.0.0:${PORT} \
        --timeout 120 \
        --keep-alive 5 \
        --log-level info \
        --access-logfile - \
        --error-logfile -
