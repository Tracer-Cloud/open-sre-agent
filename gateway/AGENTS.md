# Gateway Package Guidance

Gateway tests live in `gateway/tests/`, not the repo-wide `tests/` tree.

This package is a bounded messaging surface with its own app entrypoint,
platform adapters, storage, security, sinks, and process runner. Keeping its
tests package-local makes gateway refactors easier to review and keeps the
gateway implementation and regressions together. New gateway unit tests should
be added under `gateway/tests/`.

Pytest discovers these tests through `pytest.ini`; scoped CI maps changes under
`gateway/` to `gateway/tests/` through `.github/ci/test_scope_rules.py`.

## Gateway Agent Dispatch Architecture

- `GatewayManager.start_gateway()` owns the gateway `Agent` instance. The nested
  `handle_callback_to_gateway_agent(text, session, sink, logger)` callback must
  dispatch through that object with `gateway_agent.dispatch_message_to_headless_agent(...)`.
  Do not add a standalone gateway turn-dispatch helper for this path; that hides
  the gateway-manager-owned agent lifecycle.
- The Telegram polling layer calls the gateway callback with exactly four
  arguments: text, session, sink, and logger. Do not reintroduce `chat_id` into
  this callback contract; the sink already owns the chat transport details.
- Reuse `DefaultToolProvider` for action tools in the gateway callback. If the
  gateway has already built the action-tool list for its agent, pass it through
  `DefaultToolProvider(precomputed_action_tools=...)` rather than adding another
  gateway-specific tool-provider adapter.
- Gateway E2E regression tests should drive a normalized polled Telegram message
  into `handle_polled_inbound_telegram_message(...)` and let it invoke the
  nested gateway callback. Do not test this path by swapping in fake LLM clients;
  prefer explicit registered commands such as `/status` when the test only needs
  to validate dispatch and provider wiring.
