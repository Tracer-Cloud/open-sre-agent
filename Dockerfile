# Unified Dockerfile for OpenSRE
# Supports two runtime modes via MODE environment variable:
#   MODE=web      - FastAPI web API (health, alerts, async investigations)
#   MODE=gateway  - Two-way messaging gateway (Slack Socket Mode + Telegram)
#
# EC2 deploy (make deploy) runs both as separate containers on one instance.
#
# Web mode usage:
#   docker build -t opensre:latest .
#   docker run -p 8000:8000 --env-file .env opensre:latest
#   curl http://localhost:8000/health
#
# Gateway mode usage:
#   docker build -t opensre-gateway:latest .
#   docker run -e MODE=gateway --env-file .env opensre-gateway:latest
#
# Required env vars for gateway mode:
#   SLACK_BOT_TOKEN + SLACK_APP_TOKEN (Slack) and/or TELEGRAM_BOT_TOKEN +
#   TELEGRAM_ALLOWED_USERS (Telegram), plus LLM_PROVIDER and API keys

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

# postgresql extra: psycopg2 for the DATABASE_URL-backed investigations store.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[postgresql]"

# Run as a non-root user (uid/gid 1000). /workspace is the writable runtime
# working area owned by that user.
RUN groupadd --gid 1000 opensre \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin opensre \
    && mkdir -p /workspace/scratch \
    && chown -R opensre:opensre /workspace

ENV PORT=8000
ENV MODE=web
ENV HOME=/home/opensre
# site-packages is root-owned; skip bytecode writes the non-root user can't make.
ENV PYTHONDONTWRITEBYTECODE=1

# Note: EXPOSE and HEALTHCHECK only apply to web mode
# Gateway mode uses outbound-only long-polling (no inbound HTTP)
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD if [ "$MODE" = "web" ]; then python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)" || exit 1; else exit 0; fi

USER opensre

CMD ["sh", "-c", "if [ \"$MODE\" = \"gateway\" ]; then exec opensre gateway start --foreground; else exec uvicorn gateway.http.webapp:app --host 0.0.0.0 --port ${PORT:-8000}; fi"]
