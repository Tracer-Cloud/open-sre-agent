# Testing Guide

Reference for the opensre test suite: what to run, where tests live, and how to verify interactive REPL behavior.

See [AGENTS.md](AGENTS.md) for the full repo map and contribution rules.

---

## Commands

| Goal | Command |
| --- | --- |
| Scoped tests (recommended) | `make test-scope` |
| Full unit suite + coverage | `make test-cov` |
| Integration checks | `make verify-integrations` |
| Single RCA scenario | `make test-rca FILE=<fixture>` |
| All E2E scenarios | `make test-rca` or `make test-full` |
| Synthetic (no live infra) | `make test-synthetic` |

The fastest local loop is `make test-scope` (maps changed files to the minimal pytest invocation) or `make test-cov` for the full unit suite. Both skip live-infra paths (Kubernetes, EKS, chaos) that only run in CI.

> For pre-push readiness (lint, format, typecheck), see [CI.md](CI.md) — that file is the single source of truth for push/PR checks.

---

## Test Layout

Tests are organized by capability boundary, not by framework:

| Path | What it covers |
| --- | --- |
| `tests/cli/` | CLI behavior, REPL commands, smoke tests |
| `tests/cli/interactive_shell/sessions/` | Session store, `/sessions`, `/resume` |
| `tests/tools/` | Tool behavior, registry, schema validation |
| `tests/integrations/` | Integration config, verification, store, selectors |
| `tests/e2e/` | Live end-to-end scenarios against real services |
| `tests/synthetic/` | Fixture-driven RCA with no live infrastructure |
| `tests/deployment/` | Deployment validation and lifecycle |
| `tests/chaos_engineering/` | Chaos experiments |
| `tests/utils/` | Shared test utilities and fixtures |

---

## Live REPL Testing — `ReplDriver`

Some interactive shell behavior cannot be covered by unit tests with mocked consoles: rendered table layout, slash command output, session display, `/resume` confirmation messages. Use `ReplDriver` for these.

**Location:** `tests/utils/repl_driver.py`

### How it works

`ReplDriver` uses Python's built-in `pty` module to create a pseudo-terminal. The REPL process sees a real TTY (so `prompt_toolkit` starts normally and `sys.stdin.isatty()` passes), while the test controls the master end — writing commands and reading rendered output.

```
test ──write──▶  master fd  ──▶  slave fd (opensre's stdin/stdout/stderr)
test ◀──read───  master fd  ◀──  opensre renders via prompt_toolkit
```

ANSI escape codes are stripped before storing output, so assertions work on plain text.

### Basic usage

```python
from tests.utils.repl_driver import ReplDriver

def test_resume_restores_context():
    with ReplDriver() as repl:
        repl.send("/sessions", wait=3.0)
        assert repl.contains("Session ID")

        repl.send("/resume abc1234", wait=3.0)
        assert repl.contains("resumed session abc1234")
        assert repl.contains("conversation context loaded")
```

`ReplDriver` sends `/exit` automatically on `__exit__`.

### API

| Method / Property | Description |
| --- | --- |
| `ReplDriver(startup_wait=6.0)` | Create driver; `startup_wait` covers banner + event-loop startup |
| `start()` | Start the REPL process (called by `__enter__`) |
| `send(cmd, wait=2.0)` | Type a command + newline; drain output for `wait` seconds |
| `close()` | Send `/exit`, wait for process exit (called by `__exit__`) |
| `text` | Full ANSI-stripped output captured so far |
| `contains(s)` | `True` if `s` appears anywhere in `text` |
| `lines()` | Non-empty visible lines from `text` |
| `reset_output()` | Clear captured output between test phases |

### Choosing wait times

| Command type | `wait` |
| --- | --- |
| Slash commands (`/sessions`, `/resume`, `/status`) | `2.0–3.0s` |
| LLM-backed commands (avoid in automated tests) | `15–25s` |

### When to use ReplDriver

✅ Adding or changing a slash command → verify rendered output  
✅ Session management (`/sessions`, `/resume`, `/reset`) → verify display  
✅ Banner or prompt formatting changes → screenshot / string check  

### When NOT to use ReplDriver

❌ Logic testable with a mocked `Console` — keep those in `tests/cli/`  
❌ Storage / state correctness — use `tmp_path` + `SessionStore` directly  
❌ Tests that need a real LLM response — latency makes pty timing unreliable; use `make test-rca` instead  

### Two-phase pattern

For features that touch both storage and display, test each layer separately:

```python
# Phase 1 — storage correctness (fast, no REPL)
session = ReplSession()
SessionStore.open_session(session)
session.record("chat", "why is redis slow?")
SessionStore.flush(session)
data = SessionStore.load_session(session.session_id[:8])
assert data["has_snapshot"] is True

# Phase 2 — display correctness (ReplDriver)
with ReplDriver() as repl:
    repl.send(f"/resume {session.session_id[:8]}", wait=3.0)
    assert repl.contains("resumed session")
    assert repl.contains("conversation context loaded")
```

### Limitations

- `prompt_toolkit` may drop characters typed before the input loop is ready. The default `startup_wait=6.0s` covers normal startup; increase on slow machines.
- ANSI stripping is regex-based — exotic escape sequences may leave artifacts. Check `repl.text` if an assertion unexpectedly fails.
- The driver shares the host's `~/.opensre/sessions/` directory. Use a patched `_sessions_dir` in storage tests to avoid cross-test contamination.

---

## Routing Tests

Routing live tests always run with live coverage enabled. Do not use deselection filters like `-k "not live_llm"`. Fix failures by improving planner/tool correctness or updating fixtures only when behavior changes are explicitly approved.

---

## CI-Only Tests

Some paths require live infrastructure and are excluded from `make test-cov`:

- Kubernetes / EKS scenarios (`tests/e2e/`)
- Chaos Mesh workflows (`tests/chaos_engineering/`)
- Docker-dependent Grafana stack tests

Mark CI-only tests with the appropriate pytest marker or place them in the correct folder so they do not run locally by default.
