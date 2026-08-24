"""Cluster and rank tool pairs within an integration by description similarity.

Used for identifying confusable sibling tool pairs that require explicit
disambiguation in their descriptions.
"""

from __future__ import annotations

import argparse
import importlib
import itertools
import json
import pkgutil
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    module_path: str
    source: str


@dataclass(frozen=True)
class PairSimilarity:
    tool_a: str
    tool_b: str
    shared_tokens: list[str]
    shared_token_count: int
    jaccard_similarity: float
    description_a: str
    description_b: str


def tokenize(text: str) -> set[str]:
    """Extract normalized alphanumeric word tokens from text, ignoring common stop words."""
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "with",
    }
    words = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    return {w for w in words if len(w) > 1 and w not in stopwords}


def compute_jaccard(tokens_a: set[str], tokens_b: set[str]) -> float:
    """Compute Jaccard similarity coefficient between two token sets."""
    if not tokens_a and not tokens_b:
        return 0.0
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    return len(tokens_a & tokens_b) / len(union)


def discover_integration_tools(integration_name: str) -> list[ToolInfo]:
    """Discover all registered tools under integrations/<integration_name>/tools."""
    tools_dir = Path("integrations") / integration_name / "tools"
    if not tools_dir.exists() or not tools_dir.is_dir():
        print(f"Directory not found: {tools_dir}", file=sys.stderr)
        return []

    tools: list[ToolInfo] = []
    package_path = f"integrations.{integration_name}.tools"

    # Walk subpackages/modules in the tools directory
    for _, mod_name, _ in pkgutil.iter_modules([str(tools_dir)]):
        full_mod_path = f"{package_path}.{mod_name}"
        try:
            mod = importlib.import_module(full_mod_path)
        except Exception as err:
            print(f"Warning: could not import {full_mod_path}: {err}", file=sys.stderr)
            continue

        # Look for registered tools or BaseTool instances in module
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            reg_tool: Any = getattr(attr, "__opensre_registered_tool__", None)
            if reg_tool is not None:
                tools.append(
                    ToolInfo(
                        name=reg_tool.name,
                        description=reg_tool.description,
                        module_path=full_mod_path,
                        source=getattr(reg_tool, "source", integration_name),
                    )
                )
            elif hasattr(attr, "name") and hasattr(attr, "description") and hasattr(attr, "run"):
                # BaseTool-like instance or class
                if isinstance(attr, type):
                    continue
                tools.append(
                    ToolInfo(
                        name=attr.name,
                        description=attr.description,
                        module_path=full_mod_path,
                        source=getattr(attr, "source", integration_name),
                    )
                )

    # Deduplicate by name
    unique_tools: dict[str, ToolInfo] = {}
    for t in tools:
        if t.name not in unique_tools:
            unique_tools[t.name] = t

    return list(unique_tools.values())


def analyze_integration_similarity(
    tools: list[ToolInfo],
) -> list[PairSimilarity]:
    """Rank all pairwise combinations of tools by token overlap and Jaccard similarity."""
    pairs: list[PairSimilarity] = []

    for tool_a, tool_b in itertools.combinations(tools, 2):
        tokens_a = tokenize(tool_a.description)
        tokens_b = tokenize(tool_b.description)
        shared = sorted(tokens_a & tokens_b)
        jaccard = compute_jaccard(tokens_a, tokens_b)
        pairs.append(
            PairSimilarity(
                tool_a=tool_a.name,
                tool_b=tool_b.name,
                shared_tokens=shared,
                shared_token_count=len(shared),
                jaccard_similarity=round(jaccard, 4),
                description_a=tool_a.description,
                description_b=tool_b.description,
            )
        )

    # Sort primarily by Jaccard similarity, secondarily by shared token count
    pairs.sort(key=lambda p: (p.jaccard_similarity, p.shared_token_count), reverse=True)
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute tool description similarity clusters for an integration."
    )
    parser.add_argument(
        "--integration",
        default="s3",
        help="Integration name under integrations/ (default: s3)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON results",
    )
    args = parser.parse_args()

    tools = discover_integration_tools(args.integration)
    if not tools:
        print(f"No tools discovered for integration '{args.integration}'")
        sys.exit(1)

    print(f"Discovered {len(tools)} tools for integration '{args.integration}':")
    for t in tools:
        print(f"  - {t.name}: {t.description}")
    print()

    pairs = analyze_integration_similarity(tools)

    if args.json:
        print(json.dumps([asdict(p) for p in pairs], indent=2))
        return

    print("=== Pairwise Similarity Ranking (Jaccard + Shared Tokens) ===")
    for rank, p in enumerate(pairs, 1):
        print(f"#{rank} {p.tool_a} <-> {p.tool_b}")
        print(f"   Jaccard Similarity: {p.jaccard_similarity:.4f}")
        print(f"   Shared Tokens ({p.shared_token_count}): {', '.join(p.shared_tokens)}")
        print(f"   Tool A description: {p.description_a}")
        print(f"   Tool B description: {p.description_b}")
        print()


if __name__ == "__main__":
    main()
