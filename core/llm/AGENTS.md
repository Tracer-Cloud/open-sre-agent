# Hosted LLM Runtime

This package owns hosted LLM provider clients and runtime helpers used by the
agent loop. Subprocess-backed LLM CLIs live under `integrations/llm_cli/`.

## Where provider wiring lives

| File | Role |
| --- | --- |
| `config/config.py` | Declares `LLMProvider`, provider env vars, defaults, and validation requirements. |
| `config/llm_auth/provider_catalog.py` | Canonical `ProviderSpec` metadata shared by wizard, auth, and runtime checks. |
| `core/llm/llm_client.py` | Routes `LLM_PROVIDER` to chat/reasoning/classification/toolcall clients. |
| `core/llm/agent_llm_client.py` | Investigation ReAct loop: tool-calling clients (`get_agent_llm`). |
| `core/llm/transport_mode.py` | `OPENSRE_LLM_TRANSPORT` (`sdk` vs `litellm`) and `use_litellm_for_provider()`. |
| `core/llm/client_cache_key.py` | Singleton cache invalidation key `(transport, runtime_provider)`. |
| `core/llm/openai_compat_providers.py` | OpenAI-compatible provider catalog and model/base-URL resolution. |
| `core/llm/azure_openai.py` | Azure OpenAI helpers: endpoint normalization, deployment selection, LiteLLM kwargs. |
| `core/llm/litellm/routing.py` | Per-provider LiteLLM client construction (model prefix, `api_base`, `api_version`). |
| `core/llm/litellm/clients.py` | `LiteLLMAgentClient` / `LiteLLMLLMClient` wrappers around `litellm.completion`. |
| `core/llm/sdk/agent_clients.py` | Native SDK tool-calling clients (Anthropic, OpenAI, Bedrock, CLI-backed). |
| `core/llm/sdk/llm_clients.py` | Native SDK non-agent clients. |
| `core/llm/tool_schema_normalize.py` | JSON Schema normalization shared by strict tool-calling adapters. |
| `surfaces/cli/wizard/config.py` | Onboarding metadata (`SUPPORTED_PROVIDERS`) and model choices. |
| `surfaces/cli/wizard/env_sync.py` | `.env` synchronization when provider/model choices change. |

User-facing setup and env var tables: [`docs/llm-providers.mdx`](../../docs/llm-providers.mdx).

## Transport: native SDK vs LiteLLM

Default path is **native vendor SDKs** (`OPENSRE_LLM_TRANSPORT` unset or `sdk`).

**LiteLLM path** (`OPENSRE_LLM_TRANSPORT=litellm`): routes hosted API providers through
`core/llm/litellm/routing.py` instead of `core/llm/sdk/*`.

**Azure OpenAI** (`LLM_PROVIDER=azure-openai`) **always** uses LiteLLM — even when
`OPENSRE_LLM_TRANSPORT` is unset. Onboarding writes `OPENSRE_LLM_TRANSPORT=litellm` to
`.env`; switching away from Azure removes that key so other providers return to SDK routing.

Dispatch entrypoints:

```text
get_agent_llm() / get_llm_for_*()
  → use_litellm_for_provider(runtime_provider)?
      yes → build_litellm_*_client(settings, provider)   # litellm/routing.py
      no  → native SDK client in sdk/agent_clients.py or sdk/llm_clients.py
```

When changing routing, update **both** `agent_llm_client.py` and `llm_client.py` if the
provider appears in investigation and chat/reasoning surfaces.

Singleton caches invalidate on `(transport, runtime_provider)` changes — not transport alone.
REPL `/model` and wizard env sync call `reset_llm_singletons()` / `reset_agent_client()`.

## Adding a Hosted API Provider

1. Add the provider literal to `LLMProvider` and normalization/validation paths in `config/config.py`.
2. Add `ProviderSpec` in `config/llm_auth/provider_catalog.py` and matching `ProviderOption` in
   `surfaces/cli/wizard/config.py` (model env vars, defaults, `endpoint_env` if needed).
3. Add runtime routing:
   - **SDK path:** `core/llm/sdk/llm_clients.py` and/or `core/llm/sdk/agent_clients.py`, wired from
     `llm_client.py` / `agent_llm_client.py`.
   - **LiteLLM path (optional or required):** branch in `core/llm/litellm/routing.py`.
   - **OpenAI-compatible:** register in `openai_compat_providers.py` (SDK compat path) and/or
     `litellm/routing.py` (LiteLLM path).
4. Update `surfaces/cli/wizard/env_sync.py` if you introduce new non-secret env keys; keep endpoint
   keys in `active_non_secret` when the provider needs persisted URL/version settings.
5. Add or update tests under `tests/core/runtime/llm/` and wizard tests if onboarding changes.

### Azure OpenAI (`azure-openai`)

Azure uses **deployment names** (not public OpenAI model IDs) and a resource **base URL**:

- `AZURE_OPENAI_BASE_URL`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION` (default applied when unset)
- `AZURE_OPENAI_*_MODEL` env vars hold deployment names in the user's Azure resource
- LiteLLM model string: `azure/<deployment>` via `azure_openai_litellm_model()`

Do not add a separate Azure client class — extend `litellm/routing.py` and helpers in
`azure_openai.py`.

For investigation tool calling details, see
[`docs/investigation-tool-calling.md`](../../docs/investigation-tool-calling.md).
