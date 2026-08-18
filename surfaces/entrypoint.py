"""The ``opensre`` process entrypoint: composes the CLI, the interactive shell and the gateway entry.

The surfaces are peers and do not import each other; this module is the one
place that knows all of them. A bare ``opensre`` on a terminal opens the shell,
``opensre <command>`` runs the CLI, and ``opensre gateway start --foreground``
runs the gateway attached — each through a callable handed to the CLI as its
:class:`~surfaces.cli.host.CliHost`.
"""

from __future__ import annotations

from config.repl_config import ReplConfig


def _launch_shell(config: ReplConfig, resume_session_id: str | None) -> int:
    from surfaces.cli.app import cli
    from surfaces.interactive_shell import run_repl

    return run_repl(config=config, resume_session_id=resume_session_id, cli_command_group=cli)


def _start_gateway_foreground() -> None:
    from surfaces.gateway_entry import start_gateway

    start_gateway()


def main(argv: list[str] | None = None) -> int:
    """Run ``opensre`` with the shell and the gateway entry wired in; return the exit status."""
    from surfaces.cli.app import main as cli_main
    from surfaces.cli.host import CliHost

    return cli_main(
        argv,
        host=CliHost(
            launch_shell=_launch_shell,
            start_gateway_foreground=_start_gateway_foreground,
        ),
    )


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
