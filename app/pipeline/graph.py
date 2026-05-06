"""Unified agent pipeline — wires nodes and edges into a LangGraph."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, cast

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.nodes import (
    node_adapt_window,
    node_diagnose_root_cause,
    node_extract_alert,
    node_plan_actions,
    node_publish_findings,
    node_resolve_integrations,
)
from app.nodes.auth import inject_auth_node
from app.nodes.chat import (
    chat_agent_node,
    general_node,
    router_node,
    tool_executor_node,
)
from app.nodes.evaluate_opensre import node_opensre_llm_eval
from app.nodes.investigate.merge import merge_hypothesis_results
from app.nodes.investigate.parallel import node_investigate_hypothesis
from app.pipeline.routing import (
    distribute_hypotheses,
    route_after_extract,
    route_by_mode,
    route_chat,
    route_investigation_loop,
    should_call_tools,
)
from app.state import AgentState
from app.types.config import NodeConfig

NodeWithConfig = Callable[[AgentState, NodeConfig | None], dict[str, Any]]


def _accept_langgraph_config(func: NodeWithConfig) -> Callable[..., dict[str, Any]]:
    """Expose an unannotated config kwarg for LangGraph runtime injection."""

    @functools.wraps(func)
    def _wrapped(state: AgentState, config=None) -> dict[str, Any]:
        return func(state, cast(NodeConfig | None, config))

    return _wrapped


def build_graph(config: None = None) -> CompiledStateGraph:
    """Build and compile the LangGraph agent."""
    _ = config
    graph = StateGraph(AgentState)

    graph.add_node("inject_auth", _accept_langgraph_config(inject_auth_node))

    graph.add_node("router", router_node)
    graph.add_node("chat_agent", _accept_langgraph_config(chat_agent_node))
    graph.add_node("general", _accept_langgraph_config(general_node))
    graph.add_node("tool_executor", tool_executor_node)

    graph.add_node("extract_alert", _accept_langgraph_config(node_extract_alert))
    graph.add_node("resolve_integrations", _accept_langgraph_config(node_resolve_integrations))
    graph.add_node("plan_actions", _accept_langgraph_config(node_plan_actions))
    graph.add_node("investigate_hypothesis", node_investigate_hypothesis)
    graph.add_node("merge_hypothesis_results", merge_hypothesis_results)
    graph.add_node("diagnose", _accept_langgraph_config(node_diagnose_root_cause))
    graph.add_node("adapt_window", _accept_langgraph_config(node_adapt_window))
    graph.add_node("opensre_eval", node_opensre_llm_eval)
    graph.add_node("publish", _accept_langgraph_config(node_publish_findings))

    graph.set_entry_point("inject_auth")

    graph.add_conditional_edges(
        "inject_auth", route_by_mode, {"chat": "router", "investigation": "extract_alert"}
    )

    graph.add_conditional_edges(
        "router", route_chat, {"tracer_data": "chat_agent", "general": "general"}
    )
    graph.add_conditional_edges(
        "chat_agent", should_call_tools, {"call_tools": "tool_executor", "done": END}
    )
    graph.add_edge("tool_executor", "chat_agent")
    graph.add_edge("general", END)

    graph.add_conditional_edges(
        "extract_alert", route_after_extract, {"end": END, "investigate": "resolve_integrations"}
    )
    graph.add_edge("resolve_integrations", "plan_actions")
    graph.add_conditional_edges("plan_actions", distribute_hypotheses)
    graph.add_edge("investigate_hypothesis", "merge_hypothesis_results")
    graph.add_edge("merge_hypothesis_results", "diagnose")
    # When the routing function returns "investigate" (loop again), the path
    # goes through ``adapt_window`` first so the window can be widened
    # before the next plan_actions iteration. Terminal paths
    # ("opensre_eval", "publish") bypass adapt_window entirely.
    graph.add_conditional_edges(
        "diagnose",
        route_investigation_loop,
        {"investigate": "adapt_window", "opensre_eval": "opensre_eval", "publish": "publish"},
    )
    graph.add_edge("adapt_window", "plan_actions")
    graph.add_edge("opensre_eval", "publish")
    graph.add_edge("publish", END)

    return graph.compile()


graph = build_graph()
