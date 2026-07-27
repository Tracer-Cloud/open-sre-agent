from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from surfaces.cli.wizard.configurators import observability


def test_configure_grafana_local_compose_file_path_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for a __file__-relative path breaking when the module moves.

    _configure_grafana_local resolves local_grafana_stack/docker-compose.yml relative
    to its own module file. Moving the module to a different directory depth (e.g. into
    the configurators/ package) silently breaks this unless the traversal is updated to
    match, and `docker compose -f <bad-path> up -d` fails with a confusing error far from
    the actual bug.
    """
    captured_compose_paths: list[str] = []

    def _fake_run(args: list[str], **_kwargs: object) -> object:
        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        if args[:2] == ["docker", "info"]:
            return _Result()
        if args[:2] == ["docker", "compose"]:
            captured_compose_paths.append(args[args.index("-f") + 1])
            # Short-circuit before the real seed_logs()/Loki wait — this test only
            # cares about the compose file path passed to the docker CLI.
            return type("Result", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        raise AssertionError(f"unexpected subprocess.run call: {args}")

    monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", _fake_run)

    observability._configure_grafana_local()

    assert len(captured_compose_paths) == 1
    compose_path = Path(captured_compose_paths[0])
    assert compose_path.exists(), f"compose file does not exist: {compose_path}"
    assert compose_path.name == "docker-compose.yml"
