"""Run the OpenSRE quickstart wizard."""

from __future__ import annotations

import click
from dotenv import load_dotenv

from app.analytics.cli import build_cli_invoked_properties, capture_cli_invoked
from app.analytics.provider import capture_first_run_if_needed, shutdown_analytics
from app.cli.support.prompt_support import install_questionary_escape_cancel
from app.cli.wizard.flow import run_wizard
from app.utils.sentry_sdk import init_sentry

_ENTRYPOINT = "python -m app.cli.wizard"


def main() -> int:
    load_dotenv(override=False)
    init_sentry()
    install_questionary_escape_cancel()

    capture_first_run_if_needed()
    capture_cli_invoked(
        build_cli_invoked_properties(
            entrypoint=_ENTRYPOINT,
            command_parts=["wizard"],
        )
    )

    try:
        return int(run_wizard())
    except KeyboardInterrupt:
        print(flush=True)
        return 0
    except click.Abort:
        print(flush=True)
        return 0
    finally:
        shutdown_analytics(flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
