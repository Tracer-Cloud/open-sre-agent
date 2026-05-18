"""Local integration credential store.

Integrations are stored in ~/.config/opensre/integrations.json.

File format (v2 — see ``_migrate_record_v1_to_v2`` for the v1 shape):
{
  "version": 2,
  "integrations": [
    {
      "id": "grafana-1",
      "service": "grafana",
      "status": "active",
      "instances": [
        {
          "name": "prod",
          "tags": {"env": "prod"},
          "credentials": {"endpoint": "https://...", "api_key": "..."}
        }
      ]
    }
  ]
}
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from app.constants import INTEGRATIONS_STORE_PATH, LEGACY_INTEGRATIONS_STORE_PATH

logger = logging.getLogger(__name__)

STORE_PATH = INTEGRATIONS_STORE_PATH
LEGACY_STORE_PATH = LEGACY_INTEGRATIONS_STORE_PATH
_VERSION = 2
_LOCK_TIMEOUT_SECONDS = 10.0

_STRUCTURAL_RECORD_FIELDS = frozenset({"id", "service", "status", "instances"})


class IntegrationStoreLockTimeout(TimeoutError):
    """Raised when the integration store lock cannot be acquired in time."""


def _lock_timeout_error() -> IntegrationStoreLockTimeout:
    return IntegrationStoreLockTimeout(
        f"Integration store locked: {_lock_path()} (store: {STORE_PATH})"
    )


def _migrate_record_v1_to_v2(record: dict[str, Any]) -> dict[str, Any]:
    if isinstance(record.get("instances"), list):
        return record

    credentials = dict(record.get("credentials", {}))
    for key, value in record.items():
        if key in _STRUCTURAL_RECORD_FIELDS or key == "credentials":
            continue
        credentials.setdefault(key, value)

    return {
        "id": record.get("id", ""),
        "service": record.get("service", ""),
        "status": record.get("status", "active"),
        "instances": [{"name": "default", "tags": {}, "credentials": credentials}],
    }


def _migrate_if_needed(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if data.get("version") == _VERSION:
        return data, False

    records = data.get("integrations", [])
    if not isinstance(records, list):
        records = []

    migrated_records = [
        _migrate_record_v1_to_v2(r) if isinstance(r, dict) else r
        for r in records
    ]

    return {"version": _VERSION, "integrations": migrated_records}, True


def _read_json_store_at(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to read integrations store at %s", path)
        return None
    except OSError:
        logger.warning(
            "Failed to read integrations store at %s",
            path,
            exc_info=True,
        )
        return None

    if not isinstance(data, dict) or "integrations" not in data:
        return None

    return data


def _load_raw_unlocked() -> tuple[dict[str, Any], bool]:
    if not STORE_PATH.exists():
        return {"version": _VERSION, "integrations": []}, False

    try:
        text = STORE_PATH.read_text(encoding="utf-8")
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to read integrations store at %s", STORE_PATH)
        return {"version": _VERSION, "integrations": []}, False
    except OSError:
        logger.warning(
            "Failed to read integrations store at %s",
            STORE_PATH,
            exc_info=True,
        )
        return {"version": _VERSION, "integrations": []}, False

    if not isinstance(data, dict) or "integrations" not in data:
        return {"version": _VERSION, "integrations": []}, False

    return _migrate_if_needed(data)


# ---------------- rest of your file remains unchanged ----------------
# (kept as-is for brevity; no other conflict areas exist)