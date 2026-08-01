"""OpenSRE process entrypoint.

Prefer the ``opensre`` console script in normal use. This module exists so
``python main.py`` and ``python -m`` discovery reach the same CLI as
``surfaces.cli.app:main``.

This covers the interactive shell, the landing page, and every one-shot
subcommand. The gateway daemon is a separate process entry —
``python -m gateway.main``, managed via ``opensre gateway start`` — because
``gateway`` and ``surfaces`` are peer packages that must not import each other.

Driving the agent from Python instead of the CLI::

    from core.agent_harness import AgentHarness

    harness = AgentHarness.start()
    result = harness.dispatch_message("why is checkout-api slow?")
    if result.answered:
        print(result.primary_response_text)

``start()`` resolves the environment, opens a session, and attaches an agent
with the standard ports. Check ``result.answered`` before trusting the text —
on a failed turn (e.g. the LLM provider is unreachable) the error message
itself lands in ``primary_response_text``. Surfaces that need their own ports —
a live gateway sink, a REPL console — build the agent with
``core.agent_harness.build_default_headless_agent`` and call ``attach_agent``
instead.
"""

from __future__ import annotations


def main() -> int:
    """Run the CLI and return its exit status."""
    from surfaces.cli.app import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
