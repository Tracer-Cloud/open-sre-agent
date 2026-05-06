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


class _InactiveGuardrailEngine:
    is_active = False

    def apply(self, content: str) -> str:
        return content


class _RecordingBedrockRuntime:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.converse_calls: list[dict] = []

    def converse(self, **kwargs) -> dict:
        self.converse_calls.append(kwargs)
        return self.response


def test_is_anthropic_bedrock_model_claude_ids() -> None:
    assert llm_client._is_anthropic_bedrock_model("anthropic.claude-3-haiku-20240307-v1:0")
    assert llm_client._is_anthropic_bedrock_model(
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )


def test_is_anthropic_bedrock_model_foundation_model_arn() -> None:
    arn = "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-sonnet-20240229-v1:0"
    assert llm_client._is_anthropic_bedrock_model(arn)


def test_is_anthropic_bedrock_model_non_anthropic() -> None:
    assert not llm_client._is_anthropic_bedrock_model(
        "mistral.mistral-large-2402-v1:0",
    )


def test_is_anthropic_bedrock_model_application_inference_profile_arn() -> None:
    profile_arn = (
        "arn:aws:bedrock:us-east-2:012345678901:application-inference-profile/a1b2c3profile"
    )
    assert not llm_client._is_anthropic_bedrock_model(profile_arn)


def test_bedrock_client_routes_mistral_to_converse(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.guardrails.engine.get_guardrail_engine",
        _InactiveGuardrailEngine,
    )
    runtime = _RecordingBedrockRuntime(
        {"output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}}},
    )
    monkeypatch.setattr(llm_client.boto3, "client", lambda *_a, **_k: runtime)

    client = llm_client.BedrockLLMClient(model="mistral.mistral-large-2402-v1:0")
    assert client._use_anthropic is False

    resp = client.invoke([{"role": "user", "content": "hi"}])
    assert resp.content == "ok"
    assert len(runtime.converse_calls) == 1
    call = runtime.converse_calls[0]
    assert call["modelId"] == "mistral.mistral-large-2402-v1:0"
    assert call["messages"] == [
        {"role": "user", "content": [{"text": "hi"}]},
    ]
    assert "system" not in call


def test_invoke_converse_includes_optional_system_temperature(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.guardrails.engine.get_guardrail_engine",
        _InactiveGuardrailEngine,
    )
    runtime = _RecordingBedrockRuntime(
        {"output": {"message": {"role": "assistant", "content": [{"text": ""}, {"text": "x"}]}}},
    )
    monkeypatch.setattr(llm_client.boto3, "client", lambda *_a, **_k: runtime)

    client = llm_client.BedrockLLMClient(model="mistral.mini", temperature=0.4)
    client.invoke(
        [
            {"role": "system", "content": "context"},
            {"role": "user", "content": "q"},
        ],
    )

    kwargs = runtime.converse_calls[0]
    assert kwargs["system"] == [{"text": "context"}]
    assert kwargs["inferenceConfig"]["temperature"] == 0.4


def test_invoke_converse_raises_when_no_text_blocks(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.guardrails.engine.get_guardrail_engine",
        _InactiveGuardrailEngine,
    )
    runtime = _RecordingBedrockRuntime(
        {
            "stopReason": "tool_use",
            "output": {"message": {"role": "assistant", "content": [{"toolUse": {"name": "x"}}]}},
        },
    )
    monkeypatch.setattr(llm_client.boto3, "client", lambda *_a, **_k: runtime)

    client = llm_client.BedrockLLMClient(model="mistral.mini")
    with pytest.raises(RuntimeError, match="no text content"):
        client.invoke("hello")


def test_bedrock_application_inference_profile_arn_uses_converse(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.guardrails.engine.get_guardrail_engine",
        _InactiveGuardrailEngine,
    )
    runtime = _RecordingBedrockRuntime(
        {"output": {"message": {"role": "assistant", "content": [{"text": "via-converse"}]}}},
    )
    monkeypatch.setattr(llm_client.boto3, "client", lambda *_a, **_k: runtime)

    arn = "arn:aws:bedrock:us-west-2:123:application-inference-profile/p2"
    client = llm_client.BedrockLLMClient(model=arn)

    assert client._use_anthropic is False
    assert client.invoke("hi").content == "via-converse"


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


# ---------------------------------------------------------------------------
# LLMClient.invoke / invoke_stream — kwargs builder + streaming behavior
# ---------------------------------------------------------------------------


def _make_capturing_anthropic(
    *,
    response_text: str = "",
    chunks: list[str] | None = None,
):
    """Build a fake Anthropic class that captures kwargs and returns canned data.

    Distinct from the module-level ``_FakeAnthropic`` (which raises on any
    API call to guard ``_ensure_client`` tests). Closure-scoped so each test
    gets a fresh capture dict — no class-level state to reset between tests.
    """
    state: dict = {"kwargs": None}
    stream_chunks = list(chunks or [])

    class _Block:
        def __init__(self, text: str) -> None:
            self.type = "text"
            self.text = text

    class _Response:
        def __init__(self, text: str) -> None:
            self.content = [_Block(text)]

    class _StreamCM:
        def __init__(self) -> None:
            self.text_stream = iter(stream_chunks)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Messages:
        def create(self, **kwargs):
            state["kwargs"] = kwargs
            return _Response(response_text)

        def stream(self, **kwargs):
            state["kwargs"] = kwargs
            return _StreamCM()

    class _Anthropic:
        def __init__(self, *, api_key: str, timeout: float) -> None:
            self.api_key = api_key
            self.timeout = timeout
            self.messages = _Messages()

    return _Anthropic, state


def test_anthropic_invoke_forwards_built_kwargs_to_messages_create(monkeypatch) -> None:
    """Refactored invoke() still sends model, max_tokens, and messages to the SDK."""
    fake, captured = _make_capturing_anthropic(response_text="hello")
    monkeypatch.setattr(llm_client, "resolve_llm_api_key", lambda _env: "k")
    monkeypatch.setattr(llm_client, "Anthropic", fake)

    client = llm_client.LLMClient(model="claude-test", max_tokens=64)
    response = client.invoke("hi")

    assert response.content == "hello"
    assert captured["kwargs"]["model"] == "claude-test"
    assert captured["kwargs"]["max_tokens"] == 64
    assert captured["kwargs"]["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_invoke_stream_yields_text_stream_chunks(monkeypatch) -> None:
    """invoke_stream() routes through the same builder and yields SDK chunks in order."""
    fake, captured = _make_capturing_anthropic(chunks=["Hel", "lo, ", "world"])
    monkeypatch.setattr(llm_client, "resolve_llm_api_key", lambda _env: "k")
    monkeypatch.setattr(llm_client, "Anthropic", fake)

    client = llm_client.LLMClient(model="claude-test", max_tokens=64)
    chunks = list(client.invoke_stream("hi"))

    assert chunks == ["Hel", "lo, ", "world"]
    assert captured["kwargs"]["model"] == "claude-test"
    assert captured["kwargs"]["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_invoke_stream_applies_guardrails_to_input(monkeypatch) -> None:
    """The shared kwargs builder runs guardrail redaction before the stream opens."""
    fake, captured = _make_capturing_anthropic(chunks=["ok"])
    monkeypatch.setattr(llm_client, "resolve_llm_api_key", lambda _env: "k")
    monkeypatch.setattr(llm_client, "Anthropic", fake)

    class _RedactingEngine:
        is_active = True

        def apply(self, content: str) -> str:
            return content.replace("secret", "[REDACTED]")

    import app.guardrails.engine as engine_module

    monkeypatch.setattr(engine_module, "get_guardrail_engine", lambda: _RedactingEngine())

    client = llm_client.LLMClient(model="claude-test")
    list(client.invoke_stream("share my secret"))

    assert captured["kwargs"]["messages"][0]["content"] == "share my [REDACTED]"


def test_anthropic_invoke_stream_retries_when_no_chunk_emitted(monkeypatch) -> None:
    """Transient failure before any chunk yields → retry succeeds, caller sees recovered text."""
    attempts: list[bool] = []

    class _SuccessStream:
        def __init__(self) -> None:
            self.text_stream = iter(["recovered"])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Messages:
        def stream(self, **_kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise RuntimeError("Overloaded")
            return _SuccessStream()

    class _Anthropic:
        def __init__(self, **_kwargs) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(llm_client, "resolve_llm_api_key", lambda _env: "k")
    monkeypatch.setattr(llm_client, "Anthropic", _Anthropic)
    # Skip the real backoff sleep so the test is fast.
    monkeypatch.setattr(llm_client.time, "sleep", lambda _seconds: None)

    client = llm_client.LLMClient(model="claude-test")
    chunks = list(client.invoke_stream("hi"))

    assert chunks == ["recovered"]
    assert len(attempts) == 2  # First call raised; retry succeeded


def test_anthropic_invoke_stream_does_not_retry_after_yielding(monkeypatch) -> None:
    """Mid-stream failure must propagate; retrying would duplicate visible output."""
    attempts: list[bool] = []

    def _yield_then_raise():
        yield "partial"
        raise RuntimeError("connection dropped")

    class _Stream:
        def __init__(self) -> None:
            self.text_stream = _yield_then_raise()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Messages:
        def stream(self, **_kwargs):
            attempts.append(True)
            return _Stream()

    class _Anthropic:
        def __init__(self, **_kwargs) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(llm_client, "resolve_llm_api_key", lambda _env: "k")
    monkeypatch.setattr(llm_client, "Anthropic", _Anthropic)
    monkeypatch.setattr(llm_client.time, "sleep", lambda _seconds: None)

    client = llm_client.LLMClient(model="claude-test")
    iterator = client.invoke_stream("hi")

    # First chunk reaches the caller — visible on the user's screen.
    assert next(iterator) == "partial"

    raised = False
    try:
        next(iterator)
    except RuntimeError as exc:
        raised = True
        assert "connection dropped" in str(exc)

    assert raised, "RuntimeError should have propagated"
    assert len(attempts) == 1, "Must not retry after emitting any chunk"


# ---------------------------------------------------------------------------
# OpenAILLMClient.invoke / invoke_stream — kwargs builder + streaming behavior
# ---------------------------------------------------------------------------


def _make_capturing_openai(
    *,
    response_text: str = "",
    chunk_contents: list[str | None] | None = None,
):
    """Build a fake OpenAI class that captures kwargs and returns canned data.

    ``chunk_contents`` accepts ``None`` entries to simulate empty deltas the
    real SDK emits during keep-alive — invoke_stream must skip those.
    Closure-scoped so each test gets a fresh capture dict.
    """
    state: dict = {"kwargs": None}
    raw_chunks = list(chunk_contents or [])

    class _Delta:
        def __init__(self, content: str | None) -> None:
            self.content = content

    class _Choice:
        def __init__(self, *, delta_content: str | None = None, message_content: str = "") -> None:
            self.delta = _Delta(delta_content)
            self.message = type("_Msg", (), {"content": message_content})()

    class _Response:
        def __init__(self, message_content: str) -> None:
            self.choices = [_Choice(message_content=message_content)]

    class _StreamChunk:
        def __init__(self, content: str | None) -> None:
            self.choices = [_Choice(delta_content=content)] if content is not None else []

    class _Completions:
        def create(self, **kwargs):
            state["kwargs"] = kwargs
            if kwargs.get("stream"):
                return iter(_StreamChunk(c) for c in raw_chunks)
            return _Response(response_text)

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(
            self,
            *,
            api_key: str,
            base_url: str | None = None,
            timeout: float,
            default_headers: dict[str, str] | None = None,
        ) -> None:
            self.api_key = api_key
            self.base_url = base_url
            self.timeout = timeout
            self.default_headers = default_headers
            self.chat = _Chat()

    return _OpenAI, state


def test_openai_invoke_forwards_built_kwargs_to_chat_completions_create(monkeypatch) -> None:
    """Refactored invoke() still sends model, max_tokens, and messages to the SDK."""
    fake, captured = _make_capturing_openai(response_text="hello")
    monkeypatch.setattr(llm_client, "resolve_llm_api_key", lambda _env: "k")
    monkeypatch.setattr(llm_client, "OpenAI", fake)

    client = llm_client.OpenAILLMClient(model="gpt-test", max_tokens=64)
    response = client.invoke("hi")

    assert response.content == "hello"
    assert captured["kwargs"]["model"] == "gpt-test"
    assert captured["kwargs"]["max_tokens"] == 64
    assert captured["kwargs"]["messages"] == [{"role": "user", "content": "hi"}]
    assert "stream" not in captured["kwargs"]


def test_openai_invoke_stream_yields_delta_content_chunks(monkeypatch) -> None:
    """invoke_stream() routes through the same builder and yields delta.content in order."""
    fake, captured = _make_capturing_openai(chunk_contents=["Hel", "lo, ", "world"])
    monkeypatch.setattr(llm_client, "resolve_llm_api_key", lambda _env: "k")
    monkeypatch.setattr(llm_client, "OpenAI", fake)

    client = llm_client.OpenAILLMClient(model="gpt-test", max_tokens=64)
    chunks = list(client.invoke_stream("hi"))

    assert chunks == ["Hel", "lo, ", "world"]
    assert captured["kwargs"]["stream"] is True
    assert captured["kwargs"]["model"] == "gpt-test"
    assert captured["kwargs"]["messages"] == [{"role": "user", "content": "hi"}]


def test_openai_invoke_stream_skips_empty_deltas_and_choiceless_chunks(monkeypatch) -> None:
    """OpenAI keep-alive frames have empty delta or no choices — those must not be yielded."""
    fake, _ = _make_capturing_openai(chunk_contents=["Hi", None, "", " there", None])
    monkeypatch.setattr(llm_client, "resolve_llm_api_key", lambda _env: "k")
    monkeypatch.setattr(llm_client, "OpenAI", fake)

    client = llm_client.OpenAILLMClient(model="gpt-test")
    chunks = list(client.invoke_stream("hi"))

    assert chunks == ["Hi", " there"]


def test_openai_invoke_stream_retries_when_no_chunk_emitted(monkeypatch) -> None:
    """Transient failure before any chunk yields → retry succeeds."""
    attempts: list[bool] = []

    class _Delta:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Choice:
        def __init__(self, content: str) -> None:
            self.delta = _Delta(content)

    class _Chunk:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    class _Completions:
        def create(self, **_kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise RuntimeError("Overloaded")
            return iter([_Chunk("recovered")])

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(llm_client, "resolve_llm_api_key", lambda _env: "k")
    monkeypatch.setattr(llm_client, "OpenAI", _OpenAI)
    monkeypatch.setattr(llm_client.time, "sleep", lambda _seconds: None)

    client = llm_client.OpenAILLMClient(model="gpt-test")
    chunks = list(client.invoke_stream("hi"))

    assert chunks == ["recovered"]
    assert len(attempts) == 2


def test_openai_invoke_stream_does_not_retry_after_yielding(monkeypatch) -> None:
    """Mid-stream failure must propagate; retry would duplicate visible chunks."""
    attempts: list[bool] = []

    class _Delta:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Choice:
        def __init__(self, content: str) -> None:
            self.delta = _Delta(content)

    class _Chunk:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    def _yield_then_raise():
        yield _Chunk("partial")
        raise RuntimeError("connection dropped")

    class _Completions:
        def create(self, **_kwargs):
            attempts.append(True)
            return _yield_then_raise()

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(llm_client, "resolve_llm_api_key", lambda _env: "k")
    monkeypatch.setattr(llm_client, "OpenAI", _OpenAI)
    monkeypatch.setattr(llm_client.time, "sleep", lambda _seconds: None)

    client = llm_client.OpenAILLMClient(model="gpt-test")
    iterator = client.invoke_stream("hi")

    assert next(iterator) == "partial"

    raised = False
    try:
        next(iterator)
    except RuntimeError as exc:
        raised = True
        assert "connection dropped" in str(exc)

    assert raised
    assert len(attempts) == 1, "Must not retry after emitting any chunk"


# ---------------------------------------------------------------------------
# _create_llm_client — claude-code CLI provider routing
# ---------------------------------------------------------------------------


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


def test_create_llm_client_gemini_cli_wires_cli_adapter(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini-cli")
    monkeypatch.delenv("GEMINI_CLI_MODEL", raising=False)
    llm_client.reset_llm_singletons()
    try:
        from app.integrations.llm_cli.gemini_cli import GeminiCLIAdapter
        from app.integrations.llm_cli.runner import CLIBackedLLMClient

        client = llm_client._create_llm_client("reasoning")

        assert isinstance(client, CLIBackedLLMClient)
        assert isinstance(client._adapter, GeminiCLIAdapter)
    finally:
        llm_client.reset_llm_singletons()


def test_create_llm_client_gemini_cli_reads_optional_model_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini-cli")
    monkeypatch.setenv("GEMINI_CLI_MODEL", "gemini-2.5-pro")
    llm_client.reset_llm_singletons()
    try:
        client = llm_client._create_llm_client("reasoning")

        assert client._model == "gemini-2.5-pro"
    finally:
        llm_client.reset_llm_singletons()
