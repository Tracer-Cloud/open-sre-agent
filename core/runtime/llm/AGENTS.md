# Hosted LLM Runtime

This package owns hosted LLM provider clients and runtime helpers used by the
agent loop. Subprocess-backed LLM CLIs live under `integrations/llm_cli/`.

## Where provider wiring lives

| File | Role |
| --- | --- |
| `config/config.py` | Declares `LLMProvider`, provider env vars, defaults, and validation requirements. |
| `core/runtime/llm/llm_client.py` | Routes `LLM_PROVIDER` to the chat/reasoning runtime client implementation. |
| `core/runtime/llm/agent_llm_client.py` | Investigation ReAct loop: tool-calling clients (`get_agent_llm`). |
| `core/runtime/llm/bedrock_converse.py` | Bedrock Converse request and response shaping. |
| `core/runtime/llm/tool_schema_normalize.py` | JSON Schema normalization shared by strict tool-calling adapters. |
| `core/orchestration/node/investigate/` | Investigation agent, prompts, and seed tool calls. |
| `core/runtime/` | Shared tool-loop and provider-specific assistant/tool-result messages. |
| `cli/wizard/config.py` | Onboarding metadata (`SUPPORTED_PROVIDERS`) and model choices. |
| `cli/wizard/env_sync.py` | `.env` synchronization when provider/model choices change. |

## Adding a Hosted API Provider

1. Add the provider literal to `LLMProvider` and normalization/validation paths in `config/config.py`.
2. Add provider metadata in `cli/wizard/config.py` (`ProviderOption`, model env vars, defaults).
3. Add runtime routing in `core/runtime/llm/llm_client.py` and, for investigation tool calling, `core/runtime/llm/agent_llm_client.py`.
4. Update `.env` sync behavior if you introduce new model/API env keys.
5. Add or update tests under `tests/core/runtime/llm/` and wizard tests if onboarding changes.

For investigation tool calling details, see
[`docs/investigation-tool-calling.md`](../../../docs/investigation-tool-calling.md).
