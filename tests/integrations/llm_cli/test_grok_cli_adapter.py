"""Tests for the xAI Grok Build CLI adapter (detect / build / failure / env forwarding)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from integrations.llm_cli.binary_resolver import npm_prefix_bin_dirs
from integrations.llm_cli.grok_cli import (
    GrokCLIAdapter,
    _classify_grok_auth_from_probe,
    _fallback_grok_paths,
    _has_explicit_grok_auth_env,
    parse_grok_models_output,
)
from tests.integrations.llm_cli.testing_helpers import write_fake_runnable_cli_bin

_SUBPROCESS_RUN = "integrations.llm_cli.grok_cli.subprocess.run"
_WHICH = "integrations.llm_cli.binary_resolver.shutil.which"


def _posix_path_set(paths: list[str]) -> set[str]:
    return {Path(p).as_posix() for p in paths}


def _version_proc(version: str = "0.1.0") -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = f"grok {version}\n"
    m.stderr = ""
    return m


def _auth_proc(returncode: int = 0, stdout: str = "ok", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# Auth classification from probe output
# ---------------------------------------------------------------------------


def test_classify_auth_logged_in_string() -> None:
    logged_in, detail = _classify_grok_auth_from_probe(
        0, "You are logged in with grok.com.\n\nDefault model: grok-build\n", ""
    )
    assert logged_in is True
    assert "authenticated" in detail.lower()


def test_classify_auth_exit0_is_authenticated() -> None:
    logged_in, detail = _classify_grok_auth_from_probe(0, "some other output", "")
    assert logged_in is True
    assert "authenticated" in detail.lower()


def test_classify_auth_unauthorized_string() -> None:
    logged_in, detail = _classify_grok_auth_from_probe(1, "", "Error: Unauthorized")
    assert logged_in is False
    assert "grok login" in detail.lower() or "xai_api_key" in detail.lower()


def test_classify_auth_401_in_output() -> None:
    logged_in, detail = _classify_grok_auth_from_probe(1, "", "HTTP 401 Unauthorized")
    assert logged_in is False


def test_classify_auth_not_logged_in_string() -> None:
    logged_in, detail = _classify_grok_auth_from_probe(1, "not logged in", "")
    assert logged_in is False


def test_classify_auth_network_error_is_unclear() -> None:
    logged_in, detail = _classify_grok_auth_from_probe(1, "", "network unreachable")
    assert logged_in is None
    assert "network" in detail.lower()


def test_classify_auth_unknown_nonzero_is_unclear() -> None:
    logged_in, detail = _classify_grok_auth_from_probe(2, "", "something weird")
    assert logged_in is None
    assert "unclear" in detail.lower()


def test_classify_auth_api_key_invalid() -> None:
    logged_in, detail = _classify_grok_auth_from_probe(1, "", "api key is invalid")
    assert logged_in is False


# ---------------------------------------------------------------------------
# _has_explicit_grok_auth_env
# ---------------------------------------------------------------------------


def test_has_explicit_auth_env_with_key() -> None:
    with patch.dict(os.environ, {"XAI_API_KEY": "xai-test"}, clear=False):
        assert _has_explicit_grok_auth_env() == "XAI_API_KEY"


def test_has_explicit_auth_env_empty_key() -> None:
    with patch.dict(os.environ, {"XAI_API_KEY": ""}, clear=False):
        assert _has_explicit_grok_auth_env() is None


def test_has_explicit_auth_env_missing_key() -> None:
    env = {k: v for k, v in os.environ.items() if k != "XAI_API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        assert _has_explicit_grok_auth_env() is None


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------


@patch(_SUBPROCESS_RUN)
@patch(_WHICH)
def test_detect_logged_in_via_probe(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/grok"
    mock_run.side_effect = [_version_proc(), _auth_proc(returncode=0, stdout="ok")]

    with patch.dict(os.environ, {"XAI_API_KEY": "", "GROK_CLI_BIN": ""}, clear=False):
        probe = GrokCLIAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert probe.bin_path == "/usr/bin/grok"
    assert probe.version == "0.1.0"


@patch(_SUBPROCESS_RUN)
@patch(_WHICH)
def test_detect_logged_in_via_api_key_fallback(mock_which: MagicMock, mock_run: MagicMock) -> None:
    """XAI_API_KEY promotes to authenticated even when the probe result is unclear."""
    mock_which.return_value = "/usr/bin/grok"
    mock_run.side_effect = [_version_proc(), _auth_proc(returncode=1, stderr="network unreachable")]

    with patch.dict(os.environ, {"XAI_API_KEY": "xai-test", "GROK_CLI_BIN": ""}, clear=False):
        probe = GrokCLIAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert "XAI_API_KEY" in probe.detail


@patch(_SUBPROCESS_RUN)
@patch(_WHICH)
def test_detect_not_authenticated(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/grok"
    mock_run.side_effect = [_version_proc(), _auth_proc(returncode=1, stderr="Error: Unauthorized")]

    with patch.dict(os.environ, {"XAI_API_KEY": "", "GROK_CLI_BIN": ""}, clear=False):
        probe = GrokCLIAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is False


@patch(_SUBPROCESS_RUN)
@patch(_WHICH)
def test_detect_api_key_does_not_override_explicit_false(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    """XAI_API_KEY must not promote logged_in when the probe explicitly returned False.

    The key itself may be what was rejected; masking that defers the failure
    to invocation time with a confusing 300s timeout instead of a clear error.
    """
    mock_which.return_value = "/usr/bin/grok"
    mock_run.side_effect = [_version_proc(), _auth_proc(returncode=1, stderr="Error: Unauthorized")]

    with patch.dict(os.environ, {"XAI_API_KEY": "xai-invalid", "GROK_CLI_BIN": ""}, clear=False):
        probe = GrokCLIAdapter().detect()

    assert probe.logged_in is False


@patch(_SUBPROCESS_RUN)
@patch(_WHICH)
def test_detect_auth_unclear_on_network_error(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/grok"
    mock_run.side_effect = [_version_proc(), _auth_proc(returncode=1, stderr="connection refused")]

    with patch.dict(os.environ, {"XAI_API_KEY": "", "GROK_CLI_BIN": ""}, clear=False):
        probe = GrokCLIAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is None


@patch(_SUBPROCESS_RUN)
@patch(_WHICH)
def test_detect_auth_probe_timeout_is_unclear(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/grok"
    mock_run.side_effect = [
        _version_proc(),
        subprocess.TimeoutExpired(cmd=["/usr/bin/grok", "models"], timeout=10.0),
    ]

    with patch.dict(os.environ, {"XAI_API_KEY": "", "GROK_CLI_BIN": ""}, clear=False):
        probe = GrokCLIAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is None
    assert "timed out" in probe.detail.lower()


@patch(_SUBPROCESS_RUN)
@patch(_WHICH)
def test_detect_api_key_fallback_overrides_probe_timeout(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    mock_which.return_value = "/usr/bin/grok"
    mock_run.side_effect = [
        _version_proc(),
        subprocess.TimeoutExpired(cmd=["/usr/bin/grok", "models"], timeout=10.0),
    ]

    with patch.dict(os.environ, {"XAI_API_KEY": "xai-key", "GROK_CLI_BIN": ""}, clear=False):
        probe = GrokCLIAdapter().detect()

    assert probe.logged_in is True
    assert "XAI_API_KEY" in probe.detail


@patch("integrations.llm_cli.grok_cli._fallback_grok_paths", return_value=[])
@patch(_WHICH, return_value=None)
def test_detect_not_installed(_mock_which: MagicMock, _mock_fallback: MagicMock) -> None:
    with patch.dict(os.environ, {"GROK_CLI_BIN": ""}, clear=False):
        probe = GrokCLIAdapter().detect()
    assert probe.installed is False
    assert probe.logged_in is None
    assert probe.bin_path is None
    assert "not found" in probe.detail.lower()


@patch(_SUBPROCESS_RUN)
@patch(_WHICH)
def test_detect_version_command_fails(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/grok"
    m = MagicMock()
    m.returncode = 1
    m.stdout = ""
    m.stderr = "some error\n"
    mock_run.return_value = m

    with patch.dict(os.environ, {"GROK_CLI_BIN": ""}, clear=False):
        probe = GrokCLIAdapter().detect()

    assert probe.installed is False
    assert probe.logged_in is None


@patch(_SUBPROCESS_RUN)
@patch(_WHICH)
def test_detect_version_timeout(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/grok"
    mock_run.side_effect = subprocess.TimeoutExpired(
        cmd=["/usr/bin/grok", "--version"], timeout=5.0
    )

    with patch.dict(os.environ, {"GROK_CLI_BIN": ""}, clear=False):
        probe = GrokCLIAdapter().detect()

    assert probe.installed is False
    assert probe.logged_in is None
    assert "--version" in probe.detail


# ---------------------------------------------------------------------------
# build()
# ---------------------------------------------------------------------------


@patch(_WHICH, return_value="/usr/bin/grok")
def test_build_basic_invocation(_mock_which: MagicMock) -> None:
    with patch.dict(os.environ, {"GROK_CLI_BIN": ""}, clear=False):
        inv = GrokCLIAdapter().build(prompt="explain this alert", model=None, workspace="")
    assert inv.argv[0] == "/usr/bin/grok"
    assert "-p" in inv.argv
    assert "explain this alert" in inv.argv
    assert "--output-format" in inv.argv
    assert "plain" in inv.argv
    assert "--no-auto-update" not in inv.argv
    # Prompt is delivered as the -p argument, not stdin.
    assert inv.stdin is None
    assert inv.timeout_sec == 300.0


@patch(_WHICH, return_value="/usr/bin/grok")
def test_build_never_auto_approves(_mock_which: MagicMock) -> None:
    """OpenSRE uses Grok as a text responder; it must not auto-run Grok's own tools."""
    with patch.dict(os.environ, {"GROK_CLI_BIN": ""}, clear=False):
        inv = GrokCLIAdapter().build(prompt="p", model=None, workspace="")
    assert "--always-approve" not in inv.argv


@patch(_WHICH, return_value="/usr/bin/grok")
def test_build_adds_model_flag(_mock_which: MagicMock) -> None:
    with patch.dict(os.environ, {"GROK_CLI_BIN": ""}, clear=False):
        inv = GrokCLIAdapter().build(prompt="p", model="grok-build-0.1", workspace="")
    assert "-m" in inv.argv
    idx = inv.argv.index("-m")
    assert inv.argv[idx + 1] == "grok-build-0.1"


@patch(_WHICH, return_value="/usr/bin/grok")
def test_build_omits_model_flag_when_empty(_mock_which: MagicMock) -> None:
    with patch.dict(os.environ, {"GROK_CLI_BIN": ""}, clear=False):
        inv = GrokCLIAdapter().build(prompt="p", model="", workspace="")
    assert "-m" not in inv.argv


@patch(_WHICH, return_value="/usr/bin/grok")
def test_build_uses_provided_workspace(_mock_which: MagicMock) -> None:
    workspace = "/my/project"
    with patch.dict(os.environ, {"GROK_CLI_BIN": ""}, clear=False):
        inv = GrokCLIAdapter().build(prompt="p", model=None, workspace=workspace)
    assert Path(inv.cwd) == Path(workspace)


@patch(_WHICH, return_value="/usr/bin/grok")
def test_build_sets_no_color_env(_mock_which: MagicMock) -> None:
    with patch.dict(os.environ, {"GROK_CLI_BIN": ""}, clear=False):
        inv = GrokCLIAdapter().build(prompt="p", model=None, workspace="")
    assert inv.env is not None
    assert inv.env.get("NO_COLOR") == "1"


@patch("integrations.llm_cli.grok_cli._fallback_grok_paths", return_value=[])
@patch(_WHICH, return_value=None)
def test_build_raises_when_binary_not_found(
    _mock_which: MagicMock, _mock_fallback: MagicMock
) -> None:
    with (
        patch.dict(os.environ, {"GROK_CLI_BIN": ""}, clear=False),
        pytest.raises(RuntimeError, match="Grok Build CLI not found"),
    ):
        GrokCLIAdapter().build(prompt="p", model=None, workspace="")


# ---------------------------------------------------------------------------
# parse / explain_failure
# ---------------------------------------------------------------------------


def test_parse_returns_stripped_stdout() -> None:
    result = GrokCLIAdapter().parse(stdout="  hello world  \n", stderr="", returncode=0)
    assert result == "hello world"


def test_parse_raises_on_empty_stdout() -> None:
    """Empty stdout on exit 0 must raise rather than return a silent blank response."""
    with pytest.raises(RuntimeError, match="empty output"):
        GrokCLIAdapter().parse(stdout="", stderr="", returncode=0)


def test_parse_raises_on_whitespace_only_stdout() -> None:
    with pytest.raises(RuntimeError, match="empty output"):
        GrokCLIAdapter().parse(stdout="   \n  ", stderr="", returncode=0)


def test_parse_surfaces_stderr_via_explain_failure() -> None:
    """When stdout is empty, stderr is surfaced through explain_failure in the error message."""
    with pytest.raises(RuntimeError) as exc_info:
        GrokCLIAdapter().parse(stdout="", stderr="401 Unauthorized", returncode=1)
    assert "401" in str(exc_info.value) or "auth" in str(exc_info.value).lower()


def test_explain_failure_includes_returncode_and_stderr() -> None:
    msg = GrokCLIAdapter().explain_failure(stdout="", stderr="boom", returncode=1)
    assert "1" in msg
    assert "boom" in msg


def test_explain_failure_maps_auth_errors() -> None:
    msg = GrokCLIAdapter().explain_failure(stdout="", stderr="401 Unauthorized", returncode=1)
    assert "grok login" in msg.lower() or "xai_api_key" in msg.lower()


def test_explain_failure_falls_back_to_stdout() -> None:
    msg = GrokCLIAdapter().explain_failure(stdout="some output", stderr="", returncode=2)
    assert "some output" in msg


def test_explain_failure_maps_quota_via_shared_helper() -> None:
    """Quota/rate-limit errors get an actionable hint from the shared classifier."""
    msg = GrokCLIAdapter().explain_failure(
        stdout="", stderr="Error 429: rate limit exceeded", returncode=1
    )
    assert "quota or rate limit" in msg.lower()


def test_auth_hint_mentions_login_and_api_key() -> None:
    adapter = GrokCLIAdapter()
    assert "grok login" in adapter.auth_hint
    assert "XAI_API_KEY" in adapter.auth_hint


# ---------------------------------------------------------------------------
# GROK_CLI_BIN env override
# ---------------------------------------------------------------------------


@patch(_SUBPROCESS_RUN)
def test_detect_uses_grok_cli_bin_env(mock_run: MagicMock, tmp_path: Path) -> None:
    fake_bin = write_fake_runnable_cli_bin(tmp_path, "my-grok")
    mock_run.side_effect = [_version_proc(), _auth_proc(returncode=0, stdout="ok")]

    with patch.dict(
        os.environ,
        {"GROK_CLI_BIN": str(fake_bin), "XAI_API_KEY": ""},
        clear=False,
    ):
        probe = GrokCLIAdapter().detect()

    assert probe.bin_path == str(fake_bin)
    assert probe.installed is True


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_grok_cli_registry_entry() -> None:
    from integrations.llm_cli.registry import get_cli_provider_registration

    reg = get_cli_provider_registration("grok-cli")
    assert reg is not None
    assert reg.model_env_key == "GROK_CLI_MODEL"
    assert reg.adapter_factory().name == "grok-cli"


# ---------------------------------------------------------------------------
# Subprocess env forwarding — XAI_API_KEY must be scoped to the Grok subprocess
# ---------------------------------------------------------------------------


def test_xai_key_forwarded_via_build() -> None:
    """XAI_API_KEY is forwarded explicitly by build(), not via the blanket prefix allowlist."""
    with (
        patch.dict(
            os.environ,
            {
                "XAI_API_KEY": "xai-forward-me",
                "XAI_BASE_URL": "https://proxy.example.com",
                "GROK_CLI_BIN": "",
            },
            clear=False,
        ),
        patch(_WHICH, return_value="/usr/bin/grok"),
    ):
        inv = GrokCLIAdapter().build(prompt="p", model=None, workspace="")

    assert inv.env is not None
    assert inv.env["XAI_API_KEY"] == "xai-forward-me"
    assert inv.env["XAI_BASE_URL"] == "https://proxy.example.com"


def test_xai_key_not_in_blanket_subprocess_env() -> None:
    """XAI_API_KEY must NOT be forwarded via the global prefix allowlist (would leak to others)."""
    from integrations.llm_cli.subprocess_env import build_cli_subprocess_env

    with patch.dict(os.environ, {"XAI_API_KEY": "xai-secret"}, clear=False):
        env = build_cli_subprocess_env(None)

    assert "XAI_API_KEY" not in env


# ---------------------------------------------------------------------------
# Fallback paths
# ---------------------------------------------------------------------------


def test_fallback_paths_linux() -> None:
    npm_prefix_bin_dirs.cache_clear()
    with (
        patch("integrations.llm_cli.binary_resolver.sys.platform", "linux"),
        patch.dict(os.environ, {"npm_config_prefix": "/custom/npm"}, clear=False),
    ):
        paths = _fallback_grok_paths()

    normalized = _posix_path_set(paths)
    assert "/custom/npm/bin/grok" in normalized


# ---------------------------------------------------------------------------
# parse_grok_models_output
# ---------------------------------------------------------------------------


def test_parse_models_output_typical() -> None:
    text = (
        "You are logged in with grok.com.\n\n"
        "Default model: grok-build\n\n"
        "Available models:\n"
        "  - grok-composer-2.5-fast\n"
        "  * grok-build (default)\n"
    )
    assert parse_grok_models_output(text) == ["grok-composer-2.5-fast", "grok-build"]


def test_parse_models_output_empty_string() -> None:
    assert parse_grok_models_output("") == []


def test_parse_models_output_no_available_models_section() -> None:
    assert parse_grok_models_output("You are logged in.\n\nDefault model: grok-build\n") == []


def test_parse_models_output_stops_at_non_list_line() -> None:
    text = (
        "Available models:\n"
        "  - grok-fast\n"
        "  - grok-slow\n"
        "\n"
        "Some trailing info that is not a model.\n"
        "  - grok-phantom\n"
    )
    # Blank line terminates the section; grok-phantom must not be included.
    assert parse_grok_models_output(text) == ["grok-fast", "grok-slow"]


def test_parse_models_output_strips_parenthetical_annotation() -> None:
    text = "Available models:\n  * grok-build (default)\n"
    assert parse_grok_models_output(text) == ["grok-build"]


def test_parse_models_output_model_id_with_parenthesis_in_name() -> None:
    # A model whose ID itself contains '(' should be truncated at the first '('
    # per the current split("(")[0] behaviour — this test documents that contract.
    text = "Available models:\n  - grok-weird(beta)\n"
    assert parse_grok_models_output(text) == ["grok-weird"]


def test_parse_models_output_section_header_case_insensitive() -> None:
    text = "AVAILABLE MODELS:\n  - grok-x\n"
    assert parse_grok_models_output(text) == ["grok-x"]
