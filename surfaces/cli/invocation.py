"""Argv classification, fast-path ``--version`` output, and stdio setup.

Pure helpers used by ``surfaces.cli.__main__`` before the full CLI is
bootstrapped. They take the Click command / argv explicitly so they carry no
dependency on the root group and stay trivially testable.
"""

from __future__ import annotations

import sys
from contextlib import suppress

import click

from config.version import get_opensre_version


def ensure_utf8_stdio() -> None:
    """Force UTF-8 on stdout/stderr so the themed UI renders on legacy
    Windows consoles (cp1252) without UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with suppress(Exception):
            reconfigure(encoding="utf-8", errors="replace")


def option_value_count(command: click.Command, token: str) -> int:
    for param in command.params:
        if not isinstance(param, click.Option):
            continue
        if token not in (*param.opts, *param.secondary_opts):
            continue
        if param.is_flag or param.count:
            return 0
        return max(param.nargs, 1)
    return 0


def resolve_command_parts(command: click.Command, argv: list[str]) -> list[str]:
    """Resolve nested Click command names without recording option values."""
    parts: list[str] = []
    current = command
    skip_values = 0

    for token in argv:
        if skip_values:
            skip_values -= 1
            continue
        if token == "--":
            break
        if token.startswith("-") and token != "-":
            if "=" not in token:
                skip_values = option_value_count(current, token)
            continue
        if not isinstance(current, click.Group):
            continue

        subcommand = current.get_command(click.Context(current), token)
        if subcommand is None:
            continue

        parts.append(token)
        current = subcommand

    return parts


def is_fast_version_invocation(argv: list[str]) -> bool:
    """Return whether argv can be answered before bootstrapping the full CLI."""
    return (
        argv == ["--version"]
        or argv == ["version"]
        or argv in (["--json", "version"], ["-j", "version"])
    )


def print_fast_version(argv: list[str]) -> None:
    if argv == ["--version"]:
        click.echo(f"opensre, version {get_opensre_version()}")
        return

    import json

    import platform

    json_output = argv[0] in {"--json", "-j"}
    payload = {
        "opensre": get_opensre_version(),
        "python": platform.python_version(),
        "os": platform.system().lower(),
        "arch": platform.machine(),
    }
    if json_output:
        click.echo(json.dumps(payload))
        return
    click.echo(f"opensre {payload['opensre']}")
    click.echo(f"Python  {payload['python']}")
    click.echo(f"OS      {payload['os']} ({payload['arch']})")
