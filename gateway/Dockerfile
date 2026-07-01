# Dockerfile for the Telegram Gateway process.
# Runs the two-way long-poll gateway: opensre gateway telegram.
#
# Usage:
#   docker build -t opensre-gateway:latest -f gateway/Dockerfile .
#   docker run --env-file infra/deploy_gateway/gateway.env.example opensre-gateway:latest
#
# Required env vars (see infra/deploy_gateway/gateway.env.example):
#   TELEGRAM_BOT_TOKEN      - BotFather token
#   TELEGRAM_ALLOWED_USERS  - comma-separated Telegram user IDs
#   LLM_PROVIDER            - openai | anthropic | bedrock
#   OPENAI_API_KEY or ANTHROPIC_API_KEY

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

CMD ["opensre", "gateway", "telegram"]
