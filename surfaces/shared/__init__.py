"""Code shared across multiple surfaces.

Add modules here only when concrete duplication appears between
``surfaces/cli/``, ``surfaces/interactive_shell/``, and
``surfaces/slack_app/``. The intent is a *safety valve*, not a
default home — if you're tempted to add a module here, double-check
whether it actually belongs in ``core/``, ``tools/``, or
``platform/`` first.

- ``tool_labels`` — tool source/label formatting used by both ``cli`` and
  ``interactive_shell`` while rendering live tool-call activity (T-21).
"""

from __future__ import annotations
