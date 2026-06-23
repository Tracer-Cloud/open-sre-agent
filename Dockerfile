# Production Dockerfile for OpenSRE
# Runs the FastAPI health application (see app/webapp.py).
#
# Usage:
#   docker build -t opensre:latest .                                # default port 8000
#   docker build --build-arg PORT=8080 -t opensre:latest .          # custom port
#   docker run -p 8000:8000 --env-file .env opensre:latest
#
# Health check (uses PORT env var, defaults to 8000):
#   curl http://localhost:8000/health

FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/Tracer-Cloud/opensre"
LABEL org.opencontainers.image.description="OpenSRE - Build Your Own AI SRE Agents"
LABEL org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

ARG PORT=8000
ENV PORT=${PORT}
EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request, os; port = os.environ.get('PORT', '8000'); urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=5)" || exit 1

CMD ["sh", "-c", "exec uvicorn app.webapp:app --host 0.0.0.0 --port ${PORT:-8000}"]
