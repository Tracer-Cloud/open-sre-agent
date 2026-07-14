-- Initial schema for Slack surface + org-scoped investigations (Postgres).
-- Owner: gateway migrations (Alembic recommended); this file is the agreed shape.
-- Apply via future Alembic revision under gateway/storage/migrations/.

CREATE TABLE IF NOT EXISTS slack_installs (
    team_id TEXT PRIMARY KEY,
    bot_token TEXT NOT NULL,
    bot_user_id TEXT NOT NULL DEFAULT '',
    clerk_org_id TEXT NOT NULL,
    installed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY,
    surface TEXT NOT NULL,
    team_id TEXT,
    channel_id TEXT,
    thread_ts TEXT,
    clerk_org_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS sessions_slack_thread_uidx
    ON sessions (team_id, channel_id, thread_ts)
    WHERE surface = 'slack' AND team_id IS NOT NULL AND channel_id IS NOT NULL AND thread_ts IS NOT NULL;

-- Column set must match gateway/http/postgres_store.py (_COLUMNS) — the
-- runtime store is the authoritative shape.
CREATE TABLE IF NOT EXISTS investigations (
    id TEXT PRIMARY KEY,
    session_id UUID REFERENCES sessions (id),
    clerk_org_id TEXT NOT NULL,
    workspace_id TEXT,
    status TEXT NOT NULL,
    trigger JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    report_local_path TEXT,
    report_s3_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS investigations_org_created_idx
    ON investigations (clerk_org_id, created_at DESC);
