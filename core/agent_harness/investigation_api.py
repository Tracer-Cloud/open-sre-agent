"""Public investigation result + payload runner type.

``agent_harness`` must not import ``tools`` (import-boundary tests), so callers
supply the payload callable when they invoke :meth:`AgentSession.investigate`
rather than the harness reaching for it. The canonical callable is
:func:`tools.investigation.capability.run_investigation_payload`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

AlertInput = str | dict[str, Any]
InvestigationPayloadRunner = Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class InvestigationResult:
    """Typed public result of :meth:`AgentSession.investigate`.

    Wire formats (HTTP, CLI JSON) use :meth:`as_dict` so the serializable shape
    stays identical to :func:`tools.investigation.capability.build_investigation_payload`.
    """

    report: Any
    problem_md: Any
    root_cause: Any
    is_noise: bool = False
    validity_score: float = 0.0
    tool_calls: Any = None
    opensre_llm_eval: Any = None
    _extra: Mapping[str, Any] | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> InvestigationResult:
        """Build from the serializable investigation payload dict."""
        known = {
            "report",
            "problem_md",
            "root_cause",
            "is_noise",
            "validity_score",
            "tool_calls",
            "opensre_llm_eval",
        }
        extra = {k: v for k, v in payload.items() if k not in known}
        return cls(
            report=payload.get("report"),
            problem_md=payload.get("problem_md"),
            root_cause=payload.get("root_cause"),
            is_noise=bool(payload.get("is_noise", False)),
            validity_score=float(payload.get("validity_score") or 0.0),
            tool_calls=payload.get("tool_calls"),
            opensre_llm_eval=payload.get("opensre_llm_eval"),
            _extra=extra or None,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the wire-format payload dict."""
        out: dict[str, Any] = {
            "report": self.report,
            "problem_md": self.problem_md,
            "root_cause": self.root_cause,
            "is_noise": self.is_noise,
            "validity_score": self.validity_score,
        }
        if self.tool_calls is not None:
            out["tool_calls"] = self.tool_calls
        if self.opensre_llm_eval is not None:
            out["opensre_llm_eval"] = self.opensre_llm_eval
        if self._extra:
            out.update(self._extra)
        return out


__all__ = [
    "AlertInput",
    "InvestigationPayloadRunner",
    "InvestigationResult",
]
