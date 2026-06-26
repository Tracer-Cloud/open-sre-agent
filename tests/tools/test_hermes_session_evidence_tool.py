from __future__ import annotations

from typing import Any

from tools.hermes_session_evidence_tool import (
    get_hermes_cron_state,
    get_hermes_kv_cache_state,
    get_hermes_message_history,
    get_hermes_runtime_state,
    get_hermes_session_log,
    get_hermes_session_topology,
)


class _FakeHermesBackend:
    def get_session_log(self, session_id: str = "", **_: Any) -> dict[str, Any]:
        return {"source": "hermes", "available": True, "session_id": session_id, "events": []}

    def get_message_history(self, session_id: str = "", **_: Any) -> dict[str, Any]:
        return {"source": "hermes", "available": True, "session_id": session_id, "messages": []}

    def get_kv_cache_state(self, session_id: str = "", **_: Any) -> dict[str, Any]:
        return {
            "source": "hermes",
            "available": True,
            "session_id": session_id,
            "cache_hits": 1,
            "cache_misses": 0,
            "messages_with_cache_miss": [],
        }

    def get_runtime_state(self, session_id: str = "", **_: Any) -> dict[str, Any]:
        return {
            "source": "hermes",
            "available": True,
            "session_id": session_id,
            "is_blocked": False,
        }

    def get_cron_state(self, session_id: str = "", **_: Any) -> dict[str, Any]:
        return {
            "source": "hermes",
            "available": True,
            "session_id": session_id,
            "last_run": {"delivery_status": "ok"},
        }

    def get_session_topology(self, session_id: str = "", **_: Any) -> dict[str, Any]:
        return {
            "source": "hermes",
            "available": True,
            "session_id": session_id,
            "visible_session_id": session_id,
            "all_sessions": [],
        }


def test_tools_delegate_to_backend() -> None:
    backend = _FakeHermesBackend()

    assert get_hermes_session_log("s1", hermes_backend=backend)["available"] is True
    assert get_hermes_message_history("s1", hermes_backend=backend)["available"] is True
    assert get_hermes_kv_cache_state("s1", hermes_backend=backend)["cache_hits"] == 1
    assert get_hermes_runtime_state("s1", hermes_backend=backend)["is_blocked"] is False
    assert (
        get_hermes_cron_state("s1", hermes_backend=backend)["last_run"]["delivery_status"] == "ok"
    )
    assert get_hermes_session_topology("s1", hermes_backend=backend)["visible_session_id"] == "s1"


def test_tools_require_backend_when_not_configured() -> None:
    result = get_hermes_session_log(session_id="")
    assert result["available"] is False
    assert "requires a Hermes backend" in str(result["error"])
