"""Repo-relative paths for OpenSRE's synthetic and RCA test corpora.

These are plain :class:`pathlib.Path` constants derived from the repo root, so
they live in :mod:`config` (the layered root package that everyone may depend
on). Hosting them here lets both surface code (the CLI ``tests`` command) and
core session state (post-synthetic-failure follow-up hinting) reference the
same directories without ``core -> surfaces`` imports (see T-4 layering audit,
issue #3352; harness decoupling T-06, issue #3539).

The constants intentionally do not touch the file system at import time — they
are lazy path expressions consumed by callers that check ``is_dir()`` /
``is_file()`` on demand.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
"""Absolute path to the OpenSRE repository root.

Resolved once at import time from the location of this module (``config/``
sits directly under the repo root). Consumers should treat this as read-only.
"""

SYNTHETIC_SCENARIOS_DIR = REPO_ROOT / "tests" / "synthetic" / "rds_postgres"
"""Directory holding the RDS/Postgres synthetic RCA scenarios.

Populated by the synthetic test suite runner and inspected by post-turn
follow-up hinting in :mod:`core.agent_harness.session.state`.
"""

OPENCLAW_SYNTHETIC_SCENARIOS_DIR = REPO_ROOT / "tests" / "synthetic" / "openclaw" / "scenarios"
"""Directory holding the OpenClaw synthetic RCA scenarios."""

CLOUDOPSBENCH_DIR = REPO_ROOT / "tests" / "benchmarks" / "cloudopsbench"
"""Directory holding the CloudOpsBench benchmark corpus."""

RCA_DIR = REPO_ROOT / "tests" / "e2e" / "rca"
"""Directory holding end-to-end RCA scenario fixtures."""


__all__ = [
    "CLOUDOPSBENCH_DIR",
    "OPENCLAW_SYNTHETIC_SCENARIOS_DIR",
    "RCA_DIR",
    "REPO_ROOT",
    "SYNTHETIC_SCENARIOS_DIR",
]
