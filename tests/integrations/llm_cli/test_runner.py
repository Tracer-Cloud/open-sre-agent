from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from app.integrations.llm_cli.runner import CLIBackedLLMClient


def _mock_probe() -> MagicMock:
    return MagicMock(installed=True, bin_path="/usr/bin/mock-cli", logged_in=True, detail="ok")


@patch("app.integrations.llm_cli.runner.subprocess.run")
def test_cli_llm_spawn_log_redacts_prompt_in_argv(mock_run: MagicMock, caplog) -> None:
    prompt = "customer secret investigation context"
    mock_adapter = MagicMock()
    mock_adapter.name = "copilot"
    mock_adapter.detect.return_value = _mock_probe()
    mock_adapter.build.return_value = MagicMock(
        argv=("/usr/bin/mock-cli", "-p", prompt, "--model", "gpt-5.1"),
        stdin=None,
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.parse.return_value = "answer"
    mock_run.return_value = MagicMock(returncode=0, stdout="answer\n", stderr="")

    with patch("app.guardrails.engine.get_guardrail_engine") as gr:
        gr.return_value.is_active = False
        with caplog.at_level(logging.DEBUG, logger="app.integrations.llm_cli.runner"):
            client = CLIBackedLLMClient(mock_adapter)
            client.invoke(prompt)

    spawn = next(record for record in caplog.records if record.msg == "cli_llm_spawn")
    assert spawn.provider == "copilot"
    assert spawn.argv == ["/usr/bin/mock-cli", "-p", "<redacted-prompt>", "--model", "gpt-5.1"]
    assert prompt not in spawn.argv


@patch("app.integrations.llm_cli.runner.subprocess.run")
def test_cli_llm_spawn_log_keeps_non_prompt_argv_args(mock_run: MagicMock, caplog) -> None:
    prompt = "customer secret investigation context"
    mock_adapter = MagicMock()
    mock_adapter.name = "claude-code"
    mock_adapter.detect.return_value = _mock_probe()
    mock_adapter.build.return_value = MagicMock(
        argv=("/usr/bin/mock-cli", "-p", "--output-format", "text"),
        stdin=prompt,
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.parse.return_value = "answer"
    mock_run.return_value = MagicMock(returncode=0, stdout="answer\n", stderr="")

    with patch("app.guardrails.engine.get_guardrail_engine") as gr:
        gr.return_value.is_active = False
        with caplog.at_level(logging.DEBUG, logger="app.integrations.llm_cli.runner"):
            client = CLIBackedLLMClient(mock_adapter)
            client.invoke(prompt)

    spawn = next(record for record in caplog.records if record.msg == "cli_llm_spawn")
    assert spawn.provider == "claude-code"
    assert spawn.argv == ["/usr/bin/mock-cli", "-p", "--output-format", "text"]


@patch("app.integrations.llm_cli.runner.subprocess.run")
def test_cli_llm_spawn_log_redacts_prompt_equals_form(mock_run: MagicMock, caplog) -> None:
    prompt = "customer secret investigation context"
    mock_adapter = MagicMock()
    mock_adapter.name = "mock-cli"
    mock_adapter.detect.return_value = _mock_probe()
    mock_adapter.build.return_value = MagicMock(
        argv=("/usr/bin/mock-cli", f"--prompt={prompt}", "--model", "gpt-5.1"),
        stdin=None,
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.parse.return_value = "answer"
    mock_run.return_value = MagicMock(returncode=0, stdout="answer\n", stderr="")

    with patch("app.guardrails.engine.get_guardrail_engine") as gr:
        gr.return_value.is_active = False
        with caplog.at_level(logging.DEBUG, logger="app.integrations.llm_cli.runner"):
            client = CLIBackedLLMClient(mock_adapter)
            client.invoke(prompt)

    spawn = next(record for record in caplog.records if record.msg == "cli_llm_spawn")
    assert spawn.provider == "mock-cli"
    assert spawn.argv == ["/usr/bin/mock-cli", "--prompt=<redacted-prompt>", "--model", "gpt-5.1"]
    assert all(prompt not in arg for arg in spawn.argv)


# ── failure_classifier enrichment ──────────────────────────────────────────────


@patch("app.integrations.llm_cli.runner.subprocess.run")
def test_runner_enriches_quota_error_with_actionable_message(mock_run: MagicMock) -> None:
    """exit 1 with quota-related stderr → readable message replaces raw exit-code text."""
    import pytest

    mock_adapter = MagicMock()
    mock_adapter.name = "codex"
    mock_adapter.detect.return_value = _mock_probe()
    mock_adapter.build.return_value = MagicMock(
        argv=("/usr/bin/codex", "exec", "-"),
        stdin="hello",
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.explain_failure.return_value = "codex exec exited with code 1. some quota error"
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="429 Too Many Requests: quota exceeded"
    )

    with patch("app.guardrails.engine.get_guardrail_engine") as gr:
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter)
        with pytest.raises(RuntimeError, match="quota or rate limit exceeded"):
            client.invoke("hello")


@patch("app.integrations.llm_cli.runner.subprocess.run")
def test_runner_enriches_silent_exit_with_version_banner_only(mock_run: MagicMock) -> None:
    """exit 1 with only a version banner (no error keywords) → quota/auth fallback hint."""
    import pytest

    mock_adapter = MagicMock()
    mock_adapter.name = "codex"
    mock_adapter.detect.return_value = _mock_probe()
    mock_adapter.build.return_value = MagicMock(
        argv=("/usr/bin/codex", "exec", "-"),
        stdin="hello",
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.explain_failure.return_value = (
        "codex exec exited with code 1. OpenAI Codex v0.134.0"
    )
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="OpenAI Codex v0.134.0")

    with patch("app.guardrails.engine.get_guardrail_engine") as gr:
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter)
        with pytest.raises(RuntimeError, match="quota exhausted or expired auth"):
            client.invoke("hello")
