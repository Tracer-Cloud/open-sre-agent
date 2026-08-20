"""Shell-local hook: load OpenSRE's ``platform`` package before ``platform.*`` imports.

Import this module for its side effect at the top of shell entry modules.
Call-time re-checks belong only next to a fresh ``from platform…`` that can run
after an embedding host re-caches stdlib ``platform`` (see
``_alert_listener`` and ``Session.refresh_runtime_metadata``).
"""

from __future__ import annotations

from config.platform_bootstrap import ensure_project_platform_package

ensure_project_platform_package()
