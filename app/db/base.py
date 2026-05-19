"""SQLAlchemy declarative base and TenantMixin for row-level tenant isolation.

All persistent models must inherit TenantMixin so every row carries a tenant_id.
The current tenant is stored in a ContextVar so it is safe across threads and
async tasks without any global state.
"""

from __future__ import annotations

from contextvars import ContextVar

from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

_current_tenant: ContextVar[str] = ContextVar("current_tenant", default="dev")


def get_current_tenant() -> str:
    """Return the tenant_id active in the current execution context."""
    return _current_tenant.get()


def set_current_tenant(tenant_id: str) -> None:
    """Set the tenant_id for the current execution context."""
    _current_tenant.set(tenant_id)


class Base(DeclarativeBase):
    pass


class TenantMixin:
    """Mixin that adds a tenant_id column and auto-populates it on insert."""

    # Subclasses must define id; declared here so repositories can access it.
    id: Mapped[str]

    @declared_attr
    def tenant_id(cls) -> Mapped[str]:
        return mapped_column(String(36), nullable=False, index=True, default=get_current_tenant)
