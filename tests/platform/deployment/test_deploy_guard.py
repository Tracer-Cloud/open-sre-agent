from __future__ import annotations

import pytest

from platform.deployment import prep


def test_cleanup_skips_when_no_existing_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prep, "outputs_exists", lambda: False)
    monkeypatch.setattr(prep, "find_stack_instance_ids", lambda *_args, **_kwargs: [])

    assert prep.cleanup_existing_deployment() is False


def test_cleanup_terminates_orphans_then_runs_destroy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminated: list[str] = []
    destroy_calls: list[int] = []

    monkeypatch.setattr(prep, "outputs_exists", lambda: True)
    monkeypatch.setattr(
        prep,
        "find_stack_instance_ids",
        lambda *_args, **_kwargs: ["i-old", "i-current"],
    )
    monkeypatch.setattr(
        prep,
        "terminate_instance",
        lambda instance_id, _region: terminated.append(instance_id),
    )
    monkeypatch.setattr("platform.deployment.lifecycle.destroy", lambda: destroy_calls.append(1))

    assert prep.cleanup_existing_deployment() is True
    assert terminated == ["i-old", "i-current"]
    assert destroy_calls == [1]


def test_cleanup_terminates_orphans_without_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminated: list[str] = []
    destroy_calls: list[int] = []

    monkeypatch.setattr(prep, "outputs_exists", lambda: False)
    monkeypatch.setattr(prep, "find_stack_instance_ids", lambda *_args, **_kwargs: ["i-orphan"])
    monkeypatch.setattr(
        prep,
        "terminate_instance",
        lambda instance_id, _region: terminated.append(instance_id),
    )
    monkeypatch.setattr("platform.deployment.lifecycle.destroy", lambda: destroy_calls.append(1))

    assert prep.cleanup_existing_deployment() is True
    assert terminated == ["i-orphan"]
    assert destroy_calls == []


def test_cleanup_aborts_when_env_var_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSRE_DEPLOY_ABORT_IF_EXISTS", "1")
    monkeypatch.setattr(prep, "outputs_exists", lambda: True)
    monkeypatch.setattr(prep, "find_stack_instance_ids", lambda *_args, **_kwargs: ["i-123"])

    with pytest.raises(RuntimeError, match="Existing deployment detected"):
        prep.cleanup_existing_deployment()
