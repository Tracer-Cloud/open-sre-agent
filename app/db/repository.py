"""TenantRepository: base repository that automatically scopes all queries to the current tenant.

All concrete repositories must extend TenantRepository instead of accessing
session.query(Model) directly.  This ensures that even a code-level bug cannot
return cross-tenant data — the WHERE tenant_id = <current> filter is always applied.
"""

from typing import Any, cast

from sqlalchemy.orm import Session

from app.db.base import TenantMixin, get_current_tenant


class TenantRepository[T: TenantMixin]:
    """Base repository that auto-scopes every query to the current tenant."""

    def __init__(self, db: Session, model: type[T]) -> None:
        self.db = db
        self.model = model

    def query(self) -> Any:
        """Return a query pre-filtered to the current tenant."""
        return self.db.query(self.model).filter(
            self.model.tenant_id == get_current_tenant()
        )

    def add(self, obj: T) -> T:
        """Stamp obj with the current tenant and add to the session."""
        obj.tenant_id = get_current_tenant()  # type: ignore[assignment]
        self.db.add(obj)
        return obj

    def get(self, obj_id: str) -> T | None:
        """Fetch a single record by primary key, scoped to the current tenant."""
        return cast(T | None, self.query().filter(self.model.id == obj_id).first())

    def all(self) -> list[T]:
        """Return all records for the current tenant."""
        return cast(list[T], self.query().all())

    def delete(self, obj: T) -> None:
        """Delete a record; verifies tenant ownership via the scoped query."""
        owned = self.get(obj.id)
        if owned is not None:
            self.db.delete(owned)
