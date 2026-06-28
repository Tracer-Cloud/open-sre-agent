"""Synthetic-test machinery for the interactive REPL.

Groups the synthetic-test execution concern next to the agent-facing
``tools.interactive_shell.actions.synthetic``:

* ``runner`` spawns the synthetic-test subprocess, watches its lifecycle in a
  daemon thread, applies the execution policy, and records the turn outcome.

Import submodules explicitly (for example ``tools.interactive_shell.synthetic.runner``)
rather than relying on this package initializer, to keep interactive-shell
startup import-light.
"""

from __future__ import annotations
