Fixes #XXXX

## What This PR Does

Introduces `app/types/messages.py` with an internal `SREMessage` TypedDict and
removes module-level `langchain_core.messages` imports from:
- `app/nodes/` (1 file changed)
- `app/services/` (0 files changed)

LangChain message types are now only present at the graph boundary (`app/pipeline/graph.py`)
and inside the deferred adapters in `app/types/messages.py`.

## Why This Matters

This is the natural continuation of #1364 (closed by #1395). That PR removed `RunnableConfig`
from node config boundaries. This PR removes the other major LangChain coupling: message types
in business logic. Together they advance the maintainers' stated goal of reducing
LangChain/LangGraph dependency pressure without a risky big-bang removal.

## What This PR Does NOT Do

- Does not remove LangGraph runtime or graph wiring.
- Does not remove `langchain-core` or `langchain-anthropic` from `pyproject.toml`.
- Does not touch `MessagesState` or LangGraph state definitions.
- Does not change any routing behavior.

## Verification

Grep showing clean boundaries:
```
$ git grep -n "from langchain_core.messages" -- app/nodes/ app/services/
(no output)
```

Full suite:
```
$ make test-cov
================ 5361 passed, 3 skipped, 115 warnings in 33.06s ================
```

## Non-Overlap Statement

- #1364: node config types — **already closed** by #1395.
- #1361 (if exists): LangChain messages — **this PR** implements the removal.
- #1365 (if exists): dependency metadata — **not this PR** (pyproject.toml untouched).
