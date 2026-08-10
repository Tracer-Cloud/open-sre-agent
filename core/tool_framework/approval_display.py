"""How one tool describes itself to the human being asked to approve it.

A generic argument renderer can only report *what fields were passed*. Deciding
whether to approve a write is a judgement about blast radius, and phrasing that
— "open a pull request against ``main`` touching two Terraform files" — needs
knowledge of the action. So the tool that owns the write supplies the wording,
and the approval surface renders it.

This is a leaf: it imports nothing first-party, so both ``core`` (the registry)
and ``integrations`` (the tools that implement it) can depend on it. Tools that
declare no display fall back to the surface's generic argument summary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class ApprovalDisplay(Protocol):
    """Renders one tool's approval prompt, and its outcome, in the reviewer's terms."""

    def headline(self, arguments: Mapping[str, Any]) -> str:
        """One line naming the action, e.g. ``Create PR — fix(pubsub): drop …``."""

    def details(self, arguments: Mapping[str, Any]) -> str:
        """Multi-line body describing exactly what the call will change."""

    def receipt(self, arguments: Mapping[str, Any], result: Mapping[str, Any]) -> str:
        """Replacement headline once the call has run, e.g. ``Create PR #123 … <url>``.

        Return an empty string to keep the original headline — the identifiers
        worth reporting (a pull request number, a URL) only exist on success.
        """


__all__ = ["ApprovalDisplay"]
