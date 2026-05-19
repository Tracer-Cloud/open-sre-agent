"""initial_schema

Revision ID: fb7c1119844f
Revises:
Create Date: 2026-05-19 18:23:40.381794

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fb7c1119844f'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create base tables without tenant_id (pre-multi-tenancy schema)."""
    op.create_table(
        "investigations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("thread_id", sa.String(36), nullable=False),
        sa.Column("alert_name", sa.String(255), nullable=False),
        sa.Column("pipeline_name", sa.String(255), nullable=False, server_default="unknown"),
        sa.Column("severity", sa.String(50), nullable=False, server_default="warning"),
        sa.Column("root_cause", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "investigation_id",
            sa.String(36),
            sa.ForeignKey("investigations.id"),
            nullable=False,
        ),
        sa.Column("alert_name", sa.String(255), nullable=False),
        sa.Column("alert_source", sa.String(100), nullable=False, server_default="unknown"),
        sa.Column("raw_alert", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "investigation_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "investigation_id",
            sa.String(36),
            sa.ForeignKey("investigations.id"),
            nullable=False,
        ),
        sa.Column("action_name", sa.String(255), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("step_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    """Drop base tables."""
    op.drop_table("investigation_steps")
    op.drop_table("alerts")
    op.drop_table("investigations")
