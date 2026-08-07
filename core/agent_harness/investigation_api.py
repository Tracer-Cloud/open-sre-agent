"""Public investigation result + installable payload runner port.

``agent_harness`` must not import ``tools`` (import-boundary tests). Process
boot installs the canonical runner via :func:`install_investigation_payload_runner`
from :mod:`bootstrap.adapters`. Surfaces and embedders call
:meth:`AgentSession.investigate`, which uses the installed runner.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

AlertInput = str | dict[str, Any]
InvestigationPayloadRunner = Callable[..., dict[str, Any]]

_payload_runner: InvestigationPayloadRunner | None = None


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


def install_investigation_payload_runner(runner: InvestigationPayloadRunner) -> None:
    """Bind the payload investigation callable used by :meth:`AgentSession.investigate`."""
    global _payload_runner
    _payload_runner = runner


def reset_investigation_payload_runner_for_tests() -> None:
    """Clear the installed runner (tests only)."""
    global _payload_runner
    _payload_runner = None


def get_investigation_payload_runner() -> InvestigationPayloadRunner | None:
    """Return the installed runner, or ``None`` if process boot has not wired it."""
    return _payload_runner


def run_installed_investigation_payload(
    *,
    raw_alert: AlertInput,
    opensre_evaluate: bool = False,
    investigation_metadata: tuple[str, str] | None = None,
) -> InvestigationResult:
    """Run investigation through the installed payload runner."""
    runner = _payload_runner
    if runner is None:
        raise RuntimeError(
            "Investigation payload runner is not installed. Call "
            "configure_process(...) (harness adapters) or "
            "bootstrap.adapters.install_investigation_api() before "
            "AgentSession.investigate."
        )
    payload = runner(
        raw_alert=raw_alert,
        opensre_evaluate=opensre_evaluate,
        investigation_metadata=investigation_metadata,
    )
    return InvestigationResult.from_payload(payload)


__all__ = [
    "AlertInput",
    "InvestigationPayloadRunner",
    "InvestigationResult",
    "get_investigation_payload_runner",
    "install_investigation_payload_runner",
    "reset_investigation_payload_runner_for_tests",
    "run_installed_investigation_payload",
]
