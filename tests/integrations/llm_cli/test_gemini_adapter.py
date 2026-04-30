"""Tests for Gemini CLI adapter detection and invocation helpers."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.integrations.llm_cli.binary_resolver import npm_prefix_bin_dirs
from app.integrations.llm_cli.gemini import GeminiAdapter, _fallback_gemini_paths


def _posix_path_set(paths: list[str]) -> set[str]:
    """Normalize paths for assertions when simulating POSIX platforms on Windows CI."""
    return {Path(p).as_posix() for p in paths}


def _version_proc() -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = "0.31.0\n"
    m.stderr = ""
    return m


@patch("app.integrations.llm_cli.gemini.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_path_binary_with_api_key_auth(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/gemini"
    mock_run.return_value = _version_proc()

    with patch.dict(
        os.environ,
        {"GEMINI_BIN": "", "GEMINI_API_KEY": "gemini-secret"},
        clear=False,
    ):
        probe = GeminiAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert probe.bin_path == "/usr/bin/gemini"
    assert probe.version == "0.31.0"
    mock_run.assert_called_once()


@patch("app.integrations.llm_cli.gemini.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_installed_auth_unknown_without_env(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    mock_which.return_value = "/usr/bin/gemini"
    mock_run.return_value = _version_proc()

    with patch.dict(
        os.environ,
        {
            "GEMINI_API_KEY": "",
            "GEMINI_BIN": "",
            "GOOGLE_API_KEY": "",
            "GOOGLE_GENAI_USE_VERTEXAI": "",
        },
        clear=False,
    ):
        probe = GeminiAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is None
    assert "not directly probeable" in probe.detail


@patch("app.integrations.llm_cli.gemini.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_vertex_env_incomplete_is_not_authenticated(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    mock_which.return_value = "/usr/bin/gemini"
    mock_run.return_value = _version_proc()

    with patch.dict(
        os.environ,
        {
            "GEMINI_API_KEY": "",
            "GEMINI_BIN": "",
            "GOOGLE_API_KEY": "",
            "GOOGLE_GENAI_USE_VERTEXAI": "true",
            "GOOGLE_CLOUD_PROJECT": "",
            "GOOGLE_CLOUD_LOCATION": "",
        },
        clear=False,
    ):
        probe = GeminiAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is False
    assert "GOOGLE_CLOUD_PROJECT" in probe.detail


@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/gemini")
def test_build_uses_stdin_json_output_and_model_flag(mock_which: MagicMock) -> None:
    with patch.dict(os.environ, {"GEMINI_BIN": ""}, clear=False):
        inv = GeminiAdapter().build(prompt="hello", model="gemini-2.5-flash", workspace="/tmp")

    assert inv.stdin == "hello"
    assert inv.cwd == "/tmp"
    assert inv.argv[:4] == (
        "/usr/bin/gemini",
        "--output-format",
        "json",
        "--yolo",
    )
    assert inv.argv[-2:] == ("-m", "gemini-2.5-flash")
    assert inv.timeout_sec == 300.0
    mock_which.assert_called()


@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/gemini")
def test_build_accepts_timeout_override(mock_which: MagicMock) -> None:
    with patch.dict(
        os.environ,
        {"GEMINI_BIN": "", "GEMINI_CLI_TIMEOUT_SECONDS": "45.5"},
        clear=False,
    ):
        inv = GeminiAdapter().build(prompt="hello", model=None, workspace="/tmp")

    assert inv.timeout_sec == 45.5
    mock_which.assert_called()


@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/gemini")
def test_build_ignores_invalid_timeout_override(mock_which: MagicMock) -> None:
    with patch.dict(
        os.environ,
        {"GEMINI_BIN": "", "GEMINI_CLI_TIMEOUT_SECONDS": "not-a-number"},
        clear=False,
    ):
        inv = GeminiAdapter().build(prompt="hello", model=None, workspace="/tmp")

    assert inv.timeout_sec == 300.0
    mock_which.assert_called()


def test_parse_json_response() -> None:
    out = GeminiAdapter().parse(
        stdout='{"response":"hello from gemini","stats":{}}',
        stderr="",
        returncode=0,
    )

    assert out == "hello from gemini"


def test_explain_failure_redacts_google_api_key(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaThisIsASecretGoogleApiKeyValue")

    message = GeminiAdapter().explain_failure(
        stdout="",
        stderr="auth failed for AIzaThisIsASecretGoogleApiKeyValue",
        returncode=1,
    )

    assert "AIzaThisIsASecretGoogleApiKeyValue" not in message
    assert "authentication may be missing or expired" in message


@patch("app.integrations.llm_cli.gemini.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/gemini")
def test_detect_uses_gemini_bin_env_file(
    mock_which: MagicMock, mock_run: MagicMock, tmp_path: Path
) -> None:
    fake_bin = tmp_path / "my-gemini"
    fake_bin.write_bytes(b"")
    os.chmod(fake_bin, 0o700)
    mock_run.return_value = _version_proc()

    with patch.dict(os.environ, {"GEMINI_BIN": str(fake_bin), "GEMINI_API_KEY": "x"}):
        probe = GeminiAdapter().detect()

    assert probe.bin_path == str(fake_bin)
    assert probe.installed is True
    mock_which.assert_not_called()


def test_fallback_paths_include_macos_defaults() -> None:
    npm_prefix_bin_dirs.cache_clear()
    with (
        patch("app.integrations.llm_cli.binary_resolver.sys.platform", "darwin"),
        patch.dict(os.environ, {}, clear=False),
    ):
        paths = _fallback_gemini_paths()

    normalized = _posix_path_set(paths)
    assert "/opt/homebrew/bin/gemini" in normalized
    assert "/usr/local/bin/gemini" in normalized
    assert (Path.home() / ".local/bin/gemini").as_posix() in normalized


@patch("app.integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_forwards_gemini_env_keys(mock_run: MagicMock) -> None:
    from app.integrations.llm_cli.runner import CLIBackedLLMClient

    mock_adapter = MagicMock()
    mock_adapter.name = "gemini"
    mock_adapter.env_passthrough_keys = ()
    mock_adapter.env_passthrough_prefixes = ("GEMINI_", "GOOGLE_")
    mock_adapter.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/gemini",
        logged_in=True,
        detail="ok",
    )
    mock_adapter.build.return_value = MagicMock(
        argv=["/usr/bin/gemini", "--output-format", "json"],
        stdin="hello",
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.parse.return_value = "answer"
    mock_adapter.explain_failure.return_value = "fail"
    mock_run.return_value = MagicMock(returncode=0, stdout='{"response":"answer"}', stderr="")

    with (
        patch("app.guardrails.engine.get_guardrail_engine") as gr,
        patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "gemini-secret",
                "GOOGLE_CLOUD_PROJECT": "demo-project",
                "PATH": "/usr/bin",
                "ANTHROPIC_API_KEY": "anthropic-secret",
            },
            clear=False,
        ),
    ):
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model=None, max_tokens=256)
        resp = client.invoke("hello")

    assert resp.content == "answer"
    env = mock_run.call_args.kwargs["env"]
    assert env["GEMINI_API_KEY"] == "gemini-secret"
    assert env["GOOGLE_CLOUD_PROJECT"] == "demo-project"
    assert env["PATH"] == "/usr/bin"
    assert "ANTHROPIC_API_KEY" not in env


def test_gemini_cli_registry_entry() -> None:
    from app.integrations.llm_cli.registry import get_cli_provider_registration

    reg = get_cli_provider_registration("gemini-cli")
    assert reg is not None
    assert reg.model_env_key == "GEMINI_CLI_MODEL"
    assert reg.adapter_factory().name == "gemini"
