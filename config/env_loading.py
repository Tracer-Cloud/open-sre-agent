"""Environment file loading for CLI and runtime startup."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from config.constants import OPENSRE_HOME_DIR

logger = logging.getLogger(__name__)


def load_opensre_env_files() -> None:
    """Load user-global then project-level ``.env`` files.

    Precedence (low → high):
    1. ``~/.opensre/.env`` — durable config for installed-binary users
    2. cwd ``.env`` or ``OPENSRE_PROJECT_ENV_PATH`` — project overlay
    3. Shell exports present before startup — never overwritten by files
    """
    shell_exports = dict(os.environ)

    home_env = OPENSRE_HOME_DIR / ".env"
    if home_env.is_file():
        load_dotenv(home_env, override=True)

    project_env = os.getenv("OPENSRE_PROJECT_ENV_PATH", "").strip()
    if project_env:
        project_path = Path(project_env)
        if project_path.is_file():
            load_dotenv(project_path, override=True)
        else:
            logger.warning(
                "OPENSRE_PROJECT_ENV_PATH is set but not a file: %s",
                project_env,
            )
            load_dotenv(override=True)
        _restore_shell_exports(shell_exports)
        return

    load_dotenv(override=True)
    _restore_shell_exports(shell_exports)


def _restore_shell_exports(shell_exports: dict[str, str]) -> None:
    """Re-apply variables that were exported in the shell before file loads."""
    for key, value in shell_exports.items():
        os.environ[key] = value
