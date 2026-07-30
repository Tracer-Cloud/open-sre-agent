"""OpenSRE process entrypoint.

Prefer the ``opensre`` console script in normal use. This module exists so
``python main.py`` and ``python -m`` discovery reach the same CLI as
``surfaces.cli.__main__:main``.

Typical headless usage::

    from core.agent_harness.harness import AgentHarness, HarnessConfig
    from core.agent_harness.turns.headless_dispatch import HeadlessAgent, NullToolProvider

    harness = AgentHarness(HarnessConfig())
    startup = harness.startup()
    agent = HeadlessAgent(session=startup.session, tools=NullToolProvider())
    harness.attach_agent(agent)
    result = harness.dispatch_message("summarize open incidents")
"""

from __future__ import annotations


def main() -> int:
    """Run the CLI and return its exit status."""
    from surfaces.cli.__main__ import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
