"""Synthetic scenario catalog helpers for interactive-shell orchestration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

DEFAULT_SYNTHETIC_SCENARIO = "001-replication-lag"

# Sentinel content emitted when the user pointed at a specific (non-existent)
# scenario. The planner threads this through to the executor instead of silently
# falling back to ``DEFAULT_SYNTHETIC_SCENARIO``, so the user sees an explicit
# "no such scenario" error rather than the wrong test getting launched.
SYNTHETIC_UNKNOWN_PREFIX = "rds_postgres:unknown:"

# ``parents[6]`` is the repo root. Counting parents from this file's current
# location (routing/handle_message_with_agent/orchestration/):
#   parents[0] orchestration          / parents[1] handle_message_with_agent
#   parents[2] routing                / parents[3] interactive_shell
#   parents[4] cli                    / parents[5] app
#   parents[6] <repo root>
_RDS_POSTGRES_SUITE_DIR = (
    Path(__file__).resolve().parents[6] / "tests" / "synthetic" / "rds_postgres"
)


@lru_cache(maxsize=1)
def list_rds_postgres_scenarios() -> tuple[str, ...]:
    """Enumerate available RDS Postgres synthetic scenario directory names."""
    if not _RDS_POSTGRES_SUITE_DIR.is_dir():
        return ()
    return tuple(
        sorted(
            entry.name
            for entry in _RDS_POSTGRES_SUITE_DIR.iterdir()
            if entry.is_dir()
            and len(entry.name) >= 5
            and entry.name[:3].isdigit()
            and entry.name[3] == "-"
        )
    )


__all__ = [
    "DEFAULT_SYNTHETIC_SCENARIO",
    "SYNTHETIC_UNKNOWN_PREFIX",
    "list_rds_postgres_scenarios",
]
