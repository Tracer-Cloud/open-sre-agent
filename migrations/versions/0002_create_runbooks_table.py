"""create runbooks table with pgvector index

Revision ID: 0002_create_runbooks_table
Revises: 0001_enable_pgvector
Create Date: 2026-05-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]

revision: str = "0002_create_runbooks_table"
down_revision: Union[str, Sequence[str], None] = "0001_enable_pgvector"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.create_table(
        "runbooks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("tags", sa.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("source_url", sa.String(2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index("ix_runbooks_tenant_id", "runbooks", ["tenant_id"])

    # IVFFlat index for approximate cosine similarity search, partitioned by tenant.
    # Requires the table to have data before CREATE INDEX USING ivfflat succeeds.
    # In production, run this index creation after the initial bulk import.
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_runbooks_tenant_embedding
        ON runbooks
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_runbooks_tenant_embedding", table_name="runbooks")
    op.drop_index("ix_runbooks_tenant_id", table_name="runbooks")
    op.drop_table("runbooks")
