"""Code shared across multiple surfaces.

Add modules here only when concrete duplication appears between
``surfaces/cli/``, ``surfaces/interactive_shell/``, and
``surfaces/slack_app/``. The intent is a *safety valve*, not a
default home — if you're tempted to add a module here, double-check
whether it actually belongs in ``core/``, ``tools/``, or
``platform/`` first.
"""

from __future__ import annotations
