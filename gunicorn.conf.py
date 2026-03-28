"""
Gunicorn configuration file.
Used when starting with: gunicorn -c gunicorn.conf.py backend.api:app
"""

import os
import multiprocessing

# ── Binding ───────────────────────────────────────────────────────────────────
bind    = f"0.0.0.0:{os.getenv('PORT', '8000')}"
backlog = 2048

# ── Workers ───────────────────────────────────────────────────────────────────
# Use GUNICORN_WORKERS env var; default to (2 × CPU cores) + 1
workers     = int(os.getenv("GUNICORN_WORKERS", (2 * multiprocessing.cpu_count()) + 1))
worker_class = "uvicorn.workers.UvicornWorker"
threads     = 1          # Uvicorn workers are async — threads not needed
worker_connections = 1000

# ── Timeouts ──────────────────────────────────────────────────────────────────
timeout      = 120       # LLM calls can be slow; give them 2 min
keepalive    = 5
graceful_timeout = 30

# ── Logging ───────────────────────────────────────────────────────────────────
loglevel     = os.getenv("LOG_LEVEL", "info").lower()
accesslog    = "-"       # stdout
errorlog     = "-"       # stderr
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)sμs'

# ── Server mechanics ──────────────────────────────────────────────────────────
preload_app  = True      # Load app code before forking (saves memory)
daemon       = False
pidfile      = None
user         = None
group        = None
tmp_upload_dir = None

# ── Reload (dev only) ─────────────────────────────────────────────────────────
reload = os.getenv("GUNICORN_RELOAD", "false").lower() == "true"
