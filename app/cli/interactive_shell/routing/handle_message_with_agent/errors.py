"""Typed degrade-path errors for non-command routing."""

from __future__ import annotations


class RoutingDegradeError(RuntimeError):
    """Base class for typed routing degrade reasons."""

    reason_tag: str = "routing_degrade.unknown"


class ParseError(RoutingDegradeError):
    """Prompt parsing failed before action mapping."""

    reason_tag = "routing_degrade.parse_error"


class PolicyError(RoutingDegradeError):
    """Policy/rule application failed while mapping actions."""

    reason_tag = "routing_degrade.policy_error"


class PlannerUnavailable(RoutingDegradeError):
    """Planner/mapping backend unavailable or unhealthy."""

    reason_tag = "routing_degrade.planner_unavailable"


__all__ = [
    "ParseError",
    "PlannerUnavailable",
    "PolicyError",
    "RoutingDegradeError",
]
