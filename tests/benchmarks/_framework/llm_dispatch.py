"""Per-cell LLM provider selection + version pinning enforcement.

opensre's LLM client is a module-level singleton built from env vars
(``LLM_PROVIDER``, ``ANTHROPIC_API_KEY``, ``ANTHROPIC_REASONING_MODEL``,
etc.). To run the benchmark grid across multiple LLMs we need to switch
between them. Two pragmatic constraints:

  1. **Serialize across LLMs, parallel within.** opensre's singleton
     pattern is not thread-safe for per-cell LLM swaps; trying to run
     Claude cell and GPT cell simultaneously races on
     ``_create_llm_client``. So the runner groups cells by LLM and
     activates one at a time.

  2. **Pin every model version.** ``verify_model_version`` runs in
     pre-flight — refuses if a registered spec's model doesn't match
     ``config.model_versions[<llm>]``. Prevents silent drift between
     what the YAML says and what opensre actually calls.

Token tracking is NOT yet wired here. opensre's LLM client tracks per-call
usage internally, but exposing it to the framework's CostTracker is a
follow-up. Until then, ``CostTracker`` records nothing for opensre+LLM
cells and the report shows ``cost_usd=0`` — documented gap, not silent.

Usage from the runner::

    dispatcher = LLMDispatcher()
    for llm in config.llms:
        dispatcher.verify_model_version(llm, config.model_versions[llm])
    for llm in config.llms:
        with dispatcher.activate(llm):
            # ... run all cells for this LLM (parallel within OK) ...
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum

# --------------------------------------------------------------------------- #
# Providers                                                                   #
# --------------------------------------------------------------------------- #


class LLMProvider(StrEnum):
    """opensre's supported LLM providers (matches ``LLM_PROVIDER`` env var)."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    # DeepSeek goes through an openai-compatible API at api.deepseek.com
    OPENAI_COMPATIBLE = "openai_compatible"
    OPENSRE_DEFAULT = "opensre_default"


# --------------------------------------------------------------------------- #
# LLM spec — what each registered llm name resolves to                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LLMSpec:
    """How to dispatch a given LLM name.

    ``reasoning_model`` / ``classification_model`` / ``toolcall_model``
    mirror opensre's three-tier LLM split. For benchmark purposes,
    pinning ``reasoning_model`` is what matters; the other two follow.
    """

    name: str
    provider: LLMProvider
    reasoning_model: str
    classification_model: str
    toolcall_model: str
    # Env var that holds the API key for this provider; checked at activation
    api_key_env: str | None = None
    # Optional base URL for OpenAI-compatible providers (DeepSeek, Together, etc.)
    base_url: str | None = None


# Registry of known LLMs. Add entries here when the benchmark grid grows.
# Pinned model versions match the paper's per-provider snapshots — see
# ``opensre-benchmark-framework.md`` Targets-per-model table.
LLM_SPECS: dict[str, LLMSpec] = {
    # Anthropic — paper used Claude-4-Sonnet
    "claude-4-sonnet": LLMSpec(
        name="claude-4-sonnet",
        provider=LLMProvider.ANTHROPIC,
        reasoning_model="claude-sonnet-4-5-20250929",
        classification_model="claude-sonnet-4-5-20250929",
        toolcall_model="claude-haiku-4-5-20251001",
        api_key_env="ANTHROPIC_API_KEY",
    ),
    "claude-4-opus": LLMSpec(
        name="claude-4-opus",
        provider=LLMProvider.ANTHROPIC,
        reasoning_model="claude-opus-4-7",
        classification_model="claude-sonnet-4-5-20250929",
        toolcall_model="claude-haiku-4-5-20251001",
        api_key_env="ANTHROPIC_API_KEY",
    ),
    # OpenAI — paper used GPT-5 + GPT-4o
    "gpt-5": LLMSpec(
        name="gpt-5",
        provider=LLMProvider.OPENAI,
        reasoning_model="gpt-5-2025-08-07",
        classification_model="gpt-5-2025-08-07",
        toolcall_model="gpt-4o-mini-2024-07-18",
        api_key_env="OPENAI_API_KEY",
    ),
    "gpt-4o": LLMSpec(
        name="gpt-4o",
        provider=LLMProvider.OPENAI,
        reasoning_model="gpt-4o-2024-11-20",
        classification_model="gpt-4o-2024-11-20",
        toolcall_model="gpt-4o-mini-2024-07-18",
        api_key_env="OPENAI_API_KEY",
    ),
    # DeepSeek — OpenAI-compatible
    "deepseek-v3.2": LLMSpec(
        name="deepseek-v3.2",
        provider=LLMProvider.OPENAI_COMPATIBLE,
        reasoning_model="deepseek-chat-v3.2",
        classification_model="deepseek-chat-v3.2",
        toolcall_model="deepseek-chat-v3.2",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/v1",
    ),
    # Default escape hatch — keeps existing env-var config without override
    "claude-default": LLMSpec(
        name="claude-default",
        provider=LLMProvider.OPENSRE_DEFAULT,
        reasoning_model="(opensre-default)",
        classification_model="(opensre-default)",
        toolcall_model="(opensre-default)",
        api_key_env=None,
    ),
}


# --------------------------------------------------------------------------- #
# Errors                                                                      #
# --------------------------------------------------------------------------- #


class UnknownLLM(KeyError):
    """Raised when ``config.llms`` names an LLM not in ``LLM_SPECS``."""

    def __init__(self, llm_name: str) -> None:
        super().__init__(
            f"Unknown LLM {llm_name!r}. Known: {sorted(LLM_SPECS.keys())}. "
            f"Add a spec to LLM_SPECS in tests/benchmarks/_framework/llm_dispatch.py."
        )


class ModelVersionMismatch(ValueError):
    """Raised when ``config.model_versions[<llm>]`` disagrees with the spec.

    Standardization is Pillar 0's most basic mechanism: the framework
    refuses to run if YAML says one model and the spec resolves to another.
    """

    def __init__(self, llm_name: str, configured: str, spec_version: str) -> None:
        super().__init__(
            f"Model-version mismatch for llm={llm_name!r}: "
            f"config.model_versions says {configured!r} but spec resolves to "
            f"{spec_version!r}. Update LLM_SPECS (real provider snapshot) or "
            f"the YAML (intended pin) — they must agree before any run starts."
        )


class MissingAPIKey(RuntimeError):
    """Raised at activation time when an LLM's API-key env var is unset."""

    def __init__(self, llm_name: str, env_var: str) -> None:
        super().__init__(
            f"{llm_name!r} requires env var {env_var} to be set. "
            f"Run with `set -a && source .env && set +a` or export the key first."
        )


# --------------------------------------------------------------------------- #
# LLMDispatcher                                                               #
# --------------------------------------------------------------------------- #


class LLMDispatcher:
    """Activates one LLM at a time for the runner.

    The dispatcher is the framework's single contact point with opensre's
    LLM-client state. Activation:
      1. Snapshot current env vars
      2. Set provider + model-version env vars for the chosen LLM
      3. Call opensre's ``reset_llm_singletons()`` to force re-creation
      4. Yield (runner executes cells)
      5. On exit: restore env, reset singletons again

    Not thread-safe across activations: only one cell-batch should be
    inside ``activate()`` at a time. The runner is structured to serialize
    LLM switches; within an active LLM, parallel cells share the same
    singleton (safe per opensre's own design).
    """

    # Env vars the dispatcher touches. Snapshot + restore these on enter/exit.
    _MANAGED_ENV_VARS: tuple[str, ...] = (
        "LLM_PROVIDER",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_REASONING_MODEL",
        "ANTHROPIC_CLASSIFICATION_MODEL",
        "ANTHROPIC_TOOLCALL_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_REASONING_MODEL",
        "OPENAI_CLASSIFICATION_MODEL",
        "OPENAI_TOOLCALL_MODEL",
        "OPENAI_BASE_URL",
        "DEEPSEEK_API_KEY",
    )

    # ----------------------------------------------------------------------- #
    # Lookup + verification                                                   #
    # ----------------------------------------------------------------------- #

    @staticmethod
    def spec(llm_name: str) -> LLMSpec:
        """Return the registered spec for ``llm_name`` or raise UnknownLLM."""
        try:
            return LLM_SPECS[llm_name]
        except KeyError:
            raise UnknownLLM(llm_name) from None

    @classmethod
    def verify_model_version(cls, llm_name: str, configured: str) -> None:
        """Refuse if configured model_version disagrees with the spec.

        Skipped for ``OPENSRE_DEFAULT`` provider (the escape hatch) — that
        case uses whatever opensre is configured for, no pinning.
        """
        spec = cls.spec(llm_name)
        if spec.provider == LLMProvider.OPENSRE_DEFAULT:
            return
        if configured != spec.reasoning_model:
            raise ModelVersionMismatch(llm_name, configured, spec.reasoning_model)

    # ----------------------------------------------------------------------- #
    # Activation                                                              #
    # ----------------------------------------------------------------------- #

    @contextmanager
    def activate(self, llm_name: str) -> Iterator[LLMSpec]:
        """Temporarily configure opensre to use ``llm_name``.

        Yields the spec so the caller can record `model_version` in
        RunResult rows. On exit, restores the prior env + resets singletons.
        """
        spec = self.spec(llm_name)
        snapshot = self._snapshot_env()
        try:
            self._apply_spec(spec)
            self._reset_opensre_singletons()
            yield spec
        finally:
            self._restore_env(snapshot)
            self._reset_opensre_singletons()

    # ----------------------------------------------------------------------- #
    # Internals                                                               #
    # ----------------------------------------------------------------------- #

    def _snapshot_env(self) -> dict[str, str | None]:
        """Record current values of every env var the dispatcher might touch."""
        return {key: os.environ.get(key) for key in self._MANAGED_ENV_VARS}

    @staticmethod
    def _restore_env(snapshot: dict[str, str | None]) -> None:
        for key, value in snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _apply_spec(self, spec: LLMSpec) -> None:
        """Set env vars to match the spec."""
        if spec.provider == LLMProvider.OPENSRE_DEFAULT:
            # Use whatever's already set — explicit escape hatch
            return

        # Verify API key present
        if spec.api_key_env and not os.environ.get(spec.api_key_env):
            raise MissingAPIKey(spec.name, spec.api_key_env)

        os.environ["LLM_PROVIDER"] = str(spec.provider)
        if spec.provider == LLMProvider.ANTHROPIC:
            os.environ["ANTHROPIC_REASONING_MODEL"] = spec.reasoning_model
            os.environ["ANTHROPIC_CLASSIFICATION_MODEL"] = spec.classification_model
            os.environ["ANTHROPIC_TOOLCALL_MODEL"] = spec.toolcall_model
        elif spec.provider == LLMProvider.OPENAI:
            os.environ["OPENAI_REASONING_MODEL"] = spec.reasoning_model
            os.environ["OPENAI_CLASSIFICATION_MODEL"] = spec.classification_model
            os.environ["OPENAI_TOOLCALL_MODEL"] = spec.toolcall_model
        elif spec.provider == LLMProvider.OPENAI_COMPATIBLE:
            # DeepSeek and similar — OpenAI client + base URL override
            os.environ["LLM_PROVIDER"] = str(LLMProvider.OPENAI)
            os.environ["OPENAI_REASONING_MODEL"] = spec.reasoning_model
            os.environ["OPENAI_CLASSIFICATION_MODEL"] = spec.classification_model
            os.environ["OPENAI_TOOLCALL_MODEL"] = spec.toolcall_model
            if spec.base_url:
                os.environ["OPENAI_BASE_URL"] = spec.base_url
            # Some providers store the key under a custom env; map it to OPENAI_API_KEY
            if spec.api_key_env and spec.api_key_env != "OPENAI_API_KEY":
                key_value = os.environ.get(spec.api_key_env)
                if key_value:
                    os.environ["OPENAI_API_KEY"] = key_value

    @staticmethod
    def _reset_opensre_singletons() -> None:
        """Force opensre to rebuild its LLM clients from the new env on next call.

        Both singleton caches must be cleared. ``reset_llm_singletons`` only
        clears the reasoning/classification/toolcall clients in
        ``app.services.llm_client``; the investigation agent (and the
        cloudopsbench predictor) call ``get_agent_llm`` in
        ``app.services.agent_llm_client``, which keeps a SEPARATE
        ``_agent_client`` singleton. Without resetting it too, the agent
        client built during the first LLM's cells is reused for every
        subsequent LLM — so e.g. a ``gpt-5`` stratum silently runs on the
        ``gpt-4o`` client activated first. This was an undetected bug that
        made multi-LLM grids report the first model's results under every
        model's name.
        """
        # Late import — keeps llm_dispatch.py importable without opensre deps
        from app.services.agent_llm_client import reset_agent_client
        from app.services.llm_client import reset_llm_singletons

        reset_llm_singletons()
        reset_agent_client()


# --------------------------------------------------------------------------- #
# Convenience                                                                  #
# --------------------------------------------------------------------------- #


def known_llms() -> list[str]:
    """Names of every LLM registered in LLM_SPECS."""
    return sorted(LLM_SPECS.keys())
