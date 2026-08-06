"""Composition root: shared process and harness setup for every OpenSRE host.

Sits between the entrypoint layer (``surfaces``, ``gateway``) and the
component layers (``tools``, ``integrations``). Wiring is the one job that needs
both component layers at once, so this is the only package allowed to import
them together — which is why ``surfaces`` and ``gateway``, forbidden from
importing each other, previously kept peer copies of the same registration
helper and synchronised them by comment.
"""

from __future__ import annotations

__all__: list[str] = []
