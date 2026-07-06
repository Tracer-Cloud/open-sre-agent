from __future__ import annotations

import platform
from config.version import get_opensre_version
from surfaces.cli.__main__ import main


def test_version_subcommand(monkeypatch, capsys) -> None:
    monkeypatch.setattr("surfaces.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.__main__.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr("surfaces.cli.__main__.shutdown_analytics", lambda **_kw: None)

    rc = main(["version"])
    assert rc == 0

    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 3
    assert lines[0] == f"opensre {get_opensre_version()}"
    assert lines[1] == f"Python  {platform.python_version()}"
    assert lines[2] == f"OS      {platform.system().lower()} ({platform.machine()})"


def test_version_flag_uses_fast_path(monkeypatch, capsys) -> None:
    def fail_bootstrap(*_args, **_kwargs) -> None:
        raise AssertionError("--version should not bootstrap the full CLI")

    monkeypatch.setattr("surfaces.cli.__main__.init_sentry", fail_bootstrap)
    monkeypatch.setattr("surfaces.cli.__main__._sentry_entrypoint_for_invocation", fail_bootstrap)

    rc = main(["--version"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == f"opensre, version {get_opensre_version()}"
