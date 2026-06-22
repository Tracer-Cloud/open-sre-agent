"""Diagnose node — parse investigation conclusions into structured RCA fields."""

from app.agent.nodes.diagnose.node import InvestigationResult, parse_diagnosis

__all__ = ["InvestigationResult", "parse_diagnosis"]
