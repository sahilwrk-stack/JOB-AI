# Heroku / Render (non-Docker) process file
# Render uses this if no Dockerfile is present
web: gunicorn backend.api:app --workers 2 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT --timeout 120 --log-level info
