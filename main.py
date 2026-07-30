"""Narrative composition root for OpenSRE.

Start reading here. This module does not own business logic — it points at the
real entrypoints so a new contributor can see how the process starts:

- CLI / interactive shell: ``surfaces.cli.__main__:main`` (``opensre`` console script)
- Messaging gateway: ``surfaces.cli.gateway_entry:main``
- Shared agent startup + headless turns: ``core.agent_harness.harness.AgentHarness``
- Process adapter/scheduler install: ``tools.runtime_bootstrap.install_runtime``

Example headless narrative::

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
    """Delegate to the CLI entrypoint (same as the ``opensre`` console script).

    Returns the CLI's exit status so ``python main.py`` and ``opensre`` agree —
    a swallowed status silently passes failures to CI steps and shell chains.
    """
    from surfaces.cli.__main__ import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
