"""Tests for the decoupled image-build path (build_image, _resolve_image_uri)."""

from __future__ import annotations

import pytest

import platform.deployment.ecr_deploy.stack as stack_module
from platform.deployment.ecr_deploy import lifecycle as deploy_module
from platform.deployment.ecr_deploy.lifecycle import _IMAGE_URI_ENV

_FAKE_URI = "123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre:latest"


# ── _resolve_image_uri ────────────────────────────────────────────────────────


def test_resolve_image_uri_uses_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_IMAGE_URI_ENV, _FAKE_URI)
    assert deploy_module._resolve_image_uri() == _FAKE_URI


def test_resolve_image_uri_falls_back_to_saved_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    uri_file = tmp_path / "image-uri.txt"
    uri_file.write_text(_FAKE_URI + "\n")
    monkeypatch.delenv(_IMAGE_URI_ENV, raising=False)
    monkeypatch.setattr(stack_module, "_IMAGE_URI_FILE", uri_file)

    assert deploy_module._resolve_image_uri() == _FAKE_URI


def test_resolve_image_uri_env_takes_precedence_over_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    uri_file = tmp_path / "image-uri.txt"
    uri_file.write_text("stale-uri-from-file:old\n")
    monkeypatch.setattr(stack_module, "_IMAGE_URI_FILE", uri_file)
    monkeypatch.setenv(_IMAGE_URI_ENV, _FAKE_URI)

    assert deploy_module._resolve_image_uri() == _FAKE_URI


def test_resolve_image_uri_fails_fast_when_nothing_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    monkeypatch.delenv(_IMAGE_URI_ENV, raising=False)
    monkeypatch.setattr(stack_module, "_IMAGE_URI_FILE", tmp_path / "nonexistent.txt")

    with pytest.raises(RuntimeError, match="make build-image"):
        deploy_module._resolve_image_uri()


# ── _image_tag ────────────────────────────────────────────────────────────────


class _FakeCompleted:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def _fake_git(sha: str, porcelain: str):  # noqa: ANN202 - test helper
    def run(cmd: list[str], **_kw: object) -> _FakeCompleted:
        if cmd[:3] == ["git", "rev-parse", "--short"]:
            return _FakeCompleted(sha + "\n")
        if cmd[:2] == ["git", "status"]:
            return _FakeCompleted(porcelain)
        raise AssertionError(f"unexpected command: {cmd}")

    return run


def test_image_tag_clean_tree_is_sha_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy_module.subprocess, "run", _fake_git("abc1234", ""))
    assert deploy_module._image_tag() == "sha-abc1234"


def test_image_tag_dirty_tree_is_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy_module.subprocess, "run", _fake_git("abc1234", " M file.py\n"))
    assert deploy_module._image_tag() == "sha-abc1234-dirty"


# ── build_image ───────────────────────────────────────────────────────────────


def test_build_image_saves_uri_and_returns_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    uri_file = tmp_path / "image-uri.txt"
    monkeypatch.setattr(stack_module, "_IMAGE_URI_FILE", uri_file)

    monkeypatch.setattr(
        deploy_module.ecr,
        "create_repository",
        lambda *_a, **_kw: {"uri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre"},
    )
    monkeypatch.setattr(
        deploy_module.ecr,
        "build_and_push",
        lambda *_a, **_kw: _FAKE_URI,
    )
    monkeypatch.setattr(deploy_module, "_image_tag", lambda: "sha-abc1234")

    result = deploy_module.build_image()

    assert result == _FAKE_URI
    assert uri_file.exists()
    assert uri_file.read_text().strip() == _FAKE_URI


def test_build_image_pushes_sha_tag_with_latest_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    monkeypatch.setattr(stack_module, "_IMAGE_URI_FILE", tmp_path / "image-uri.txt")
    monkeypatch.setattr(
        deploy_module.ecr,
        "create_repository",
        lambda *_a, **_kw: {"uri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre"},
    )
    monkeypatch.setattr(deploy_module, "_image_tag", lambda: "sha-abc1234")

    captured: dict[str, object] = {}

    def fake_build_and_push(*_a: object, **kw: object) -> str:
        captured.update(kw)
        return _FAKE_URI

    monkeypatch.setattr(deploy_module.ecr, "build_and_push", fake_build_and_push)

    deploy_module.build_image()

    assert captured["tag"] == "sha-abc1234"
    assert captured["extra_tags"] == ("latest",)
