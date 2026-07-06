"""Tests for surface-agnostic session investigation streaming."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

import pytest

from core.domain.stream import StreamEvent
from tools.investigation import session_runner


def _collecting_renderer(
    final_state: dict[str, Any] | None = None,
) -> session_runner.StreamRendererFn:
    state = final_state if final_state is not None else {"root_cause": "done"}

    def _render(events: Iterator[StreamEvent]) -> dict[str, Any]:
        for _ in events:
            pass
        return dict(state)

    return _render


def test_run_investigation_for_session_invokes_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_astream(**_kwargs: Any):
        async def _gen():
            yield StreamEvent(event_type="end", data={"output": {"root_cause": "ok"}})

        return _gen()

    monkeypatch.setattr(
        "tools.investigation.session_runner.check_llm_settings",
        lambda: None,
    )
    monkeypatch.setattr(
        "tools.investigation.capability.astream_investigation",
        _fake_astream,
    )

    def _render(events: Iterator[StreamEvent]) -> dict[str, Any]:
        list(events)
        captured["rendered"] = True
        return {"root_cause": "ok"}

    result = session_runner.run_investigation_for_session(
        alert_text="payments failing",
        render_stream=_render,
    )

    assert captured["rendered"] is True
    assert result["root_cause"] == "ok"


def test_run_session_alert_payload_cancel_requested_raises_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel = threading.Event()

    def _slow_astream(**_kwargs: Any):
        async def _gen():
            cancel.set()
            while True:
                yield StreamEvent(event_type="events", data={})
                await __import__("asyncio").sleep(0.05)

        return _gen()

    monkeypatch.setattr(
        "tools.investigation.session_runner.check_llm_settings",
        lambda: None,
    )
    monkeypatch.setattr(
        "tools.investigation.capability.astream_investigation",
        _slow_astream,
    )

    with pytest.raises(KeyboardInterrupt):
        session_runner.run_session_alert_payload(
            raw_alert={"alert_name": "test"},
            cancel_requested=cancel,
            render_stream=_collecting_renderer(),
        )


def test_run_sample_alert_for_session_uses_template(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_alert: dict[str, Any] = {}

    def _fake_astream(*, raw_alert: dict[str, Any], **_kwargs: Any):
        captured_alert.update(raw_alert)

        async def _gen():
            if False:
                yield StreamEvent(event_type="end")

        return _gen()

    monkeypatch.setattr(
        "tools.investigation.session_runner.check_llm_settings",
        lambda: None,
    )
    monkeypatch.setattr(
        "tools.investigation.capability.astream_investigation",
        _fake_astream,
    )

    session_runner.run_sample_alert_for_session(
        template_name="generic",
        render_stream=_collecting_renderer(),
    )

    assert captured_alert.get("pipeline_name") == "payments_etl"


def test_run_investigation_for_session_background_uses_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tools.investigation.session_runner.check_llm_settings",
        lambda: None,
    )

    async def _empty_gen():
        if False:
            yield StreamEvent(event_type="end")

    monkeypatch.setattr(
        "tools.investigation.capability.astream_investigation",
        lambda **_kwargs: _empty_gen(),
    )

    result = session_runner.run_investigation_for_session_background(
        alert_text="background test",
        render_stream=_collecting_renderer({"status": "silent"}),
    )

    assert result["status"] == "silent"


def test_run_session_alert_payload_does_not_mutate_raw_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tools.investigation.session_runner.check_llm_settings",
        lambda: None,
    )

    async def _empty_gen():
        if False:
            yield StreamEvent(event_type="end")

    monkeypatch.setattr(
        "tools.investigation.capability.astream_investigation",
        lambda **_kwargs: _empty_gen(),
    )

    shared: dict[str, Any] = {"alert_name": "test", "annotations": {"keep": "yes"}}
    original = dict(shared)
    original["annotations"] = dict(shared["annotations"])

    session_runner.run_session_alert_payload(
        raw_alert=shared,
        context_overrides={"add": "value"},
        render_stream=_collecting_renderer(),
    )

    assert shared == original
