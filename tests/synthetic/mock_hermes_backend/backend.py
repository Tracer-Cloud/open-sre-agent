"""FixtureHermesBackend for synthetic Hermes incident-identification scenarios."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from tests.synthetic.hermes_rca.scenario_loader import HermesScenarioFixture


@runtime_checkable
class HermesBackend(Protocol):
    def get_session_log(self, session_id: str = "", **kwargs: Any) -> dict[str, Any]:
        pass

    def get_message_history(self, session_id: str = "", **kwargs: Any) -> dict[str, Any]:
        pass

    def get_kv_cache_state(self, session_id: str = "", **kwargs: Any) -> dict[str, Any]:
        pass

    def get_runtime_state(self, session_id: str = "", **kwargs: Any) -> dict[str, Any]:
        pass

    def get_cron_state(self, session_id: str = "", **kwargs: Any) -> dict[str, Any]:
        pass

    def get_session_topology(self, session_id: str = "", **kwargs: Any) -> dict[str, Any]:
        pass


class FixtureHermesBackend:
    """Backend that serves evidence from ``HermesScenarioFixture`` in tool envelopes."""

    def __init__(self, fixture: HermesScenarioFixture, *, hang_threshold_s: int = 120) -> None:
        self._fixture = fixture
        self._hang_threshold_s = hang_threshold_s

    def get_session_log(self, session_id: str = "", **_: Any) -> dict[str, Any]:
        evidence = self._fixture.evidence.hermes_session_log
        if evidence is None:
            return self._missing("session_log")
        return {
            "source": "hermes",
            "available": True,
            "session_id": session_id or evidence.get("session_id", ""),
            "events": list(evidence.get("events", [])),
            "error": None,
        }

    def get_message_history(self, session_id: str = "", **_: Any) -> dict[str, Any]:
        evidence = self._fixture.evidence.hermes_message_history
        if evidence is None:
            return self._missing("message_history")
        result: dict[str, Any] = {
            "source": "hermes",
            "available": True,
            "session_id": session_id or evidence.get("session_id", ""),
            "messages": list(evidence.get("messages", [])),
            "error": None,
        }
        snapshots = evidence.get("snapshots")
        if isinstance(snapshots, dict):
            result["snapshots"] = {
                "pre_compression": list(snapshots.get("pre_compression", [])),
                "post_compression": list(snapshots.get("post_compression", [])),
            }
        return result

    def get_kv_cache_state(self, session_id: str = "", **_: Any) -> dict[str, Any]:
        evidence = self._fixture.evidence.hermes_kv_cache_state
        if evidence is None:
            return self._missing("kv_cache_state")
        return {
            "source": "hermes",
            "available": True,
            "session_id": session_id or evidence.get("session_id", ""),
            "cache_hits": int(evidence.get("cache_hits", 0)),
            "cache_misses": int(evidence.get("cache_misses", 0)),
            "last_cached_prefix_bytes": int(evidence.get("last_cached_prefix_bytes", 0)),
            "last_invalidated_reason": str(evidence.get("last_invalidated_reason", "")),
            "messages_with_cache_miss": list(evidence.get("messages_with_cache_miss", [])),
            "error": None,
        }

    def get_runtime_state(self, session_id: str = "", **_: Any) -> dict[str, Any]:
        evidence = self._fixture.evidence.hermes_runtime_state
        if evidence is None:
            return self._missing("runtime_state")

        frozen_now_ts = str(evidence.get("frozen_now_ts", ""))
        last_progress_ts = str(evidence.get("last_progress_ts", ""))

        computed_blocked = bool(evidence.get("is_blocked", False))
        if frozen_now_ts and last_progress_ts:
            try:
                frozen_dt = datetime.fromisoformat(frozen_now_ts.replace("Z", "+00:00")).astimezone(
                    UTC
                )
                progress_dt = datetime.fromisoformat(
                    last_progress_ts.replace("Z", "+00:00")
                ).astimezone(UTC)
                computed_blocked = (
                    frozen_dt - progress_dt
                ).total_seconds() > self._hang_threshold_s
            except ValueError:
                computed_blocked = bool(evidence.get("is_blocked", False))

        return {
            "source": "hermes",
            "available": True,
            "session_id": session_id,
            "pid": int(evidence.get("pid", 0)),
            "started_at": str(evidence.get("started_at", "")),
            "frozen_now_ts": frozen_now_ts,
            "interrupt_queue_depth": int(evidence.get("interrupt_queue_depth", 0)),
            "last_progress_ts": last_progress_ts,
            "is_blocked": computed_blocked,
            "blocking_call": evidence.get("blocking_call"),
            "error": None,
        }

    def get_cron_state(self, session_id: str = "", **_: Any) -> dict[str, Any]:
        evidence = self._fixture.evidence.hermes_cron_state
        if evidence is None:
            return self._missing("cron_state")
        return {
            "source": "hermes",
            "available": True,
            "session_id": session_id,
            "schedule_cron": str(evidence.get("schedule_cron", "")),
            "last_run": dict(evidence.get("last_run", {})),
            "error": None,
        }

    def get_session_topology(self, session_id: str = "", **_: Any) -> dict[str, Any]:
        evidence = self._fixture.evidence.hermes_session_topology
        if evidence is None:
            return self._missing("session_topology")
        return {
            "source": "hermes",
            "available": True,
            "session_id": session_id or str(evidence.get("visible_session_id", "")),
            "visible_session_id": str(evidence.get("visible_session_id", "")),
            "all_sessions": list(evidence.get("all_sessions", [])),
            "error": None,
        }

    def _missing(self, evidence_key: str) -> dict[str, Any]:
        return {
            "source": "hermes",
            "available": False,
            "error": (
                f"{self._fixture.scenario_id}: {evidence_key} requested "
                "but not present in available_evidence"
            ),
        }
