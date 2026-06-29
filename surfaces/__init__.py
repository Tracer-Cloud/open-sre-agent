"""User-facing surfaces — one folder per UI/client.

Each surface is a distinct way a human (or another system) interacts
with OpenSRE:

* :mod:`surfaces.cli` — stateless command runner (``opensre <command>``)
* :mod:`surfaces.interactive_shell` — stateful REPL surface (``opensre``)
* :mod:`surfaces.slack_app` — Slack bot surface (Phase 2 of V0.2)
* :mod:`surfaces.shared` — code two or more surfaces import

Layering rule: surfaces may import from ``core/``, ``tools/``,
``integrations/``, ``platform/``. Nothing first-party may import from
``surfaces/`` (it sits at the top of the dependency stack).

See ``docs/ARCHITECTURE.md`` for the full layering contract and
``opensre-notes/v0.2-ai-production-engineer/t1-design-doc.md`` for the
restructure rationale.
"""

from __future__ import annotations
