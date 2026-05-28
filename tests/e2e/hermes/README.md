# Hermes e2e suites

Hermes e2e tests execute the OpenSRE investigation pipeline against fixture-backed
Hermes evidence with `context_sources="hermes"`.

Run only Hermes e2e:

```bash
uv run pytest tests/e2e/hermes -m e2e -v
```

These tests are LLM-credential gated via `has_credentials_for_active_llm_provider()`.
