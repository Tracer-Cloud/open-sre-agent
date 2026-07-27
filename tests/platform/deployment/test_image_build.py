"""Tests for the decoupled image-build path (build_image, _resolve_image_uri)."""

from __future__ import annotations

from pathlib import Path

import pytest

import platform.deployment.aws.ecr as ecr_module
import platform.deployment.ecr_deploy.stack as stack_module
from platform.deployment.aws.config import ECR_IMAGE_TAG_ENV
from platform.deployment.ecr_deploy import lifecycle as deploy_module
from platform.deployment.ecr_deploy.lifecycle import _IMAGE_URI_ENV

_FAKE_URI = "123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre:latest"


# ── _resolve_image_uri ────────────────────────────────────────────────────────


def test_resolve_image_uri_uses_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_IMAGE_URI_ENV, _FAKE_URI)
    assert deploy_module._resolve_image_uri() == _FAKE_URI


def test_resolve_image_uri_falls_back_to_saved_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    uri_file = tmp_path / "image-uri.txt"
    uri_file.write_text(_FAKE_URI + "\n")
    monkeypatch.delenv(_IMAGE_URI_ENV, raising=False)
    monkeypatch.setattr(stack_module, "_IMAGE_URI_FILE", uri_file)

    assert deploy_module._resolve_image_uri() == _FAKE_URI


def test_resolve_image_uri_env_takes_precedence_over_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    uri_file = tmp_path / "image-uri.txt"
    uri_file.write_text("stale-uri-from-file:old\n")
    monkeypatch.setattr(stack_module, "_IMAGE_URI_FILE", uri_file)
    monkeypatch.setenv(_IMAGE_URI_ENV, _FAKE_URI)

    assert deploy_module._resolve_image_uri() == _FAKE_URI


def test_resolve_image_uri_fails_fast_when_nothing_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(_IMAGE_URI_ENV, raising=False)
    monkeypatch.setattr(stack_module, "_IMAGE_URI_FILE", tmp_path / "nonexistent.txt")

    with pytest.raises(RuntimeError, match="make build-image"):
        deploy_module._resolve_image_uri()


# ── build_image ───────────────────────────────────────────────────────────────


def test_build_image_saves_uri_and_returns_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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
    monkeypatch.setattr(deploy_module, "_resolve_image_sha_tag", lambda: "abc123def456")
    monkeypatch.setattr(deploy_module.ecr, "get_pushed_image_digest", lambda *_a, **_kw: None)

    result = deploy_module.build_image()

    assert result == _FAKE_URI
    assert uri_file.exists()
    assert uri_file.read_text().strip() == _FAKE_URI


def test_build_image_passes_sha_as_extra_tag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """build_image must forward the resolved sha tag to build_and_push so both
    latest and the immutable sha are pushed from one build."""
    monkeypatch.setattr(stack_module, "_IMAGE_URI_FILE", tmp_path / "image-uri.txt")
    monkeypatch.setattr(
        deploy_module.ecr,
        "create_repository",
        lambda *_a, **_kw: {"uri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre"},
    )
    monkeypatch.setattr(deploy_module, "_resolve_image_sha_tag", lambda: "abc123def456")

    captured: dict[str, object] = {}

    def _fake_build_and_push(*_a: object, **kw: object) -> str:
        captured.update(kw)
        return _FAKE_URI

    monkeypatch.setattr(deploy_module.ecr, "build_and_push", _fake_build_and_push)
    monkeypatch.setattr(deploy_module.ecr, "get_pushed_image_digest", lambda *_a, **_kw: None)

    deploy_module.build_image()

    assert captured["tag"] == deploy_module.ECR_DEFAULT_IMAGE_TAG
    assert captured["extra_tags"] == ["abc123def456"]


def test_build_image_resolves_digest_by_built_image_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The digest lookup must use the image ID captured at build time, not a
    tag reference that a concurrent build could retag."""
    repo_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre"
    image_id = "sha256:" + "c" * 64
    monkeypatch.setattr(stack_module, "_IMAGE_URI_FILE", tmp_path / "image-uri.txt")
    monkeypatch.setattr(
        deploy_module.ecr, "create_repository", lambda *_a, **_kw: {"uri": repo_uri}
    )
    monkeypatch.setattr(deploy_module, "_resolve_image_sha_tag", lambda: "abc123def456")

    def _fake_build_and_push(*_a: object, **kw: object) -> str:
        iidfile = kw.get("iidfile")
        assert isinstance(iidfile, Path), "build_image must request an iidfile"
        iidfile.write_text(image_id + "\n")
        return _FAKE_URI

    monkeypatch.setattr(deploy_module.ecr, "build_and_push", _fake_build_and_push)

    digest_calls: list[tuple[object, ...]] = []

    def _fake_digest(*args: object) -> str | None:
        digest_calls.append(args)
        return None

    monkeypatch.setattr(deploy_module.ecr, "get_pushed_image_digest", _fake_digest)

    deploy_module.build_image()

    assert digest_calls == [(repo_uri, image_id)]


def test_build_image_prints_digest_uri_for_prod_pinning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Prod pin guidance must reference the image digest, not a mutable tag."""
    repo_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre"
    monkeypatch.setattr(stack_module, "_IMAGE_URI_FILE", tmp_path / "image-uri.txt")
    monkeypatch.setattr(
        deploy_module.ecr, "create_repository", lambda *_a, **_kw: {"uri": repo_uri}
    )
    monkeypatch.setattr(deploy_module, "_resolve_image_sha_tag", lambda: "abc123def456")
    monkeypatch.setattr(deploy_module.ecr, "build_and_push", lambda *_a, **_kw: _FAKE_URI)
    monkeypatch.setattr(
        deploy_module.ecr, "get_pushed_image_digest", lambda *_a, **_kw: "sha256:" + "f" * 64
    )

    deploy_module.build_image()

    out = capsys.readouterr().out
    assert f"{repo_uri}@sha256:{'f' * 64}" in out
    assert "pin the digest URI" in out
    assert "pin the sha URI" not in out


def test_build_image_warns_when_digest_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No digest resolved → no tag may be presented as a prod pin."""
    repo_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre"
    monkeypatch.setattr(stack_module, "_IMAGE_URI_FILE", tmp_path / "image-uri.txt")
    monkeypatch.setattr(
        deploy_module.ecr, "create_repository", lambda *_a, **_kw: {"uri": repo_uri}
    )
    monkeypatch.setattr(deploy_module, "_resolve_image_sha_tag", lambda: "abc123def456")
    monkeypatch.setattr(deploy_module.ecr, "build_and_push", lambda *_a, **_kw: _FAKE_URI)
    monkeypatch.setattr(deploy_module.ecr, "get_pushed_image_digest", lambda *_a, **_kw: None)

    deploy_module.build_image()

    out = capsys.readouterr().out
    assert "digest lookup failed" in out
    assert "pin the digest URI" not in out
    assert "pin the sha URI" not in out


# ── ecr.get_pushed_image_digest ───────────────────────────────────────────────

_REPO = "123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre"
_IMAGE_ID = "sha256:" + "c" * 64


def _stub_docker_inspect(monkeypatch: pytest.MonkeyPatch, stdout: str) -> None:
    def _fake_run(cmd: list[str], **_kw: object):
        import subprocess as _sp

        assert cmd[:3] == ["docker", "image", "inspect"]
        assert cmd[3] == _IMAGE_ID, (
            "inspect must target the immutable image ID, never a tag — a tag "
            "can be retagged by a concurrent build on the same daemon"
        )
        return _sp.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(ecr_module.subprocess, "run", _fake_run)


def test_get_pushed_image_digest_inspects_by_image_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The digest must come from the daemon's record for OUR built image ID, so
    neither a concurrent registry push nor a concurrent local retag can
    substitute another image."""
    digest = "sha256:" + "a" * 64
    _stub_docker_inspect(monkeypatch, f'["{_REPO}@{digest}"]\n')

    assert ecr_module.get_pushed_image_digest(_REPO, _IMAGE_ID) == digest


def test_get_pushed_image_digest_matches_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """RepoDigests entries for other repositories must not be returned."""
    other = "999999999999.dkr.ecr.us-east-1.amazonaws.com/other@sha256:" + "b" * 64
    _stub_docker_inspect(monkeypatch, f'["{other}"]\n')

    assert ecr_module.get_pushed_image_digest(_REPO, _IMAGE_ID) is None


def test_get_pushed_image_digest_none_on_inspect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: object, **_kw: object) -> object:
        raise FileNotFoundError("docker not found")

    monkeypatch.setattr(ecr_module.subprocess, "run", _boom)

    assert ecr_module.get_pushed_image_digest(_REPO, _IMAGE_ID) is None


def test_get_pushed_image_digest_none_for_empty_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_if_called(*_a: object, **_kw: object) -> object:
        raise AssertionError("docker must not be invoked without an image ref")

    monkeypatch.setattr(ecr_module.subprocess, "run", _fail_if_called)

    assert ecr_module.get_pushed_image_digest(_REPO, "") is None


# ── _resolve_image_sha_tag ─────────────────────────────────────────────────────


def test_resolve_sha_tag_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ECR_IMAGE_TAG_ENV, "release-2026-07-24")
    assert deploy_module._resolve_image_sha_tag() == "release-2026-07-24"


def test_resolve_sha_tag_marks_dirty_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ECR_IMAGE_TAG_ENV, raising=False)

    def _fake_run(cmd: list[str], **_kw: object):
        import subprocess as _sp

        if cmd[:2] == ["git", "rev-parse"]:
            return _sp.CompletedProcess(cmd, 0, stdout="deadbeef1234\n", stderr="")
        # git status --porcelain → non-empty ⇒ dirty
        return _sp.CompletedProcess(cmd, 0, stdout=" M some/file.py\n", stderr="")

    monkeypatch.setattr(deploy_module.subprocess, "run", _fake_run)
    assert deploy_module._resolve_image_sha_tag() == "deadbeef1234-dirty"


def test_resolve_sha_tag_clean_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ECR_IMAGE_TAG_ENV, raising=False)

    def _fake_run(cmd: list[str], **_kw: object):
        import subprocess as _sp

        if cmd[:2] == ["git", "rev-parse"]:
            return _sp.CompletedProcess(cmd, 0, stdout="deadbeef1234\n", stderr="")
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(deploy_module.subprocess, "run", _fake_run)
    assert deploy_module._resolve_image_sha_tag() == "deadbeef1234"


def test_resolve_sha_tag_treats_failed_status_as_dirty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-zero `git status` must not read as clean — assume dirty."""
    monkeypatch.delenv(ECR_IMAGE_TAG_ENV, raising=False)

    def _fake_run(cmd: list[str], **_kw: object):
        import subprocess as _sp

        if cmd[:2] == ["git", "rev-parse"]:
            return _sp.CompletedProcess(cmd, 0, stdout="deadbeef1234\n", stderr="")
        return _sp.CompletedProcess(cmd, 1, stdout="", stderr="fatal: not a git repo")

    monkeypatch.setattr(deploy_module.subprocess, "run", _fake_run)
    assert deploy_module._resolve_image_sha_tag() == "deadbeef1234-dirty"


def test_resolve_sha_tag_none_when_git_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ECR_IMAGE_TAG_ENV, raising=False)

    def _boom(*_a: object, **_kw: object):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(deploy_module.subprocess, "run", _boom)
    assert deploy_module._resolve_image_sha_tag() is None


# ── ecr.build_and_push (multi-tag) ─────────────────────────────────────────────


def _capture_docker(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Stub docker_login + subprocess.run; return the list of captured argv."""
    calls: list[list[str]] = []
    monkeypatch.setattr(ecr_module, "docker_login", lambda *_a, **_kw: None)

    def _fake_run(cmd: list[str], **_kw: object):
        import subprocess as _sp

        calls.append(cmd)
        return _sp.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ecr_module.subprocess, "run", _fake_run)
    return calls


def test_build_and_push_pushes_latest_and_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _capture_docker(monkeypatch)
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    repo = "123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre"

    primary = ecr_module.build_and_push(
        dockerfile_path=dockerfile,
        repository_uri=repo,
        tag="latest",
        extra_tags=["abc123"],
        context_dir=tmp_path,
    )

    assert primary == f"{repo}:latest"
    build = next(c for c in calls if c[:2] == ["docker", "build"])
    assert build.count("-t") == 2
    assert f"{repo}:latest" in build and f"{repo}:abc123" in build
    pushes = [c[2] for c in calls if c[:2] == ["docker", "push"]]
    assert pushes == [f"{repo}:abc123", f"{repo}:latest"], (
        "the moving primary tag must be pushed last, after every extra tag, "
        "so a failed extra-tag push cannot leave latest mutated"
    )


def test_build_and_push_single_tag_without_extra_tags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Callers that pass no extra_tags keep the original one-tag, one-push behavior."""
    calls = _capture_docker(monkeypatch)
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    repo = "123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre"

    primary = ecr_module.build_and_push(
        dockerfile_path=dockerfile,
        repository_uri=repo,
        tag="latest",
        context_dir=tmp_path,
    )

    assert primary == f"{repo}:latest"
    build = next(c for c in calls if c[:2] == ["docker", "build"])
    assert build.count("-t") == 1
    pushes = [c[2] for c in calls if c[:2] == ["docker", "push"]]
    assert pushes == [f"{repo}:latest"]


def test_build_and_push_forwards_iidfile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _capture_docker(monkeypatch)
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    iidfile = tmp_path / "image-id"

    ecr_module.build_and_push(
        dockerfile_path=dockerfile,
        repository_uri="123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre",
        tag="latest",
        context_dir=tmp_path,
        iidfile=iidfile,
    )

    build = next(c for c in calls if c[:2] == ["docker", "build"])
    assert "--iidfile" in build
    assert build[build.index("--iidfile") + 1] == str(iidfile)


def test_build_and_push_dedupes_when_sha_equals_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _capture_docker(monkeypatch)
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    repo = "123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre"

    ecr_module.build_and_push(
        dockerfile_path=dockerfile,
        repository_uri=repo,
        tag="latest",
        extra_tags=["latest"],  # duplicate must not double-push
        context_dir=tmp_path,
    )

    pushes = [c[2] for c in calls if c[:2] == ["docker", "push"]]
    assert pushes == [f"{repo}:latest"]
