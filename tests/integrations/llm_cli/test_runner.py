from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from integrations.llm_cli.runner import CLIBackedLLMClient


def _mock_probe() -> MagicMock:
    return MagicMock(installed=True, bin_path="/usr/bin/mock-cli", logged_in=True, detail="ok")


@patch("integrations.llm_cli.runner.subprocess.run")
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

    with patch("platform.guardrails.engine.get_guardrail_engine") as gr:
        gr.return_value.is_active = False
        with caplog.at_level(logging.DEBUG, logger="integrations.llm_cli.runner"):
            client = CLIBackedLLMClient(mock_adapter)
            client.invoke(prompt)

    spawn = next(record for record in caplog.records if record.msg == "cli_llm_spawn")
    assert spawn.provider == "copilot"
    assert spawn.argv == ["/usr/bin/mock-cli", "-p", "<redacted-prompt>", "--model", "gpt-5.1"]
    assert prompt not in spawn.argv


@patch("integrations.llm_cli.runner.subprocess.run")
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

    with patch("platform.guardrails.engine.get_guardrail_engine") as gr:
        gr.return_value.is_active = False
        with caplog.at_level(logging.DEBUG, logger="integrations.llm_cli.runner"):
            client = CLIBackedLLMClient(mock_adapter)
            client.invoke(prompt)

    spawn = next(record for record in caplog.records if record.msg == "cli_llm_spawn")
    assert spawn.provider == "claude-code"
    assert spawn.argv == ["/usr/bin/mock-cli", "-p", "--output-format", "text"]


@patch("integrations.llm_cli.runner.subprocess.run")
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

    with patch("platform.guardrails.engine.get_guardrail_engine") as gr:
        gr.return_value.is_active = False
        with caplog.at_level(logging.DEBUG, logger="integrations.llm_cli.runner"):
            client = CLIBackedLLMClient(mock_adapter)
            client.invoke(prompt)

    spawn = next(record for record in caplog.records if record.msg == "cli_llm_spawn")
    assert spawn.provider == "mock-cli"
    assert spawn.argv == ["/usr/bin/mock-cli", "--prompt=<redacted-prompt>", "--model", "gpt-5.1"]
    assert all(prompt not in arg for arg in spawn.argv)


@patch("integrations.llm_cli.runner.subprocess.run")
def test_runner_uses_adapter_explain_failure_for_quota(mock_run: MagicMock) -> None:
    """Quota stderr is enriched via adapter explain_failure, not a runner-side classifier."""
    import pytest

    from integrations.llm_cli.failure_explain import explain_cli_failure

    mock_adapter = MagicMock()
    mock_adapter.name = "codex"
    mock_adapter.auth_hint = "codex login"
    mock_adapter.detect.return_value = _mock_probe()
    mock_adapter.build.return_value = MagicMock(
        argv=("/usr/bin/codex", "exec", "-"),
        stdin="hello",
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.explain_failure.side_effect = lambda **kwargs: explain_cli_failure(
        exit_label="codex exec", **kwargs
    )
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="429 Too Many Requests: quota exceeded"
    )

    with patch("platform.guardrails.engine.get_guardrail_engine") as gr:
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter)
        with pytest.raises(RuntimeError, match="quota or rate limit exceeded"):
            client.invoke("hello")
