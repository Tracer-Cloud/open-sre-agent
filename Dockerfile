# Production Dockerfile for OpenSRE
# Runs the FastAPI health application (see app/webapp.py).
#
# Usage:
#   docker build -t opensre:latest .
#   docker run -p 8000:8000 --env-file .env opensre:latest
#
# Health check:
#   curl http://localhost:8000/health

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)" || exit 1

CMD ["sh", "-c", "exec uvicorn app.webapp:app --host 0.0.0.0 --port ${PORT:-8000}"]
