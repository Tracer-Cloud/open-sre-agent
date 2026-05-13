# OpenClaw end-to-end test suite

Boots a real local OpenClaw instance, injects a fault, drives a real
OpenClaw conversation that hits the broken path, captures the failure,
and asserts the OpenSRE investigation pipeline names OpenClaw + the
specific failure mode.

Parent issue: [#1484](https://github.com/Tracer-Cloud/opensre/issues/1484).

## Prerequisites

- The `openclaw` CLI installed and on `$PATH`. Tests skip cleanly when
  it's not present, so contributors who haven't installed it can still
  run `make test-cov` without these failing.
- **Node `22.12+` active in the current shell.** OpenClaw requires
  it. With `nvm`: run `nvm use` (the repo ships an `.nvmrc` pinning
  22). Without nvm: `brew install node@22 && brew link --overwrite node@22`.
- Docker daemon running (for any future container-backed fault
  scenarios — not required for the initial gateway-down scenario).

## How to run locally

```bash
make test-openclaw
```

Equivalent to:

```bash
.venv/bin/python -m pytest -m e2e -v tests/e2e/openclaw/
```

`make test-openclaw` is excluded from `make test-cov` — the unit suite
stays runnable without the OpenClaw CLI installed.

## Layout

```
tests/e2e/openclaw/
├── __init__.py
├── README.md                       (this file)
├── infrastructure_sdk/
│   ├── __init__.py
│   ├── local.py                    boot/teardown helpers, OpenClawHandle
│   └── fault_injection.py          gateway-down / sleeping-tool / wrong-endpoint injectors
├── fixtures/
│   └── sleeping_mcp_server.py      stdio MCP fixture used by the timeout scenario
├── use_case.py                     drives an OpenClaw conversation, captures failure
├── orchestrator.py                 builds alert, invokes OpenSRE pipeline
├── test_local.py                   scaffold + boot/teardown smoke
├── test_gateway_down.py            gateway-down scenario
├── test_wrong_endpoint.py          wrong-endpoint scenario
└── test_tool_call_timeout.py       tool-call timeout scenario
```

Each fault scenario lives in its own `test_<scenario>.py` next to
`test_local.py`. Every scenario has two sub-tests: a fast use-case
assertion that doesn't need an LLM, and a full RCA assertion that
calls the investigation pipeline (gated on
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`).

## Conventions followed

- `@pytest.mark.e2e` marker on every test (excluded from `make test-cov`).
- `pytest.skip(...)` with a clear reason when the `openclaw` CLI is
  absent — same pattern as
  `tests/e2e/upstream_lambda/conftest.py::infrastructure_available`.
- Separation of concerns: boot helpers stay in `infrastructure_sdk/`,
  business logic in `use_case.py`, RCA invocation in
  `orchestrator.py`, assertions in `test_<scenario>.py`.
- Uses `tests/fixtures/openclaw_e2e_alert.json` as the alert template
  (parallels `tests/fixtures/openclaw_test_alert.json` shape).

## References

- Parent issue: [#1484](https://github.com/Tracer-Cloud/opensre/issues/1484)
- Existing patterns: `tests/e2e/crashloop/test_local.py`,
  `tests/e2e/upstream_lambda/test_agent_e2e.py`
- OpenClaw integration code: `app/integrations/openclaw.py`
- Existing unit coverage (do not duplicate):
  `tests/test_openclaw_integration.py`
- Test conventions: `tests/AGENTS.md`
