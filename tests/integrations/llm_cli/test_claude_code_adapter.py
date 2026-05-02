"""Tests for the Claude Code CLI adapter (detect / build / failure / env forwarding)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.integrations.llm_cli.binary_resolver import npm_prefix_bin_dirs
from app.integrations.llm_cli.claude_code import (
    _PROBE_TIMEOUT_SEC,
    ClaudeCodeAdapter,
    _classify_claude_code_auth,
    _fallback_claude_code_paths,
    _probe_cli_auth,
)


def _posix_path_set(paths: list[str]) -> set[str]:
    return {Path(p).as_posix() for p in paths}


# ---------------------------------------------------------------------------
# Auth classification
# ---------------------------------------------------------------------------


def test_classify_auth_api_key_set() -> None:
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-key"}, clear=False):
        logged_in, detail = _classify_claude_code_auth()
    assert logged_in is True
    assert "ANTHROPIC_API_KEY" in detail


def test_classify_auth_no_credentials_linux() -> None:
    """On Linux, no env var and no credentials file → definitive False."""
    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False),
        patch("app.integrations.llm_cli.claude_code.sys.platform", "linux"),
        patch("app.integrations.llm_cli.claude_code.Path") as mock_path,
    ):
        mock_creds = MagicMock()
        mock_creds.exists.return_value = False
        mock_path.home.return_value.__truediv__.return_value.__truediv__.return_value = mock_creds
        logged_in, _detail = _classify_claude_code_auth()
    assert logged_in is False


def test_classify_auth_no_credentials_macos_returns_none() -> None:
    """On macOS with no binary, no file, no API key → None (can't verify without binary)."""
    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False),
        patch("app.integrations.llm_cli.claude_code.sys.platform", "darwin"),
        patch("app.integrations.llm_cli.claude_code.Path") as mock_path,
    ):
        mock_creds = MagicMock()
        mock_creds.exists.return_value = False
        mock_path.home.return_value.__truediv__.return_value.__truediv__.return_value = mock_creds
        logged_in, detail = _classify_claude_code_auth()
    assert logged_in is None


def test_classify_auth_credentials_file_present(tmp_path: Path) -> None:
    # Create a fake ~/.claude/.credentials.json under tmp_path.
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    creds = claude_dir / ".credentials.json"
    creds.write_text('{"token": "abc"}')

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False),
        patch("app.integrations.llm_cli.claude_code.Path.home", return_value=tmp_path),
    ):
        logged_in, detail = _classify_claude_code_auth()

    assert logged_in is True
    assert "credentials.json" in detail


def test_classify_auth_credentials_file_unreadable() -> None:
    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False),
        patch("app.integrations.llm_cli.claude_code.Path") as mock_path,
    ):
        mock_creds = MagicMock()
        mock_creds.exists.return_value = True
        mock_creds.stat.side_effect = OSError("permission denied")
        mock_path.home.return_value.__truediv__.return_value.__truediv__.return_value = mock_creds
        logged_in, _detail = _classify_claude_code_auth()

    assert logged_in is None


def test_classify_auth_api_key_not_blocked_by_unreadable_creds() -> None:
    """ANTHROPIC_API_KEY must succeed even when credentials file raises OSError."""
    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=False),
        patch("app.integrations.llm_cli.claude_code.Path") as mock_path,
    ):
        mock_creds = MagicMock()
        mock_creds.exists.return_value = True
        mock_creds.stat.side_effect = OSError("permission denied")
        mock_path.home.return_value.__truediv__.return_value.__truediv__.return_value = mock_creds
        logged_in, detail = _classify_claude_code_auth()

    assert logged_in is True
    assert "ANTHROPIC_API_KEY" in detail


# ---------------------------------------------------------------------------
# CLI auth probe (_probe_cli_auth)
# ---------------------------------------------------------------------------


@patch("app.integrations.llm_cli.claude_code.subprocess.run")
def test_probe_cli_auth_subscription(mock_run: MagicMock) -> None:
    """Subscription login: loggedIn=true, no apiKeySource → subscription detail."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = '{"loggedIn": true, "email": "user@example.com"}\n'
    m.stderr = ""
    mock_run.return_value = m
    logged_in, detail = _probe_cli_auth("/usr/bin/claude")
    assert logged_in is True
    assert "subscription" in detail
    assert "user@example.com" in detail


@patch("app.integrations.llm_cli.claude_code.subprocess.run")
def test_probe_cli_auth_api_key(mock_run: MagicMock) -> None:
    """API key auth: loggedIn=true, apiKeySource present → API key detail."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = '{"loggedIn": true, "apiKeySource": "ANTHROPIC_API_KEY"}\n'
    m.stderr = ""
    mock_run.return_value = m
    logged_in, detail = _probe_cli_auth("/usr/bin/claude")
    assert logged_in is True
    assert "ANTHROPIC_API_KEY" in detail


@patch("app.integrations.llm_cli.claude_code.subprocess.run")
def test_probe_cli_auth_not_logged_in(mock_run: MagicMock) -> None:
    m = MagicMock()
    m.returncode = 0
    m.stdout = '{"loggedIn": false}\n'
    m.stderr = ""
    mock_run.return_value = m
    logged_in, detail = _probe_cli_auth("/usr/bin/claude")
    assert logged_in is False
    assert "Not authenticated" in detail


@patch("app.integrations.llm_cli.claude_code.subprocess.run")
def test_probe_cli_auth_nonzero_exit(mock_run: MagicMock) -> None:
    """Non-zero exit → None (probe failure, not confirmed unauthenticated)."""
    m = MagicMock()
    m.returncode = 1
    m.stdout = ""
    m.stderr = "unknown command 'auth'"
    mock_run.return_value = m
    logged_in, detail = _probe_cli_auth("/usr/bin/claude")
    assert logged_in is None
    assert "failed" in detail


@patch("app.integrations.llm_cli.claude_code.subprocess.run")
def test_probe_cli_auth_timeout(mock_run: MagicMock) -> None:
    import subprocess

    mock_run.side_effect = subprocess.TimeoutExpired(
        cmd=["/usr/bin/claude", "auth", "status"], timeout=_PROBE_TIMEOUT_SEC
    )
    logged_in, detail = _probe_cli_auth("/usr/bin/claude")
    assert logged_in is None
    assert "timed out" in detail


@patch("app.integrations.llm_cli.claude_code.subprocess.run")
def test_probe_cli_auth_os_error(mock_run: MagicMock) -> None:
    mock_run.side_effect = OSError("permission denied")
    logged_in, detail = _probe_cli_auth("/usr/bin/claude")
    assert logged_in is None
    assert "permission denied" in detail


@patch("app.integrations.llm_cli.claude_code.subprocess.run")
def test_probe_cli_auth_non_json_exit_zero(mock_run: MagicMock) -> None:
    """Older CLI versions that output non-JSON on exit 0 are treated as authenticated."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = "Logged in\n"
    m.stderr = ""
    mock_run.return_value = m
    logged_in, detail = _probe_cli_auth("/usr/bin/claude")
    assert logged_in is True
    assert "CLI" in detail


@patch("app.integrations.llm_cli.claude_code.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_subscription_authenticated(mock_which: MagicMock, mock_run: MagicMock) -> None:
    """detect() returns logged_in=True via subscription when binary is available."""
    mock_which.return_value = "/usr/bin/claude"

    auth_proc = MagicMock()
    auth_proc.returncode = 0
    auth_proc.stdout = '{"loggedIn": true, "email": "user@example.com"}\n'
    auth_proc.stderr = ""
    mock_run.side_effect = [_version_proc(), auth_proc]

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
        probe = ClaudeCodeAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert "subscription" in probe.detail


@patch("app.integrations.llm_cli.claude_code.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_api_key_authenticated(mock_which: MagicMock, mock_run: MagicMock) -> None:
    """detect() returns logged_in=True via API key when binary reports apiKeySource."""
    mock_which.return_value = "/usr/bin/claude"

    auth_proc = MagicMock()
    auth_proc.returncode = 0
    auth_proc.stdout = '{"loggedIn": true, "apiKeySource": "ANTHROPIC_API_KEY"}\n'
    auth_proc.stderr = ""
    mock_run.side_effect = [_version_proc(), auth_proc]

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=False):
        probe = ClaudeCodeAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert "ANTHROPIC_API_KEY" in probe.detail


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------


def _version_proc() -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = "1.2.3\n"
    m.stderr = ""
    return m


def _auth_status_proc(logged_in: bool, api_key_source: str = "", email: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    data: dict = {"loggedIn": logged_in}
    if api_key_source:
        data["apiKeySource"] = api_key_source
    if email:
        data["email"] = email
    m.stdout = json.dumps(data) + "\n"
    m.stderr = ""
    return m


@patch("app.integrations.llm_cli.claude_code.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_logged_in_via_api_key(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/claude"
    mock_run.side_effect = [
        _version_proc(),
        _auth_status_proc(True, api_key_source="ANTHROPIC_API_KEY"),
    ]

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=False):
        probe = ClaudeCodeAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert probe.bin_path == "/usr/bin/claude"
    assert probe.version == "1.2.3"


@patch("app.integrations.llm_cli.claude_code.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_not_authenticated(mock_which: MagicMock, mock_run: MagicMock) -> None:
    """When claude auth status reports not logged in, detect() returns logged_in=False."""
    mock_which.return_value = "/usr/bin/claude"
    mock_run.side_effect = [_version_proc(), _auth_status_proc(False)]

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
        probe = ClaudeCodeAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is False


@patch("app.integrations.llm_cli.claude_code._fallback_claude_code_paths", return_value=[])
@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value=None)
def test_detect_not_installed(_mock_which: MagicMock, _mock_fallback: MagicMock) -> None:
    probe = ClaudeCodeAdapter().detect()
    assert probe.installed is False
    assert probe.logged_in is None
    assert probe.bin_path is None
    assert "not found" in probe.detail.lower()


@patch("app.integrations.llm_cli.claude_code.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_version_command_fails(_mock_which: MagicMock, mock_run: MagicMock) -> None:
    _mock_which.return_value = "/usr/bin/claude"
    m = MagicMock()
    m.returncode = 1
    m.stdout = ""
    m.stderr = "some error\n"
    mock_run.return_value = m

    probe = ClaudeCodeAdapter().detect()

    assert probe.installed is False
    assert probe.logged_in is None


@patch("app.integrations.llm_cli.claude_code.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_version_os_error(_mock_which: MagicMock, mock_run: MagicMock) -> None:
    _mock_which.return_value = "/usr/bin/claude"
    mock_run.side_effect = OSError("not found")

    probe = ClaudeCodeAdapter().detect()

    assert probe.installed is False
    assert probe.logged_in is None


@patch("app.integrations.llm_cli.claude_code.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_version_timeout_expired(_mock_which: MagicMock, mock_run: MagicMock) -> None:
    """Cold-start `claude --version` can exceed the probe timeout; must not raise."""
    import subprocess

    _mock_which.return_value = "/usr/bin/claude"
    mock_run.side_effect = subprocess.TimeoutExpired(
        cmd=["/usr/bin/claude", "--version"], timeout=8.0
    )

    probe = ClaudeCodeAdapter().detect()

    assert probe.installed is False
    assert probe.logged_in is None
    assert probe.bin_path is None
    assert "could not run" in probe.detail.lower()
    assert "--version" in probe.detail


# ---------------------------------------------------------------------------
# build()
# ---------------------------------------------------------------------------


@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/claude")
def test_build_basic_invocation(_mock_which: MagicMock) -> None:
    inv = ClaudeCodeAdapter().build(prompt="explain this alert", model=None, workspace="")
    assert inv.argv[0] == "/usr/bin/claude"
    assert "-p" in inv.argv
    assert "--output-format" in inv.argv
    assert "text" in inv.argv
    assert inv.stdin == "explain this alert"
    assert inv.timeout_sec == 120.0


@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/claude")
def test_build_adds_model_flag(_mock_which: MagicMock) -> None:
    inv = ClaudeCodeAdapter().build(prompt="p", model="claude-opus-4-7", workspace="")
    assert "--model" in inv.argv
    idx = inv.argv.index("--model")
    assert inv.argv[idx + 1] == "claude-opus-4-7"


@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/claude")
def test_build_omits_model_flag_when_empty(_mock_which: MagicMock) -> None:
    inv = ClaudeCodeAdapter().build(prompt="p", model="", workspace="")
    assert "--model" not in inv.argv


@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/claude")
def test_build_omits_model_flag_when_none(_mock_which: MagicMock) -> None:
    inv = ClaudeCodeAdapter().build(prompt="p", model=None, workspace="")
    assert "--model" not in inv.argv


@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/claude")
def test_build_uses_provided_workspace(_mock_which: MagicMock) -> None:
    inv = ClaudeCodeAdapter().build(prompt="p", model=None, workspace="/my/project")
    assert inv.cwd == "/my/project"


@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/claude")
def test_build_sets_no_color_env(_mock_which: MagicMock) -> None:
    inv = ClaudeCodeAdapter().build(prompt="p", model=None, workspace="")
    assert inv.env is not None
    assert inv.env.get("NO_COLOR") == "1"


@patch("app.integrations.llm_cli.claude_code._fallback_claude_code_paths", return_value=[])
@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value=None)
def test_build_raises_when_binary_not_found(
    _mock_which: MagicMock, _mock_fallback: MagicMock
) -> None:
    import pytest

    with pytest.raises(RuntimeError, match="Claude Code CLI not found"):
        ClaudeCodeAdapter().build(prompt="p", model=None, workspace="")


# ---------------------------------------------------------------------------
# parse / explain_failure
# ---------------------------------------------------------------------------


def test_parse_returns_stripped_stdout() -> None:
    adapter = ClaudeCodeAdapter()
    result = adapter.parse(stdout="  hello world  \n", stderr="", returncode=0)
    assert result == "hello world"


def test_explain_failure_includes_returncode_and_stderr() -> None:
    adapter = ClaudeCodeAdapter()
    msg = adapter.explain_failure(stdout="", stderr="auth error", returncode=1)
    assert "1" in msg
    assert "auth error" in msg


def test_explain_failure_falls_back_to_stdout() -> None:
    adapter = ClaudeCodeAdapter()
    msg = adapter.explain_failure(stdout="some output", stderr="", returncode=2)
    assert "some output" in msg


# ---------------------------------------------------------------------------
# CLAUDE_CODE_BIN env override
# ---------------------------------------------------------------------------


def test_detect_uses_claude_code_bin_env(tmp_path: Path) -> None:
    fake_bin = tmp_path / "my-claude"
    fake_bin.write_bytes(b"")
    os.chmod(fake_bin, 0o700)

    with (
        patch.dict(
            os.environ,
            {"CLAUDE_CODE_BIN": str(fake_bin), "ANTHROPIC_API_KEY": "sk-t"},
            clear=False,
        ),
        patch("app.integrations.llm_cli.claude_code.subprocess.run") as mock_run,
    ):
        mock_run.return_value = _version_proc()
        probe = ClaudeCodeAdapter().detect()

    assert probe.bin_path == str(fake_bin)
    assert probe.installed is True
    assert mock_run.call_args[0][0][0] == str(fake_bin)


@patch("app.integrations.llm_cli.claude_code.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/claude")
def test_detect_falls_back_when_bin_env_invalid(
    _mock_which: MagicMock, mock_run: MagicMock
) -> None:
    with patch.dict(
        os.environ,
        {"CLAUDE_CODE_BIN": "/does/not/exist/claude", "ANTHROPIC_API_KEY": "sk-t"},
        clear=False,
    ):
        mock_run.return_value = _version_proc()
        probe = ClaudeCodeAdapter().detect()

    assert probe.bin_path == "/usr/bin/claude"
    assert probe.installed is True


# ---------------------------------------------------------------------------
# Fallback paths
# ---------------------------------------------------------------------------


def test_fallback_paths_macos() -> None:
    npm_prefix_bin_dirs.cache_clear()
    with (
        patch("app.integrations.llm_cli.binary_resolver.sys.platform", "darwin"),
        patch.dict(os.environ, {}, clear=False),
    ):
        paths = _fallback_claude_code_paths()

    normalized = _posix_path_set(paths)
    assert "/opt/homebrew/bin/claude" in normalized
    assert "/usr/local/bin/claude" in normalized
    assert (Path.home() / ".local/bin/claude").as_posix() in normalized


def test_fallback_paths_linux() -> None:
    npm_prefix_bin_dirs.cache_clear()
    with (
        patch("app.integrations.llm_cli.binary_resolver.sys.platform", "linux"),
        patch.dict(os.environ, {"npm_config_prefix": "/custom/npm"}, clear=False),
    ):
        paths = _fallback_claude_code_paths()

    normalized = _posix_path_set(paths)
    assert "/custom/npm/bin/claude" in normalized


def test_fallback_paths_windows() -> None:
    npm_prefix_bin_dirs.cache_clear()
    with (
        patch("app.integrations.llm_cli.binary_resolver.sys.platform", "win32"),
        patch.dict(
            os.environ,
            {
                "APPDATA": r"C:\Users\me\AppData\Roaming",
                "LOCALAPPDATA": r"C:\Users\me\AppData\Local",
            },
            clear=False,
        ),
    ):
        paths = _fallback_claude_code_paths()

    normalized = {p.replace("\\", "/") for p in paths}
    assert "C:/Users/me/AppData/Roaming/npm/claude.cmd" in normalized
    assert "C:/Users/me/AppData/Roaming/npm/claude.exe" in normalized


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_claude_code_registry_entry() -> None:
    from app.integrations.llm_cli.registry import get_cli_provider_registration

    reg = get_cli_provider_registration("claude-code")
    assert reg is not None
    assert reg.model_env_key == "CLAUDE_CODE_MODEL"
    assert reg.adapter_factory().name == "claude-code"


# ---------------------------------------------------------------------------
# Subprocess env forwarding — ANTHROPIC_ and CLAUDE_ prefixes must be safe
# ---------------------------------------------------------------------------


def test_anthropic_key_forwarded_via_build() -> None:
    """ANTHROPIC_API_KEY is forwarded explicitly by build(), not via the blanket prefix allowlist.

    This keeps Codex subprocesses from receiving Anthropic credentials.
    """
    with (
        patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "sk-forward-me",
                "ANTHROPIC_BASE_URL": "https://proxy.example.com",
            },
            clear=False,
        ),
        patch(
            "app.integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/claude"
        ),
    ):
        inv = ClaudeCodeAdapter().build(prompt="p", model=None, workspace="")

    assert inv.env is not None
    assert inv.env["ANTHROPIC_API_KEY"] == "sk-forward-me"
    assert inv.env["ANTHROPIC_BASE_URL"] == "https://proxy.example.com"


def test_anthropic_key_not_in_blanket_subprocess_env() -> None:
    """ANTHROPIC_API_KEY must NOT be forwarded via the global prefix allowlist (would leak to Codex)."""
    from app.integrations.llm_cli.runner import _build_subprocess_env

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-secret"}, clear=False):
        env = _build_subprocess_env(None)

    assert "ANTHROPIC_API_KEY" not in env


def test_claude_prefix_forwarded_to_subprocess() -> None:
    from app.integrations.llm_cli.runner import _build_subprocess_env

    with patch.dict(
        os.environ,
        {"CLAUDE_CODE_MODEL": "claude-opus-4-7", "CLAUDE_CODE_BIN": "/usr/bin/claude"},
        clear=False,
    ):
        env = _build_subprocess_env(None)

    assert env["CLAUDE_CODE_MODEL"] == "claude-opus-4-7"
    assert env["CLAUDE_CODE_BIN"] == "/usr/bin/claude"
