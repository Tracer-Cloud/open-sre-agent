from __future__ import annotations

import pytest

from core.orchestration import entrypoints as runners
from platform.observability import errors


def test_run_investigation_initializes_sentry_and_captures_unhandled_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentry_init_calls: list[None] = []
    captured_errors: list[BaseException] = []
    expected_error = RuntimeError("investigation failed")

    def failing_run(*_args: object, **_kwargs: object) -> object:
        raise expected_error

    def capture_stub(exc: BaseException, **_kwargs: object) -> None:
        captured_errors.append(exc)

    import core.orchestration.pipeline as pipeline_module

    monkeypatch.setattr(runners, "init_sentry", lambda **_kw: sentry_init_calls.append(None))
    monkeypatch.setattr(errors, "capture_exception", capture_stub)
    monkeypatch.setattr(pipeline_module, "run_connected_investigation", failing_run)

    with pytest.raises(RuntimeError, match="investigation failed"):
        runners.run_investigation("cpu high on api")

    assert sentry_init_calls == [None]
    assert captured_errors == [expected_error]


def test_traced_node_exception_is_captured_once_with_node_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[BaseException, dict[str, object]]] = []
    expected_error = RuntimeError("node failed")

    def failing_node() -> None:
        raise expected_error

    def capture_stub(exc: BaseException, **kwargs: object) -> None:
        captured.append((exc, kwargs))

    monkeypatch.setattr("platform.observability.sentry_sdk.capture_exception", capture_stub)

    with pytest.raises(RuntimeError, match="node failed") as raised:
        runners._traced_node("extract_alert", failing_node)

    runners._capture_exception_once(
        raised.value,
        context="pipeline.astream_investigation",
    )

    assert len(captured) == 1
    exc, kwargs = captured[0]
    assert exc is expected_error
    assert kwargs["context"] == "node.extract_alert"
    assert kwargs["tags"] == {"surface": "node", "node": "extract_alert"}
