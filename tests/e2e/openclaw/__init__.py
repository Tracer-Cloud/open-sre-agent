"""End-to-end tests for the OpenClaw MCP integration.

Boots a real local OpenClaw instance, injects a fault, drives a real
OpenClaw conversation that hits the broken path, captures the failure,
and asserts the OpenSRE investigation pipeline names OpenClaw + the
specific failure mode.

See :mod:`tests.e2e.openclaw.test_local` for the pytest entrypoint and
``tests/e2e/openclaw/README.md`` for prerequisites + how to run locally.
"""
