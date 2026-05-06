"""python -m app.integrations <command> [service] [options]

Commands: setup, list, show, remove, verify
Services: see the supported-service lists in the help output below

Verify options: --send-slack-test
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

from app.analytics.cli import build_cli_invoked_properties, capture_cli_invoked
from app.analytics.provider import capture_first_run_if_needed, shutdown_analytics
from app.cli.support.prompt_support import install_questionary_escape_cancel
from app.integrations.cli import (
    SUPPORTED,
    cmd_list,
    cmd_remove,
    cmd_setup,
    cmd_show,
    cmd_verify,
)
from app.integrations.verify import SUPPORTED_VERIFY_SERVICES
from app.utils.sentry_sdk import init_sentry

_ENTRYPOINT = "python -m app.integrations"
_KNOWN_COMMANDS = ("setup", "list", "show", "remove", "verify")


def _print_help() -> None:
    print(__doc__)
    print(f"  Supported services: {SUPPORTED}\n")
    print(f"  Verify services: {', '.join(SUPPORTED_VERIFY_SERVICES)}\n")


def _capture_invocation(command_parts: list[str]) -> None:
    capture_first_run_if_needed()
    capture_cli_invoked(
        build_cli_invoked_properties(
            entrypoint=_ENTRYPOINT,
            command_parts=command_parts,
        )
    )


def main() -> None:
    load_dotenv(override=False)
    init_sentry()
    install_questionary_escape_cancel()
    args = sys.argv[1:]

    try:
        if not args or args[0] in ("-h", "--help"):
            _print_help()
            return

        cmd = args[0]
        option_args = {arg for arg in args[1:] if arg.startswith("--")}
        positional_args = [arg for arg in args[1:] if not arg.startswith("--")]
        svc = positional_args[0].lower() if positional_args else None

        if cmd not in _KNOWN_COMMANDS:
            print(
                f"  Unknown command '{cmd}'. Try: setup, list, show, remove, verify",
                file=sys.stderr,
            )
            sys.exit(1)

        _capture_invocation([cmd, svc] if svc else [cmd])

        if cmd == "list":
            cmd_list()
            return
        if cmd == "show":
            cmd_show(svc)
            return
        if cmd == "remove":
            cmd_remove(svc)
            return
        if cmd == "setup":
            resolved_service = cmd_setup(svc)
            if resolved_service in SUPPORTED_VERIFY_SERVICES:
                print(f"  Verifying {resolved_service}...\n")
                sys.exit(cmd_verify(resolved_service))
            return
        if cmd == "verify":
            sys.exit(
                cmd_verify(
                    svc,
                    send_slack_test="--send-slack-test" in option_args,
                )
            )
    finally:
        shutdown_analytics(flush=True)


if __name__ == "__main__":
    main()
