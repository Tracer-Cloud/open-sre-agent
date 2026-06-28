"""Environment file loading for CLI and runtime startup."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from config.constants import OPENSRE_HOME_DIR


def load_opensre_env_files() -> None:
    """Load user-global then project-level ``.env`` files.

    Precedence (low → high):
    1. ``~/.opensre/.env`` — durable config for installed-binary users
    2. cwd ``.env`` or ``OPENSRE_PROJECT_ENV_PATH`` — project overlay wins
    """
    home_env = OPENSRE_HOME_DIR / ".env"
    if home_env.is_file():
        load_dotenv(home_env, override=False)

    project_env = os.getenv("OPENSRE_PROJECT_ENV_PATH", "").strip()
    if project_env and Path(project_env).is_file():
        load_dotenv(project_env, override=True)
        return

    load_dotenv(override=True)
