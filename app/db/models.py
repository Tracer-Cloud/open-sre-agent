"""SQLAlchemy ORM models for HealOps persistent state.

Every model inherits TenantMixin so all rows carry a tenant_id column that
is auto-populated from the current execution context on insert.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantMixin


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Investigation(TenantMixin, Base):
    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False)
    alert_name: Mapped[str] = mapped_column(String(255), nullable=False)
    pipeline_name: Mapped[str] = mapped_column(String(255), nullable=False, default="unknown")
    severity: Mapped[str] = mapped_column(String(50), nullable=False, default="warning")
    root_cause: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    alerts: Mapped[list[Alert]] = relationship("Alert", back_populates="investigation")
    steps: Mapped[list[InvestigationStep]] = relationship(
        "InvestigationStep", back_populates="investigation"
    )


class Alert(TenantMixin, Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    investigation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("investigations.id"), nullable=False
    )
    alert_name: Mapped[str] = mapped_column(String(255), nullable=False)
    alert_source: Mapped[str] = mapped_column(String(100), nullable=False, default="unknown")
    raw_alert: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    investigation: Mapped[Investigation] = relationship("Investigation", back_populates="alerts")


class InvestigationStep(TenantMixin, Base):
    __tablename__ = "investigation_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    investigation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("investigations.id"), nullable=False
    )
    action_name: Mapped[str] = mapped_column(String(255), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    step_order: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    investigation: Mapped[Investigation] = relationship("Investigation", back_populates="steps")
