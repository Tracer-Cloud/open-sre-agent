"""FixtureOpenClawBackend for synthetic investigation tests.

Intercepts all calls to app.integrations.openclaw.call_openclaw_tool and
app.integrations.openclaw.list_openclaw_tools, serving fixture data without
any live OpenClaw process.

Usage in run_suite.py
---------------------
    backend = FixtureOpenClawBackend(scenario)
    with backend.patch():
        state = run_investigation(..., resolved_integrations={
            "openclaw": {
                "connection_verified": True,
                "mode": "stdio",
                "command": "openclaw",
                "args": ["mcp", "serve"],
                "url": "",
                "auth_token": "",
            }
        })

The backend intercepts calls at the integration layer so the full tool call
path (is_available check → extract_params → _resolve_config →
openclaw_runtime_unavailable_reason bypass → invoke_openclaw_mcp_tool) is
exercised, except the final network/subprocess hop.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

if TYPE_CHECKING:
    from collections.abc import Generator

    from tests.synthetic.openclaw.scenario_loader import OpenClawScenario


class FixtureOpenClawBackend:
    """Serves scenario fixture data in place of live OpenClaw MCP calls."""

    def __init__(self, scenario: OpenClawScenario) -> None:
        self._scenario = scenario

    # ------------------------------------------------------------------
    # Core dispatch
    # ------------------------------------------------------------------

    def call_tool(
        self,
        _config: Any,
        tool_name: str,
        arguments: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Intercept call_openclaw_tool and return fixture data."""
        handler = self._tool_handlers().get(tool_name)
        if handler is None:
            return {
                "is_error": True,
                "text": f"FixtureOpenClawBackend: unknown tool '{tool_name}'",
                "tool": tool_name,
                "arguments": arguments or {},
                "content": [],
                "structured_content": None,
            }
        return handler(arguments or {})  # type: ignore[no-any-return]

    def list_tools(self, _config: Any) -> list[dict[str, object]]:
        """Intercept list_openclaw_tools and return fixture tool descriptors."""
        return self._scenario.fixture_tools

    # ------------------------------------------------------------------
    # Patch context manager
    # ------------------------------------------------------------------

    @contextmanager
    def patch(self) -> Generator[None]:
        """Patch MCP transport functions and the runtime-availability check.

        Patches all three locations where openclaw_runtime_unavailable_reason
        is imported by name:
          - app.integrations.openclaw   (canonical source)
          - app.tools.OpenClawMCPTool   (tool layer local alias)
          - app.nodes.plan_actions.detect_sources  (source detection layer)

        Also patches the tool call and list functions at both the integration
        module and the tool module's local aliases.
        """
        with (
            patch(
                "app.integrations.openclaw.call_openclaw_tool",
                side_effect=self.call_tool,
            ),
            patch(
                "app.tools.OpenClawMCPTool.invoke_openclaw_mcp_tool",
                side_effect=self.call_tool,
            ),
            patch(
                "app.integrations.openclaw.list_openclaw_tools",
                side_effect=self.list_tools,
            ),
            patch(
                "app.tools.OpenClawMCPTool.list_openclaw_mcp_tools",
                side_effect=self.list_tools,
            ),
            patch(
                "app.integrations.openclaw.openclaw_runtime_unavailable_reason",
                return_value=None,
            ),
            patch(
                "app.tools.OpenClawMCPTool.openclaw_runtime_unavailable_reason",
                return_value=None,
            ),
            patch(
                "app.nodes.plan_actions.detect_sources.openclaw_runtime_unavailable_reason",
                return_value=None,
            ),
        ):
            yield

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    def _tool_handlers(self) -> dict[str, Any]:
        return {
            "conversations_list": self._handle_conversations_list,
            "conversations_get": self._handle_conversations_get,
            "conversations_create": self._handle_conversations_create,
            "message_send": self._handle_message_send,
        }

    def _handle_conversations_list(self, arguments: dict[str, object]) -> dict[str, object]:
        conversations = self._scenario.fixture_conversations
        search = str(arguments.get("search", "")).lower().strip()
        if search:
            filtered = [
                c
                for c in conversations
                if search in str(c.get("title", "")).lower()
                or search in str(c.get("lastMessage", "")).lower()
            ]
            # Fall back to all conversations when search term yields no matches
            # (mirrors real OpenClaw returning recent conversations on broad queries)
            conversations = filtered if filtered else conversations
        limit = int(str(arguments.get("limit", 10)))
        conversations = conversations[:limit]
        return {
            "is_error": False,
            "tool": "conversations_list",
            "arguments": arguments,
            "text": f"Found {len(conversations)} conversation(s).",
            "content": [{"type": "text", "text": f"Found {len(conversations)} conversation(s)."}],
            "structured_content": {"conversations": conversations},
        }

    def _handle_conversations_get(self, arguments: dict[str, object]) -> dict[str, object]:
        conversation_id = str(arguments.get("conversationId", ""))
        for conv in self._scenario.fixture_conversations:
            if conv.get("id") == conversation_id:
                return {
                    "is_error": False,
                    "tool": "conversations_get",
                    "arguments": arguments,
                    "text": conv.get("lastMessage", ""),
                    "content": [{"type": "text", "text": str(conv.get("lastMessage", ""))}],
                    "structured_content": conv,
                }
        return {
            "is_error": True,
            "tool": "conversations_get",
            "arguments": arguments,
            "text": f"Conversation '{conversation_id}' not found.",
            "content": [],
            "structured_content": None,
        }

    def _handle_conversations_create(self, arguments: dict[str, object]) -> dict[str, object]:
        return {
            "is_error": False,
            "tool": "conversations_create",
            "arguments": arguments,
            "text": "Conversation created successfully.",
            "content": [{"type": "text", "text": "Conversation created successfully."}],
            "structured_content": {"id": "fixture-conv-new", "title": arguments.get("title", "")},
        }

    def _handle_message_send(self, arguments: dict[str, object]) -> dict[str, object]:
        return {
            "is_error": False,
            "tool": "message_send",
            "arguments": arguments,
            "text": "Message sent.",
            "content": [{"type": "text", "text": "Message sent."}],
            "structured_content": None,
        }
