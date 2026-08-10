"""Connection string for the gateway's durable datastore.

Two names are in circulation and both have to work. The Helm chart, the
`.env.example` template and every deployment doc in this repo say
``DATABASE_URI``; hosted platforms that provision a database for you — Railway,
Heroku and their lookalikes — inject ``DATABASE_URL`` and give you no say in
the name. Reading only one of them means a deployment that looks correctly
configured silently falls back to the process-local in-memory store, which
loses every queued investigation on restart and cannot be shared between the
web and gateway pods.

``DATABASE_URI`` wins when both are set: it is the name this repo's own
tooling emits, so it is the one an operator here chose deliberately.

Distinct from ``config.constants.postgresql``, which carries the discrete
host/port/user fields of the PostgreSQL *integration* the agent queries on a
user's behalf. This is the platform's own storage.
"""

from __future__ import annotations

import os
from typing import Final

#: Preferred name — emitted by this repo's Helm chart and `.env.example`.
DATABASE_URI_ENV: Final[str] = "DATABASE_URI"

#: Injected by hosted platforms that provision the database for you.
DATABASE_URL_ENV: Final[str] = "DATABASE_URL"


def database_dsn() -> str:
    """The datastore connection string, or ``""`` when none is configured."""
    for name in (DATABASE_URI_ENV, DATABASE_URL_ENV):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


__all__ = ["DATABASE_URI_ENV", "DATABASE_URL_ENV", "database_dsn"]
