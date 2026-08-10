# Unified Dockerfile for OpenSRE
# Supports two runtime modes via MODE environment variable:
#   MODE=web      - FastAPI web API (health, alerts, async investigations)
#   MODE=gateway  - Two-way messaging gateway (Slack Socket Mode + Telegram)
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

# --- gke-gcloud-auth-plugin ---------------------------------------------------
# A GKE kubeconfig — whether written by `opensre integrations add-gke-clusters`
# or by `gcloud container clusters get-credentials` — carries no credential. It
# execs `gke-gcloud-auth-plugin` to mint a token per connection. Without that
# binary on PATH the kubernetes_* tools cannot reach any GKE cluster from inside
# this image, and add-gke-clusters refuses to write a kubeconfig at all.
#
# Only the ~10MB binary is taken. `apt-get install` would pull in
# google-cloud-cli (~1GB) as a dependency, which nothing else here needs:
# `apt-get download` fetches the .deb without its dependencies and `dpkg-deb -x`
# unpacks it without running maintainer scripts. The .deb is architecture-
# specific, so this stage must build for the same platform as the final image.
FROM python:3.12-slim AS gke-auth-plugin

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
        | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg]" \
        "https://packages.cloud.google.com/apt cloud-sdk main" \
        > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && apt-get update \
    && cd /tmp \
    && apt-get download google-cloud-cli-gke-gcloud-auth-plugin \
    && dpkg-deb -x /tmp/google-cloud-cli-gke-gcloud-auth-plugin_*.deb /tmp/extracted \
    && install -m 0755 \
        /tmp/extracted/usr/lib/google-cloud-sdk/bin/gke-gcloud-auth-plugin \
        /usr/local/bin/gke-gcloud-auth-plugin \
    && rm -rf /var/lib/apt/lists/* /tmp/extracted /tmp/*.deb

# --- runtime ------------------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=gke-auth-plugin /usr/local/bin/gke-gcloud-auth-plugin /usr/local/bin/gke-gcloud-auth-plugin

COPY . /app

# Dependencies come from uv.lock, not from the ranges in pyproject.toml.
#
# `pip install .` re-resolves every range at build time, so the image drifts away
# from the versions CI tests the moment an upstream release lands — and it does so
# silently, with no diff and no failing test. That is not hypothetical: an image
# built this way resolved mcp 2.0.0 against a lock pinning 1.28.1, and the two
# breaking changes in between (a transport yield arity, then `CallToolResult
# .isError`) each surfaced as a runtime AttributeError in a deployed pod, on a
# code path CI had exercised green minutes earlier.
#
# `uv export` flattens the lock to a pinned requirements file with hashes; pip
# installs exactly that, then the project itself with --no-deps so nothing is
# re-resolved behind it. uv is pinned too and uninstalled afterwards — it is a
# build tool, not a runtime dependency.
#
# postgresql extra: psycopg2 for the DATABASE_URL-backed investigations store.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir "uv==0.12.1" \
    && uv export \
        --locked \
        --no-dev \
        --extra postgresql \
        --no-emit-project \
        --format requirements-txt \
        -o /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir --no-deps . \
    && pip uninstall -y uv \
    && rm -f /tmp/requirements.txt

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

CMD ["sh", "-c", "if [ \"$MODE\" = \"gateway\" ]; then exec opensre gateway start --foreground; else exec uvicorn gateway.web.webapp:app --host 0.0.0.0 --port ${PORT:-8000}; fi"]
