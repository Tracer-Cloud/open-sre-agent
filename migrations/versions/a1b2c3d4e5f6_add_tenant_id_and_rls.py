"""add_tenant_id_and_rls

Adds tenant_id column to all tables (backfilling existing rows with 'dev'),
then enables Postgres Row Level Security so queries are tenant-scoped at the
DB engine level as a belt-and-suspenders defense.

Revision ID: a1b2c3d4e5f6
Revises: fb7c1119844f
Create Date: 2026-05-19 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'fb7c1119844f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ["investigations", "alerts", "investigation_steps"]


def upgrade() -> None:
    """Add tenant_id to all tables, backfill existing rows, enable Postgres RLS."""
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    for table in _TABLES:
        # Step 1: Add the column as nullable so existing rows are accepted.
        op.add_column(table, sa.Column("tenant_id", sa.String(36), nullable=True))

        # Step 2: Backfill existing rows with the default tenant.
        op.execute(f"UPDATE {table} SET tenant_id = 'dev' WHERE tenant_id IS NULL")  # noqa: S608

        # Step 3: Enforce NOT NULL now that all rows have a value.
        op.alter_column(table, "tenant_id", nullable=False)

        # Step 4: Index for fast per-tenant scans.
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])

    if is_postgres:
        _enable_rls()


def _enable_rls() -> None:
    """Enable Row Level Security on all tables (Postgres-only)."""
    for table in _TABLES:
        # Enable RLS.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

        # Superusers and table owners bypass RLS by default; force it for owners too.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

        # Create a permissive policy using the Postgres session variable.
        # The app sets SET LOCAL app.current_tenant_id = '<id>' at session start.
        op.execute(
            f"CREATE POLICY tenant_isolation_policy ON {table} "
            f"USING (tenant_id = current_setting('app.current_tenant_id', true))"
        )


def downgrade() -> None:
    """Remove tenant_id columns and RLS policies."""
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        for table in _TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    for table in _TABLES:
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_column(table, "tenant_id")
