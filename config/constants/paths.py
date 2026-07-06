from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = REPO_ROOT

SYNTHETIC_SCENARIOS_DIR = REPO_ROOT / "tests" / "synthetic" / "rds_postgres"

OPENSRE_HOME_DIR = Path.home() / ".opensre"
INTEGRATIONS_STORE_PATH = OPENSRE_HOME_DIR / "integrations.json"
OPENSRE_TMP_DIR = Path(tempfile.gettempdir()) / "opensre"


def get_store_path() -> Path:
    override = os.getenv("OPENSRE_WIZARD_STORE_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return OPENSRE_HOME_DIR / "opensre.json"


def ensure_opensre_tmp_dir() -> Path:
    OPENSRE_TMP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        OPENSRE_TMP_DIR.chmod(0o700)
    return OPENSRE_TMP_DIR


__all__ = [
    "INTEGRATIONS_STORE_PATH",
    "OPENSRE_HOME_DIR",
    "OPENSRE_TMP_DIR",
    "PROJECT_ROOT",
    "REPO_ROOT",
    "SYNTHETIC_SCENARIOS_DIR",
    "ensure_opensre_tmp_dir",
    "get_store_path",
]
