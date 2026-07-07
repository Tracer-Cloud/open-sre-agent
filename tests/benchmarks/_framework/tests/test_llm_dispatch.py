"""Unit tests for the per-cell LLM dispatcher + version-pinning gate."""

from __future__ import annotations

import os

import pytest

from tests.benchmarks._framework.llm_dispatch import (
    LLM_SPECS,
    LLMDispatcher,
    LLMProvider,
    LLMSpec,
    MissingAPIKey,
    ModelVersionMismatch,
    UnknownLLM,
    known_llms,
)

# --------------------------------------------------------------------------- #
# Fixture: prevent the dispatcher from touching opensre's real singletons.    #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _patch_reset_singletons(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace _reset_opensre_singletons with a no-op so tests don't
    require opensre's real LLM client to be importable."""
    monkeypatch.setattr(
        LLMDispatcher,
        "_reset_opensre_singletons",
        staticmethod(lambda: None),
    )


# --------------------------------------------------------------------------- #
# Spec lookup                                                                  #
# --------------------------------------------------------------------------- #


def test_spec_returns_registered_llm() -> None:
    spec = LLMDispatcher.spec("claude-4-sonnet")
    assert spec.provider == LLMProvider.ANTHROPIC
    assert spec.reasoning_model == "claude-sonnet-4-5-20250929"


def test_spec_unknown_raises_unknown_llm() -> None:
    with pytest.raises(UnknownLLM) as exc_info:
        LLMDispatcher.spec("not-a-real-llm")
    assert "not-a-real-llm" in str(exc_info.value)


def test_known_llms_returns_sorted_registry_keys() -> None:
    names = known_llms()
    assert names == sorted(LLM_SPECS.keys())
    assert "claude-4-sonnet" in names
    assert "gpt-5" in names
    assert "deepseek-v3.2" in names


# --------------------------------------------------------------------------- #
# Version pinning                                                              #
# --------------------------------------------------------------------------- #


def test_verify_model_version_passes_when_pin_matches_spec() -> None:
    LLMDispatcher.verify_model_version("claude-4-sonnet", "claude-sonnet-4-5-20250929")


def test_verify_model_version_raises_on_mismatch() -> None:
    with pytest.raises(ModelVersionMismatch) as exc_info:
        LLMDispatcher.verify_model_version("claude-4-sonnet", "claude-sonnet-3-5-old")
    msg = str(exc_info.value)
    assert "claude-4-sonnet" in msg
    assert "claude-sonnet-3-5-old" in msg
    assert "claude-sonnet-4-5-20250929" in msg


def test_verify_model_version_skips_opensre_default() -> None:
    """Escape-hatch LLM uses whatever opensre is configured for — no pin check."""
    LLMDispatcher.verify_model_version("claude-default", "literally-anything")


def test_verify_model_version_unknown_llm_raises_unknown_llm() -> None:
    with pytest.raises(UnknownLLM):
        LLMDispatcher.verify_model_version("phantom-model", "some-version")


# --------------------------------------------------------------------------- #
# activate() — env var swap + restore                                          #
# --------------------------------------------------------------------------- #


def test_activate_anthropic_sets_provider_and_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    dispatcher = LLMDispatcher()
    with dispatcher.activate("claude-4-sonnet") as spec:
        assert os.environ["LLM_PROVIDER"] == "anthropic"
        assert os.environ["ANTHROPIC_REASONING_MODEL"] == "claude-sonnet-4-5-20250929"
        assert os.environ["ANTHROPIC_TOOLCALL_MODEL"] == "claude-haiku-4-5-20251001"
        assert spec.name == "claude-4-sonnet"


def test_activate_openai_sets_provider_and_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    dispatcher = LLMDispatcher()
    with dispatcher.activate("gpt-4o") as spec:
        assert os.environ["LLM_PROVIDER"] == "openai"
        assert os.environ["OPENAI_REASONING_MODEL"] == "gpt-4o-2024-11-20"
        assert spec.reasoning_model == "gpt-4o-2024-11-20"


def test_activate_openai_compatible_uses_openai_provider_with_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek goes through the OpenAI client with a base URL override; the
    DEEPSEEK_API_KEY is also mapped to OPENAI_API_KEY so the SDK can find it."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    dispatcher = LLMDispatcher()
    with dispatcher.activate("deepseek-v3.2"):
        # Provider is overridden to "openai" (not "openai_compatible")
        assert os.environ["LLM_PROVIDER"] == "openai"
        assert os.environ["OPENAI_REASONING_MODEL"] == "deepseek-chat-v3.2"
        assert os.environ["OPENAI_BASE_URL"] == "https://api.deepseek.com/v1"
        assert os.environ["OPENAI_API_KEY"] == "ds-key"


def test_activate_opensre_default_does_not_touch_provider_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The escape hatch keeps whatever env is already set."""
    monkeypatch.setenv("LLM_PROVIDER", "preexisting-value")
    dispatcher = LLMDispatcher()
    with dispatcher.activate("claude-default"):
        assert os.environ["LLM_PROVIDER"] == "preexisting-value"


def test_activate_missing_api_key_raises_missing_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    dispatcher = LLMDispatcher()
    with pytest.raises(MissingAPIKey) as exc_info, dispatcher.activate("claude-4-sonnet"):
        pass
    assert "claude-4-sonnet" in str(exc_info.value)
    assert "ANTHROPIC_API_KEY" in str(exc_info.value)


def test_activate_restores_env_on_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("LLM_PROVIDER", "before")
    monkeypatch.setenv("ANTHROPIC_REASONING_MODEL", "old-model")
    dispatcher = LLMDispatcher()
    with dispatcher.activate("claude-4-sonnet"):
        assert os.environ["LLM_PROVIDER"] == "anthropic"
    # After exit, prior values are restored
    assert os.environ["LLM_PROVIDER"] == "before"
    assert os.environ["ANTHROPIC_REASONING_MODEL"] == "old-model"


def test_activate_clears_env_vars_that_were_unset_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env vars introduced inside activate() must be removed on exit."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.delenv("ANTHROPIC_REASONING_MODEL", raising=False)
    dispatcher = LLMDispatcher()
    with dispatcher.activate("claude-4-sonnet"):
        assert "ANTHROPIC_REASONING_MODEL" in os.environ
    assert "ANTHROPIC_REASONING_MODEL" not in os.environ


def test_activate_restores_env_when_body_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exceptions inside the `with` block must not leak env state."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("LLM_PROVIDER", "before")
    dispatcher = LLMDispatcher()

    # Isolating the raise in a helper keeps the post-`with` assertion clearly
    # reachable to static analysis (which does not model pytest.raises as an
    # exception-suppressing context manager).
    def _raise_inside_dispatch_context() -> None:
        with dispatcher.activate("claude-4-sonnet"):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _raise_inside_dispatch_context()
    assert os.environ["LLM_PROVIDER"] == "before"


# --------------------------------------------------------------------------- #
# LLMSpec immutability                                                         #
# --------------------------------------------------------------------------- #


def test_llm_spec_is_frozen() -> None:
    spec = LLMSpec(
        name="x",
        provider=LLMProvider.ANTHROPIC,
        reasoning_model="r",
        classification_model="c",
        toolcall_model="t",
    )
    # dataclasses.FrozenInstanceError subclasses AttributeError
    with pytest.raises(AttributeError):
        spec.name = "y"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Singleton-reset coverage: protects against the "every cell silently runs    #
# the first activated model" bug that hit the 06-05 11:46 run.                #
#                                                                             #
# Diagnostic from that run: `cost.by_model` showed 180 calls to gpt-4o and 0  #
# to gpt-5, even though the config listed both. Root cause: the dispatcher's  #
# ``_reset_opensre_singletons`` only cleared ``llm_client._client`` but not   #
# ``agent_llm_client._agent_client`` — and the investigation agent uses the   #
# latter. With the agent client cached from the first activation, every       #
# subsequent activation's cells reused it under whatever model the first      #
# activation set.                                                             #
# --------------------------------------------------------------------------- #


def test_reset_opensre_singletons_clears_both_module_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``factory.reset_llm_clients`` MUST be called on every activation
    switch. Skipping it re-uses the first activation's cached clients for
    the rest of the run."""
    # Re-install the real method (the autouse fixture stubs it out)
    monkeypatch.undo()

    call_log: list[str] = []

    # The unified factory cache is cleared by one reset_llm_clients() call.
    monkeypatch.setattr(
        "core.llm.factory.reset_llm_clients", lambda: call_log.append("reset_llm_clients")
    )

    LLMDispatcher._reset_opensre_singletons()  # type: ignore[attr-defined]

    assert call_log == ["reset_llm_clients"], (
        "the unified LLM client cache was NOT cleared — opensre would keep returning "
        "the previously-activated provider's client for every subsequent LLM in the grid"
    )


def test_activation_round_trip_resets_singletons_on_enter_and_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reset must run both BEFORE yield (clears stale client so the
    new env's get_llm(LLMRole.AGENT) rebuilds) AND on finally (restores prior
    state so the next activation isn't polluted)."""
    monkeypatch.undo()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    reset_calls: list[str] = []
    monkeypatch.setattr(
        LLMDispatcher,
        "_reset_opensre_singletons",
        staticmethod(lambda: reset_calls.append("reset")),
    )

    dispatcher = LLMDispatcher()
    with dispatcher.activate("claude-4-sonnet"):
        # First reset happens BEFORE the yield, after env vars are applied
        assert reset_calls == ["reset"], (
            "Singleton reset must run between env-var application and the "
            "yield, so opensre rebuilds its client against the new env"
        )

    # Second reset happens in the finally branch after env restoration
    assert reset_calls == ["reset", "reset"], (
        "Singleton reset must also run on activation exit, otherwise the "
        "next activation's first cell could be polluted by the prior LLM's "
        "cached client"
    )
