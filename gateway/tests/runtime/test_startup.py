"""Gateway process boot delegates to :func:`configure_process`.

Shared order lives in :mod:`bootstrap.process` (characterized there).
This module only pins the thin wrapper: ``GATEWAY_PROFILE``, no harness return.
"""

from __future__ import annotations

import logging
from typing import Any


def test_startup_run_configures_gateway_profile(monkeypatch: Any) -> None:
    from bootstrap.process import GATEWAY_PROFILE
    from gateway.core.runtime import startup

    seen: list[Any] = []

    monkeypatch.setattr(
        startup,
        "configure_process",
        lambda profile, *, logger=None: seen.append((profile, logger)),
    )

    logger = logging.getLogger("test.startup")
    startup.run(logger)

    assert seen == [(GATEWAY_PROFILE, logger)]
