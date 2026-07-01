"""Slack bot surface — Phase 2 of V0.2.

This package is empty scaffolding until the Slack bot code migrates
from the tracer-web-app repo. See
``opensre-notes/v0.2-ai-production-engineer/`` for the V0.2 roadmap.

Naming note: this package is ``slack_app/``, not ``slack/``, to
disambiguate from :mod:`integrations.slack` (the existing webhook
configuration + verifier + outbound delivery). ``surfaces.slack_app``
is the inbound bot surface; ``integrations.slack`` is the outbound
integration. The surface may import the integration; the reverse is
forbidden by the layering contract.
"""

from __future__ import annotations
