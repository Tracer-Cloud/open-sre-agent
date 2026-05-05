from __future__ import annotations

import pytest

from app.services import llm_client


class _FakeAnthropicMessages:
    def create(self, **_kwargs):
        raise AssertionError("unexpected network call in unit test")


class _FakeAnthropic:
    last_api_key: str | None = None

    def __init__(self, *, api_key: str, timeout: float) -> None:
        _FakeAnthropic.last_api_key = api_key
        self.timeout = timeout
        self.messages = _FakeAnthropicMessages()


class _FakeOpenAICompletions:
    def create(self, **_kwargs):
        raise AssertionError("unexpected network call in unit test")


class _FakeOpenAIChat:
    def __init__(self) -> None:
        self.completions = _FakeOpenAICompletions()


class _FakeOpenAI:
    last_api_key: str | None = None
    last_base_url: str | None = None
    last_default_headers: dict[str, str] | None = None
    init_api_keys: list[str] = []

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        timeout: float,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        _FakeOpenAI.last_api_key = api_key
        _FakeOpenAI.last_base_url = base_url
        _FakeOpenAI.last_default_headers = default_headers
        _FakeOpenAI.init_api_keys.append(api_key)
        self.base_url = base_url
        self.timeout = timeout
        self.default_headers = default_headers
        self.chat = _FakeOpenAIChat()


@pytest.fixture(autouse=True)
def _reset_fake_openai_state() -> None:
    _FakeOpenAI.last_api_key = None
    _FakeOpenAI.last_base_url = None
    _FakeOpenAI.last_default_headers = None
    _FakeOpenAI.init_api_keys = []


def test_openai_llm_client_defers_openai_until_ensure(monkeypatch) -> None:
    """Avoid constructing OpenAI in __init__: sdk 2.34+ rejects empty api_key."""
    monkeypatch.setattr(llm_client, "resolve_llm_api_key", lambda _env_var: "")
    monkeypatch.setattr(llm_client, "OpenAI", _FakeOpenAI)

    llm_client.OpenAILLMClient(model="gpt-4.1-mini")

    assert _FakeOpenAI.last_api_key is None
    assert _FakeOpenAI.init_api_keys == []


def test_openai_llm_client_reads_secure_local_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_client,
        "resolve_llm_api_key",
        lambda env_var: "stored-openai-key" if env_var == "OPENAI_API_KEY" else "",
    )
    monkeypatch.setattr(llm_client, "OpenAI", _FakeOpenAI)

    client = llm_client.OpenAILLMClient(model="gpt-5.4")
    client._ensure_client()

    assert _FakeOpenAI.last_api_key == "stored-openai-key"
    assert _FakeOpenAI.init_api_keys == ["stored-openai-key"]


def test_openai_llm_client_invoke_fails_when_key_missing(monkeypatch) -> None:
    monkeypatch.setattr(llm_client, "resolve_llm_api_key", lambda _env_var: "")
    client = llm_client.OpenAILLMClient(model="gpt-4.1-mini")

    with pytest.raises(RuntimeError, match="Missing OPENAI_API_KEY"):
        client.invoke("hello")


def test_openai_llm_client_rebuilds_client_when_key_rotates(monkeypatch) -> None:
    state = {"key": "first-key"}
    monkeypatch.setattr(llm_client, "resolve_llm_api_key", lambda _env_var: state["key"])
    monkeypatch.setattr(llm_client, "OpenAI", _FakeOpenAI)
    client = llm_client.OpenAILLMClient(model="gpt-4.1-mini")

    client._ensure_client()
    state["key"] = "second-key"
    client._ensure_client()

    assert _FakeOpenAI.init_api_keys == ["first-key", "second-key"]


def test_anthropic_llm_client_reads_secure_local_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_client,
        "resolve_llm_api_key",
        lambda env_var: "stored-anthropic-key" if env_var == "ANTHROPIC_API_KEY" else "",
    )
    monkeypatch.setattr(llm_client, "Anthropic", _FakeAnthropic)

    client = llm_client.LLMClient(model="claude-opus-4")
    client._ensure_client()

    assert _FakeAnthropic.last_api_key == "stored-anthropic-key"


def test_minimax_llm_client_reads_api_key_and_base_url(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_client,
        "resolve_llm_api_key",
        lambda env_var: "minimax-test-key" if env_var == "MINIMAX_API_KEY" else "",
    )
    monkeypatch.setattr(llm_client, "OpenAI", _FakeOpenAI)

    client = llm_client.OpenAILLMClient(
        model="MiniMax-M2.7",
        base_url="https://api.minimax.io/v1",
        api_key_env="MINIMAX_API_KEY",
        temperature=1.0,
    )
    client._ensure_client()

    assert _FakeOpenAI.last_api_key == "minimax-test-key"
    assert _FakeOpenAI.last_base_url == "https://api.minimax.io/v1"


def test_minimax_llm_client_temperature_is_set(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_client,
        "resolve_llm_api_key",
        lambda env_var: "minimax-test-key" if env_var == "MINIMAX_API_KEY" else "",
    )
    monkeypatch.setattr(llm_client, "OpenAI", _FakeOpenAI)

    client = llm_client.OpenAILLMClient(
        model="MiniMax-M2.7",
        base_url="https://api.minimax.io/v1",
        api_key_env="MINIMAX_API_KEY",
        temperature=1.0,
    )
    assert client._temperature == 1.0


def test_create_llm_client_claude_code_wires_cli_adapter(monkeypatch) -> None:
    """Investigation uses ``_create_llm_client`` → registry → ``CLIBackedLLMClient``."""
    monkeypatch.setenv("LLM_PROVIDER", "claude-code")
    monkeypatch.delenv("CLAUDE_CODE_MODEL", raising=False)
    llm_client.reset_llm_singletons()
    try:
        from app.integrations.llm_cli.claude_code import ClaudeCodeAdapter
        from app.integrations.llm_cli.runner import CLIBackedLLMClient

        client = llm_client._create_llm_client("reasoning")

        assert isinstance(client, CLIBackedLLMClient)
        assert isinstance(client._adapter, ClaudeCodeAdapter)
    finally:
        llm_client.reset_llm_singletons()


def test_create_llm_client_claude_code_reads_optional_model_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "claude-code")
    monkeypatch.setenv("CLAUDE_CODE_MODEL", "claude-opus-4-7")
    llm_client.reset_llm_singletons()
    try:
        client = llm_client._create_llm_client("reasoning")

        assert client._model == "claude-opus-4-7"
    finally:
        llm_client.reset_llm_singletons()
