# OpenSRE CLI / REPL product audit

**Date:** 2026-05-20  
**Branch:** `docs/e2e-cli-repl-audit`  
**Scope:** Practical end-to-end product testing (not unit tests). Synthetic RCA runs excluded per review request.

## Environment

| Check | Result |
|-------|--------|
| Python | 3.13.11 |
| LLM | `codex` (doctor: CLI ready) |
| `.env` | Present (9 keys) |
| `integrations.json` | Not configured |
| Run command | `uv run opensre …` from repo root |

## Summary

| Surface | Working | Issues |
|---------|---------|--------|
| Top-level CLI | Most read-only commands | `health` exits 1 when integrations missing |
| REPL slash (in-process) | Help, status, doctor, agents, session cmds | — |
| REPL slash (CLI-delegated) | Commands run but output often invisible | **Primary bug:** subprocess stdout not replayed into REPL |

Artifacts in this directory:

- `REPORT.md` — this document
- `functional_audit.json` — machine-readable results
- `functional_audit.py` — reproducible audit harness

Re-run:

```bash
OPENSRE_NO_TELEMETRY=1 uv run python docs/e2e-product-audit/functional_audit.py
```

---

## Working as expected

### Top-level CLI

| Command | Functional check |
|---------|------------------|
| `opensre version` | Prints version, Python, OS |
| `opensre doctor` | Full diagnostic table; LLM shows codex ready |
| `opensre config show` | Shows config path + YAML |
| `opensre integrations list` | Clear empty-state message |
| `opensre agents list` / `agents scan` | Table + scan output |
| `opensre tests list` | Full test inventory |
| `opensre guardrails rules` | “No guardrails config” message |
| `opensre messaging status --platform telegram` | Status output |
| `opensre watchdog --help` | Usage text |
| `opensre remote health` | Fails with timeout when remote unreachable (expected if URL configured) |

### REPL slash (in-process handlers)

| Command | Functional check |
|---------|------------------|
| `/help`, `/?` | Command index renders |
| `/status`, `/version`, `/doctor`, `/health` | Session / version / doctor / health tables |
| `/integrations`, `/agents`, `/list` | Empty-state + agents/MCP listing |
| `/trust on`, `/reset`, `/tasks`, `/watches` | Trust on, session cleared, task/watch lists |
| `/onboard` | Correct handoff: exit REPL and run `opensre onboard` |
| `/privacy` | Privacy settings table |
| `/template <type>` | JSON for `generic`, `datadog`, `grafana`, etc. |
| `/clear`, `/compact`, `/cost`, `/context`, `/history`, `/stop`, `/last` | Meta/session commands respond |

---

## Broken or not working as documented

### 1. REPL: CLI-delegated slash commands swallow output (primary UI bug)

**Affected:** `/tests list`, `/guardrails`, `/guardrails rules`, `/config`, `/messaging`, `/remote`, `/debug`, `/hermes`, `/watchdog`, `/update`, `/uninstall`, and parts of `/integrations`.

**Expected:** Same output as `opensre <cmd>` in a normal terminal.

**Actual:** Child CLI runs without captured stdout. The REPL often shows blank lines or only `CLI command exited with non-zero code N`. Output may appear on the raw terminal or be lost under `patch_stdout`.

**Root cause:** `run_cli_command()` in `app/cli/interactive_shell/command_registry/cli_parity.py` only replays output when `subprocess_timeout` is set (capture mode). Most parity commands use the non-capture path:

```python
interactive_result = subprocess.run(cmd, check=False)
if interactive_result.returncode != 0:
    console.print(f"CLI command exited with non-zero code {interactive_result.returncode}")
```

**Severity:** High — commands look broken in the REPL even when the CLI works.

**Suggested fix:** Capture stdout/stderr for non-interactive delegations (`list`, `rules`, `show`, etc.) and replay via `print_command_output`, same as timed subprocess path.

---

### 2. REPL: `/guardrails` with no subcommand

**Expected:** Usage or help (like `opensre guardrails --help`).

**Actual:** `CLI command exited with non-zero code 2` with no guardrails text in the REPL panel.

**Workaround:** Run `opensre guardrails rules` outside the REPL.

---

### 3. REPL: `/remote` without args (non-TTY / scripted)

**Expected:** Clear error or usage when no URL.

**Actual:** Can hang on an interactive remote picker (`Warning: Input is not a terminal`) or block on a configured dead URL (e.g. connection timeout to `http://10.0.0.1:2024`).

**Severity:** Medium — bad for automation; awkward in REPL with stale remote config.

---

### 4. REPL: `/model` misleading when settings load fails

**Expected:** Show active provider (codex per doctor).

**Actual (observed in harness):** `LLM settings unavailable — check provider env vars.` while `opensre doctor` reports codex OK. `/model` uses `LLMSettings.from_env()` in-process; failure message is vague.

**Note:** Normal `opensre` startup loads `.env` in `app/cli/__main__.py`; live TTY REPL may behave correctly — confirm manually.

---

### 5. CLI: `opensre health` exit code 1 with all integrations missing

**Expected (user mental model):** “Health check completed.”

**Actual:** Renders the full table, then **exits 1** because every integration is `MISSING` (0 passed, 39 missing).

**Severity:** Low — works as coded; confusing for scripts/CI.

**Suggested fix:** Exit 0 when the report rendered and failures are only `missing` (not `failed`), or document exit semantics in `--help`.

---

## Audit false positives (not product bugs)

| Item | Explanation |
|------|-------------|
| `/template` with no args | Correct usage message |
| `opensre investigate --yes` | Harness used invalid flag; investigate has no `--yes` |
| Synthetic `tests run` | Excluded from scope; reported fine separately |
| No integrations | Empty states are correct |

---

## Not fully validated in this pass

| Command | Reason |
|---------|--------|
| `opensre investigate -i <fixture>` | Long-running; not run to completion |
| `/investigate <file>` | Same; should use codex when `.env` loaded via `opensre` |
| `/watch`, `/unwatch` | Need trust + Telegram creds for full alarm path |
| Interactive `/help` menu | Arrow-key UI not exercised in non-TTY audit |
| `opensre` (no args) REPL banner | Welcome panel only checked via `--help` |

---

## Priority fix list

1. Capture and replay stdout/stderr for non-interactive CLI delegations in REPL.
2. Default `/guardrails` to show Click help in the REPL buffer.
3. `/remote` without TTY: fail fast with usage instead of interactive picker hang.
4. Clarify or adjust `health` exit code when only integrations are missing.

---

## JSON summary (from `functional_audit.json`)

```json
{
  "total": 34,
  "passed": 28,
  "failed": 6
}
```

Failed harness checks (see JSON for detail):

- `cli health` — exit 1 (missing integrations)
- `cli investigate -i fixture` — invalid `--yes` flag in harness
- `cli tests run synthetic:000-healthy` — excluded from product scope
- `repl /tests list` — empty buffer (subprocess capture bug)
- `repl /template` — no args (usage only; not a bug)
- `repl /guardrails` — exit 2, no help text in buffer
