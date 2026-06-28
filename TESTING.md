# Live REPL Testing — `ReplDriver`

Some interactive shell behavior cannot be covered by unit tests with mocked consoles: rendered table layout, slash command output, session display, `/resume` confirmation messages. Use `ReplDriver` for these.

**Location:** `tests/utils/repl_driver.py`

For test commands and CI checks, see [CI.md](CI.md).

---

## How it works

`ReplDriver` uses Python's built-in `pty` module to create a pseudo-terminal. The REPL process sees a real TTY (so `prompt_toolkit` starts normally and `sys.stdin.isatty()` passes), while the test controls the master end — writing commands and reading rendered output.

```
test ──write──▶  master fd  ──▶  slave fd (opensre's stdin/stdout/stderr)
test ◀──read───  master fd  ◀──  opensre renders via prompt_toolkit
```

ANSI escape codes are stripped before storing output, so assertions work on plain text.

## Basic usage

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

## API

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

## Choosing wait times

| Command type | `wait` |
| --- | --- |
| Slash commands (`/sessions`, `/resume`, `/status`) | `2.0–3.0s` |
| LLM-backed commands (avoid in automated tests) | `15–25s` |

## When to use

✅ Adding or changing a slash command → verify rendered output  
✅ Session management (`/sessions`, `/resume`, `/reset`) → verify display  
✅ Banner or prompt formatting changes → screenshot / string check  

## When NOT to use

❌ Logic testable with a mocked `Console` — keep those in `tests/cli/`  
❌ Storage / state correctness — use `tmp_path` + a session storage/repo backend directly  
❌ Tests that need a real LLM response — latency makes pty timing unreliable; use `make test-rca` instead  

## Two-phase pattern

For features that touch both storage and display, test each layer separately:

```python
# Phase 1 — storage correctness (fast, no REPL)
from interactive_shell.session import (
    JsonlSessionStorage,
    ReplSession,
    default_session_repo,
)

storage = JsonlSessionStorage()
session = ReplSession(storage=storage)
storage.open_session(session)
session.record("chat", "why is redis slow?")
storage.flush(session)
data = default_session_repo().load_session(session.session_id[:8])
assert data["has_snapshot"] is True

# Phase 2 — display correctness (ReplDriver)
with ReplDriver() as repl:
    repl.send(f"/resume {session.session_id[:8]}", wait=3.0)
    assert repl.contains("resumed session")
    assert repl.contains("conversation context loaded")
```

## Limitations

- `prompt_toolkit` may drop characters typed before the input loop is ready. The default `startup_wait=6.0s` covers normal startup; increase on slow machines.
- ANSI stripping is regex-based — exotic escape sequences may leave artifacts. Check `repl.text` if an assertion unexpectedly fails.
- The driver shares the host's `~/.opensre/sessions/` directory. Use a patched `_sessions_dir` in storage tests to avoid cross-test contamination.
