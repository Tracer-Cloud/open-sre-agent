"""The ``investigations`` table: name, columns and DDL. Applied by ``migrations``."""

from __future__ import annotations

TABLE = "investigations"

COLUMNS = (
    "id, clerk_org_id, workspace_id, status, trigger, error, "
    "report_local_path, report_s3_key, created_at, updated_at"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS investigations (
    id TEXT PRIMARY KEY,
    clerk_org_id TEXT NOT NULL,
    workspace_id TEXT,
    status TEXT NOT NULL,
    trigger JSONB NOT NULL,
    error TEXT,
    report_local_path TEXT,
    report_s3_key TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS workspace_id TEXT;
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS error TEXT;
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS report_local_path TEXT;
CREATE INDEX IF NOT EXISTS investigations_status_created
    ON investigations (status, created_at);
"""

__all__ = ["COLUMNS", "SCHEMA", "TABLE"]
