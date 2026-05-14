"""Test fixtures for the OpenClaw end-to-end suite.

Currently contains:
- :mod:`sleeping_mcp_server`: stdio MCP server with a single tool that
  sleeps forever. Used by
  :func:`tests.e2e.openclaw.infrastructure_sdk.fault_injection.inject_sleeping_tool_call`
  to exercise OpenSRE's tool-call timeout behavior without depending on
  OpenClaw itself.
"""
