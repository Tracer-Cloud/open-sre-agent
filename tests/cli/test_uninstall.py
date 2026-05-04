from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from app.cli.__main__ import cli
from app.cli.support.uninstall import _remove_path, run_uninstall


def test_remove_path_removes_file(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("data")
    ok, err = _remove_path(f)
    assert ok is True
    assert err is None
    assert not f.exists()


def test_remove_path_removes_directory(tmp_path: Path) -> None:
    d = tmp_path / "subdir"
    d.mkdir()
    (d / "child.txt").write_text("x")
    ok, err = _remove_path(d)
    assert ok is True
    assert err is None
    assert not d.exists()


def test_remove_path_nonexistent_returns_ok(tmp_path: Path) -> None:
    ok, err = _remove_path(tmp_path / "does_not_exist")
    assert ok is True
    assert err is None


def test_remove_path_returns_error_on_permission_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = tmp_path / "locked"
    d.mkdir()

    def _raise(path: str) -> None:
        raise OSError("Permission denied")

    monkeypatch.setattr("shutil.rmtree", _raise)
    ok, err = _remove_path(d)
    assert ok is False
    assert "Permission denied" in (err or "")


def test_run_uninstall_cancelled_by_user(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.cli.support.uninstall._data_dirs", lambda: [])
    monkeypatch.setattr("app.cli.support.uninstall._is_binary_install", lambda: False)

    import questionary as _q

    monkeypatch.setattr(
        _q,
        "confirm",
        lambda *_args, **_kwargs: type("Q", (), {"ask": lambda _self: False})(),
    )

    rc = run_uninstall(yes=False)

    assert rc == 0
    assert "Cancelled" in capsys.readouterr().out


def test_run_uninstall_aborted_by_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.cli.support.uninstall._data_dirs", lambda: [])
    monkeypatch.setattr("app.cli.support.uninstall._is_binary_install", lambda: False)

    import questionary as _q

    def _raise_interrupt(*a: object, **kw: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(_q, "confirm", _raise_interrupt)

    rc = run_uninstall(yes=False)

    assert rc == 1
    assert "Aborted" in capsys.readouterr().out


def test_run_uninstall_skips_missing_dirs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    missing = tmp_path / "does_not_exist"
    monkeypatch.setattr("app.cli.support.uninstall._data_dirs", lambda: [missing])
    monkeypatch.setattr("app.cli.support.uninstall._is_binary_install", lambda: False)
    monkeypatch.setattr("app.cli.support.uninstall._pip_uninstall", lambda: 0)

    rc = run_uninstall(yes=True)

    assert rc == 0
    out = capsys.readouterr().out
    assert "not found" in out
    assert "skipped" in out


def test_run_uninstall_removes_existing_dir(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    d = tmp_path / "tracer_home"
    d.mkdir()
    monkeypatch.setattr("app.cli.support.uninstall._data_dirs", lambda: [d])
    monkeypatch.setattr("app.cli.support.uninstall._is_binary_install", lambda: False)
    monkeypatch.setattr("app.cli.support.uninstall._pip_uninstall", lambda: 0)

    rc = run_uninstall(yes=True)

    assert rc == 0
    assert not d.exists()
    assert "deleted" in capsys.readouterr().out


def test_run_uninstall_pip_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.cli.support.uninstall._data_dirs", lambda: [])
    monkeypatch.setattr("app.cli.support.uninstall._is_binary_install", lambda: False)
    monkeypatch.setattr("app.cli.support.uninstall._pip_uninstall", lambda: 0)

    rc = run_uninstall(yes=True)

    assert rc == 0
    assert "opensre has been uninstalled" in capsys.readouterr().out


def test_run_uninstall_pip_failure_shows_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.cli.support.uninstall._data_dirs", lambda: [])
    monkeypatch.setattr("app.cli.support.uninstall._is_binary_install", lambda: False)
    monkeypatch.setattr("app.cli.support.uninstall._pip_uninstall", lambda: 1)
    monkeypatch.setattr("app.cli.support.uninstall._is_windows", lambda: False)

    rc = run_uninstall(yes=True)

    assert rc == 1
    err = capsys.readouterr().err
    assert "pip uninstall failed" in err
    assert "retry manually" in err


def test_run_uninstall_pip_failure_windows_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.cli.support.uninstall._data_dirs", lambda: [])
    monkeypatch.setattr("app.cli.support.uninstall._is_binary_install", lambda: False)
    monkeypatch.setattr("app.cli.support.uninstall._pip_uninstall", lambda: 1)
    monkeypatch.setattr("app.cli.support.uninstall._is_windows", lambda: True)

    rc = run_uninstall(yes=True)

    assert rc == 1
    assert "pip uninstall" in capsys.readouterr().err


def test_run_uninstall_binary_removes_executable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fake_exe = tmp_path / "opensre"
    fake_exe.write_bytes(b"\x7fELF")
    monkeypatch.setattr("app.cli.support.uninstall._data_dirs", lambda: [])
    monkeypatch.setattr("app.cli.support.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("app.cli.support.uninstall.sys.executable", str(fake_exe))

    rc = run_uninstall(yes=True)

    assert rc == 0
    assert not fake_exe.exists()
    assert "binary" in capsys.readouterr().out


def test_run_uninstall_dir_removal_error_sets_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    d = tmp_path / "locked_dir"
    d.mkdir()
    monkeypatch.setattr("app.cli.support.uninstall._data_dirs", lambda: [d])
    monkeypatch.setattr("app.cli.support.uninstall._is_binary_install", lambda: False)
    monkeypatch.setattr("app.cli.support.uninstall._pip_uninstall", lambda: 0)

    def _fail(path: str) -> None:
        raise OSError("Permission denied")

    monkeypatch.setattr("shutil.rmtree", _fail)

    rc = run_uninstall(yes=True)

    assert rc == 1
    assert "errors" in capsys.readouterr().err


def test_uninstall_command_registered() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["uninstall", "--help"])
    assert result.exit_code == 0
    assert "uninstall" in result.output.lower()


def test_uninstall_command_yes_flag_skips_prompt() -> None:
    runner = CliRunner()

    with (
        patch("app.cli.commands.general.capture_cli_invoked"),
        patch("app.cli.support.uninstall._data_dirs", return_value=[]),
        patch("app.cli.support.uninstall._is_binary_install", return_value=False),
        patch("app.cli.support.uninstall._pip_uninstall", return_value=0),
    ):
        result = runner.invoke(cli, ["uninstall", "--yes"])

    assert result.exit_code == 0
    assert "opensre has been uninstalled" in result.output


def test_uninstall_command_short_yes_flag() -> None:
    runner = CliRunner()

    with (
        patch("app.cli.commands.general.capture_cli_invoked"),
        patch("app.cli.support.uninstall._data_dirs", return_value=[]),
        patch("app.cli.support.uninstall._is_binary_install", return_value=False),
        patch("app.cli.support.uninstall._pip_uninstall", return_value=0),
    ):
        result = runner.invoke(cli, ["uninstall", "-y"])

    assert result.exit_code == 0


def test_uninstall_help_describes_command() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["uninstall", "--help"])
    assert result.exit_code == 0
    assert "Remove opensre and all local data from this machine." in result.output
