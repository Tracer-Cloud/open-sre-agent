"""Install analytics entrypoint."""

from __future__ import annotations

from app.analytics.provider import (
    Properties,
    capture_install_detected_if_needed,
    shutdown_analytics,
)
from contextlib import suppress
from app.utils.sentry_sdk import init_sentry

_INSTALL_PROPERTIES: Properties = {
    "install_source": "make_install",
    "entrypoint": "make install",
}


def main() -> int:
    capture_install_detected_if_needed(_INSTALL_PROPERTIES)
    shutdown_analytics(flush=True)
    return 0


if __name__ == "__main__":
    with suppress(ModuleNotFoundError):
        init_sentry(entrypoint="analytics.install")
    raise SystemExit(main())
