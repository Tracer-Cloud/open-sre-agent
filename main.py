"""OpenSRE process entrypoint.

Prefer the ``opensre`` console script in normal use. This module exists so
``python main.py`` and ``python -m`` discovery reach the same CLI as
``surfaces.cli.app:main``.

This covers the interactive shell, the landing page, and every one-shot
subcommand. The gateway daemon is a separate process entry —
``python -m gateway.main``, managed via ``opensre gateway start`` — because
``gateway`` and ``surfaces`` are peer packages that must not import each other.

Shared process setup — environment, error reporting, adapter registration —
lives in ``bootstrap.process``. Each host picks a profile rather than repeating
the order: this CLI uses ``CLI_PROFILE`` (via ``surfaces.cli.startup``), the
gateway ``GATEWAY_PROFILE``, the web app ``WEB_PROFILE``.

Driving the agent from Python instead of the CLI::

    from bootstrap.process import EMBEDDED_PROFILE, configure_process
    from core.agent_harness import AgentSession

    configure_process(EMBEDDED_PROFILE)

    session = AgentSession.start()
    result = session.chat("why is checkout-api slow?")
    if result.answered:
        print(result.primary_response_text)

    report = session.investigate({"alert_name": "HighLatency"})
    print(report.report)

``configure_process`` comes first and is what registers the harness adapters
tools resolve through (and the investigation payload runner);
``AgentSession.start()`` only loads the environment, so on its own the agent
starts but no tool is available. It cannot self-bootstrap — ``core`` sits below
``bootstrap`` in the layer contract, so the caller wires it.
``EMBEDDED_PROFILE`` deliberately leaves Sentry and LLM preloading to the host
process.

``start()`` opens a session and attaches an agent with the standard ports. Check
``result.answered`` before trusting the text — on a failed turn (e.g. the LLM
provider is unreachable) the error message itself lands in
``primary_response_text``. Surfaces that need their own ports — a live gateway
sink, a REPL console — build the agent with
``core.agent_harness.build_default_headless_agent`` and call ``attach_agent``
instead. ``AgentHarness`` / ``dispatch_message`` remain compatibility aliases.
"""

from __future__ import annotations


def main() -> int:
    """Run the CLI and return its exit status."""
    from surfaces.cli.app import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
