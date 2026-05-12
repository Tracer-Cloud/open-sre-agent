from __future__ import annotations

from typing import Any


def test_run_investigation_wraps_runner_with_tracking(monkeypatch) -> None:
    track_calls: list[tuple[str, str]] = []

    class _TrackContext:
        def __enter__(self) -> None:
            return None

        def __exit__(self, exc_type, exc, tb) -> bool:
            _ = (exc_type, exc, tb)
            return False

    def fake_track_investigation(*, entrypoint, trigger_mode, **kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        track_calls.append((entrypoint.value, trigger_mode.value))
        return _TrackContext()

    captured_kwargs: dict[str, Any] = {}

    def fake_runner(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured_kwargs["args"] = args
        captured_kwargs["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr("app.entrypoints.sdk.track_investigation", fake_track_investigation)
    monkeypatch.setattr("app.pipeline.runners.run_investigation", fake_runner)

    from app.entrypoints.sdk import run_investigation

    result = run_investigation("alert-name", raw_alert={"foo": "bar"})

    assert result == {"ok": True}
    assert captured_kwargs["args"] == ("alert-name",)
    assert captured_kwargs["kwargs"] == {"raw_alert": {"foo": "bar"}}
    assert track_calls == [("sdk", "service_runtime")]
