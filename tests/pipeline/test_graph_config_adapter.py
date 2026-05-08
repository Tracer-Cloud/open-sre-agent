from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.nodes.auth import inject_auth_node
from app.pipeline.graph import _accept_langgraph_config
from app.state import AgentState, make_chat_state


def test_langgraph_config_adapter_injects_runtime_config() -> None:
    graph = StateGraph(AgentState)
    graph.add_node("inject_auth", _accept_langgraph_config(inject_auth_node))
    graph.set_entry_point("inject_auth")
    graph.add_edge("inject_auth", END)
    compiled = graph.compile()

    state = compiled.invoke(
        make_chat_state(messages=[]),
        {
            "configurable": {
                "langgraph_auth_user": {
                    "org_id": "org-1",
                    "identity": "user-1",
                    "email": "user@example.com",
                    "full_name": "User One",
                    "organization_slug": "test-org",
                },
                "thread_id": "thread-1",
                "run_id": "run-1",
            }
        },
    )

    assert state["org_id"] == "org-1"
    assert state["user_id"] == "user-1"
    assert state["user_email"] == "user@example.com"
    assert state["user_name"] == "User One"
    assert state["organization_slug"] == "test-org"
    assert state["thread_id"] == "thread-1"
    assert state["run_id"] == "run-1"
