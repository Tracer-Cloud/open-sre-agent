"""CLI context helpers for accessing global flags from any call depth.

Uses ``click.get_current_context()`` so commands don't need
``@click.pass_context`` to read global flags set on the root group.
"""

from __future__ import annotations

import os

import click


def _root_obj() -> dict[str, object]:
    ctx = click.get_current_context(silent=True)
    if ctx is None:
        return {}
    while ctx.parent is not None:
        ctx = ctx.parent
    return ctx.obj or {}


def is_interactive_env() -> bool:
    """True unless OPENSRE_INTERACTIVE=0 in env or the context has interactive=False."""
    if os.environ.get("OPENSRE_INTERACTIVE") == "0":
        return False
    return bool(_root_obj().get("interactive", True))


def is_json_output() -> bool:
    """True when the user passed ``--json`` / ``-j``."""
    return bool(_root_obj().get("json"))


def is_verbose() -> bool:
    """True when the user passed ``--verbose``."""
    return bool(_root_obj().get("verbose"))


def is_debug() -> bool:
    """True when the user passed ``--debug``."""
    return bool(_root_obj().get("debug"))


def is_yes() -> bool:
    """True when the user passed ``--yes`` / ``-y``."""
    return bool(_root_obj().get("yes"))
