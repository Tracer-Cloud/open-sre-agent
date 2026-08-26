"""Where investigation feedback is written and whether it is asked at all."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

_NEVER_AGAIN_KEY = "feedback_disabled"


def _config_dir() -> Path:
    from config.constants import OPENSRE_HOME_DIR

    return OPENSRE_HOME_DIR


def _feedback_path() -> Path:
    return _config_dir() / "feedback.jsonl"


def _prefs_path() -> Path:
    return _config_dir() / "prefs.json"


def _is_disabled() -> bool:
    with contextlib.suppress(Exception):
        path = _prefs_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return bool(data.get(_NEVER_AGAIN_KEY, False))
    return False


def _set_disabled() -> None:
    with contextlib.suppress(Exception):
        path = _prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if path.exists():
            with contextlib.suppress(Exception):
                data = json.loads(path.read_text(encoding="utf-8"))
        data[_NEVER_AGAIN_KEY] = True
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _store(record: dict[str, Any]) -> None:
    path = _feedback_path()
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
