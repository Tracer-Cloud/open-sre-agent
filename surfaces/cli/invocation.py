"""Argv classification, fast-path CLI answers, and stdio setup.

Pure helpers used by ``surfaces.cli.app`` before the full CLI is
bootstrapped. They take the Click command / argv explicitly so they carry no
dependency on the root group and stay trivially testable.

Fast paths (``--version``, ``investigate --print-template``) must stay cheap:
they answer before :func:`surfaces.cli.startup.run` installs adapters.
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


def _option_equals_value(token: str, option: str) -> str | None:
    prefix = f"{option}="
    if token.startswith(prefix):
        return token[len(prefix) :]
    return None


def parse_investigate_print_template_argv(
    argv: list[str],
) -> tuple[str, str | None] | None:
    """Parse ``investigate --print-template <name>`` with optional ``-o``/``--output``.

    Returns ``(template_name, output_path)`` only when argv is exactly that
    shape — any other investigate flag or positional falls through to the
    full CLI (so Click keeps ownership of usage errors).
    """
    if not argv or argv[0] != "investigate":
        return None

    template: str | None = None
    output: str | None = None
    i = 1
    while i < len(argv):
        token = argv[i]
        equals = _option_equals_value(token, "--print-template")
        if equals is not None:
            template = equals
            i += 1
            continue
        if token == "--print-template":
            i += 1
            if i >= len(argv) or argv[i].startswith("-"):
                return None
            template = argv[i]
            i += 1
            continue

        equals = _option_equals_value(token, "--output")
        if equals is not None:
            output = equals
            i += 1
            continue
        if token in {"--output", "-o"}:
            i += 1
            if i >= len(argv) or argv[i].startswith("-"):
                return None
            output = argv[i]
            i += 1
            continue

        # Any other option or positional needs the full Click path.
        return None

    if template is None or not template.strip():
        return None
    return template.strip(), output


def try_fast_investigate_print_template(argv: list[str]) -> int | None:
    """Print an alert template without process boot; ``None`` means not this path.

    ``--print-template`` is a pure JSON dump — installing harness adapters and
    loading every integration verifier just to print a fixture is what pushed
    the smoke subprocess past its 15s budget under a loaded ``test-cov`` run.
    """
    parsed = parse_investigate_print_template_argv(argv)
    if parsed is None:
        return None

    template_name, output_path = parsed
    from config.constants.investigation import ALERT_TEMPLATE_CHOICES
    from surfaces.cli.args import write_json
    from tools.investigation.alert_templates import build_alert_template

    if template_name not in ALERT_TEMPLATE_CHOICES:
        supported = ", ".join(ALERT_TEMPLATE_CHOICES)
        click.echo(
            f"Error: Invalid value for '--print-template': '{template_name}' "
            f"is not one of {supported}.",
            err=True,
        )
        return 2

    write_json(build_alert_template(template_name), output_path)
    return 0


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
