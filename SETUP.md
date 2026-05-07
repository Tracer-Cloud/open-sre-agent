# Development Environment Setup

## Prerequisites

- Python 3.11 or later (see `.python-version` / `pyproject.toml`; CI uses 3.13)
- Git
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — required for `make install` (creates `.venv`, installs locked deps from `uv.lock`)
- Make (standard on macOS/Linux; see Windows section below)

## Quick Setup (All Platforms)

1. Fork and clone the repo:

```bash
 git clone https://github.com/YOUR_USERNAME/opensre.git
 cd opensre
```

2. Install uv if you do not have it yet (pick one):

- **macOS/Linux:** `curl -LsSf https://astral.sh/uv/install.sh | sh` (or follow the [install guide](https://docs.astral.sh/uv/getting-started/installation/))
- **Windows (PowerShell):** `irm https://astral.sh/uv/install.ps1 | iex`  
  Or: `winget install --id astral-sh.uv -e`

3. Install dependencies (uses the committed lockfile):

```bash
 make install
```

Without Make (equivalent):

```bash
 uv sync --frozen --extra dev
 uv run python -m app.analytics.install
```

4. Verify setup by running checks:

```bash
 make lint && make typecheck && make test-cov
```

All three must pass before you're ready to develop.

---

## VS Code Dev Container Setup

If you use VS Code, you can skip the manual Python setup and use the repo's devcontainer instead:

1. Install the **Dev Containers** extension in VS Code.
2. Start Docker Desktop, OrbStack, Colima, or another Docker-compatible runtime on your host machine.
3. Open the repository in VS Code and run `Dev Containers: Reopen in Container`.
4. Wait for the container's `postCreateCommand` to install `.[dev]`.
5. Run the usual checks:

```bash
 make lint && make typecheck && make test-cov
```

The devcontainer uses Python 3.13 to match CI and `.tool-versions`. Manual host-based setup continues to work with any supported Python version (`>=3.11`).

---

## Windows-Specific Setup

Windows does not include `make` by default. Install it to use our development task runner.

### Option A: Chocolatey (Recommended)

1. Open PowerShell as Administrator

- Search "PowerShell" in Start Menu
- Right-click → "Run as administrator"

2. Install Chocolatey (review the script first):

```powershell
 Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
```

3. Install make:

```powershell
 choco install make
```

4. Restart your terminal and verify:

```bash
 make --version
```

### Option B: winget

If you prefer winget (Windows Package Manager):

```powershell
winget install GnuWin32.Make
```

Restart your terminal and verify:

```bash
make --version
```

### Option C: Manual Commands (No make required)

If you can't install make, you can run these approximate equivalents directly instead (they are close to, but not always identical to, the Makefile targets; see comments for differences). Use the same shell where `uv` is on your `PATH` and run commands from the repo root:

```bash
# One-time / refresh deps (same as `make install` without analytics)
uv sync --frozen --extra dev
uv run python -m app.analytics.install

# Linting (rough equivalent of `make lint`; this also applies auto-fixes via --fix)
uv run python -m ruff check app/ tests/ --fix

# Type checking (equivalent of `make typecheck`)
uv run mypy app/

# Tests with coverage (rough equivalent of `make test-cov`; the Makefile version may add --cov-report/--ignore flags)
uv run pytest --cov=app tests/
```

---

## Troubleshooting

### Virtual environment not activating

- **macOS/Linux:** Make sure you ran `source .venv/bin/activate` (uv creates `.venv` under the project root)
- **Windows:** Use `.venv\Scripts\activate` instead

### Command not found: python

- Make sure Python 3.11+ is installed and in your PATH
- Verify with: `python --version`

### Command not found: uv

- Install uv using the links in [Prerequisites](#prerequisites) or [uv’s installation guide](https://docs.astral.sh/uv/getting-started/installation/)
- Restart the terminal so your `PATH` picks up the binary

### `make install` / `uv sync` fails

- Ensure you are in the repository root and `uv.lock` is present (it should be in git)
- Upgrade uv: `uv self update`
- If the lockfile is out of date with `pyproject.toml`, run `uv lock` locally and commit the updated `uv.lock` (or open a PR) rather than editing constraints by hand

### make: command not found (Windows)

- See Windows-Specific Setup section above
- Or use Option C (manual commands)

### Import errors when running code

- Make sure you've activated the virtual environment, or prefix commands with `uv run`
- Reinstall dependencies: `uv sync --frozen --extra dev`

### `opensre` does not pick up my local code edits

`make install` installs this repo into `.venv` in **editable** mode, so Python changes apply immediately — but your shell might still run a **different** `opensre` first on `PATH` (release binary from `install.sh`, a shim from **asdf** / another version manager, `~/.local/bin`, and so on).

1. Prefer **`uv run opensre …`** from the repository root (always uses this project’s environment and sources).
2. Or run **`eval "$(./scripts/dev-path.sh)"`** from the repo root, then **`hash -r`** — same `PATH` prepend, works from any cwd if you use the script’s absolute path.
3. Or activate `.venv` and confirm **`which opensre`** shows `<repo>/.venv/bin/opensre`. If not, prepend manually: `export PATH="$(pwd)/.venv/bin:$PATH"` (macOS/Linux), then **`hash -r`** or open a new terminal.

---

## Verify Your Setup

Run this to confirm everything is working:

```bash
make lint && make typecheck && make test-cov
```

If all three pass, you're ready to start developing! See `CONTRIBUTING.md` for the development workflow.

---

## Running OpenSRE MCP Server

You can start the MCP server with:

```bash
opensre-mcp
```

This exposes the `run_rca` tool for MCP clients.

---

## Connecting OpenClaw

Use OpenClaw to call OpenSRE's `run_rca` tool.

### 1. Add OpenSRE to OpenClaw

In OpenClaw, open **Settings → MCP Servers** and add:

```json
{
    "mcpServers": {
        "opensre": {
            "command": "opensre-mcp",
            "args": []
        }
    }
}
```

If `opensre-mcp` is not on your `PATH`, use the full path:

```json
{ "command": "/path/to/venv/bin/opensre-mcp" }
```

### 2. Configure one observability integration

Run the setup wizard once and connect Datadog, Grafana, Sentry, or another backend:

```bash
opensre integrations setup
```

### 3. Run a test

Run the fixture directly from the CLI:

```bash
opensre investigate -i tests/fixtures/openclaw_test_alert.json
```

### 4. Optional: let OpenSRE call OpenClaw

If you want the OpenSRE investigation pipeline to query OpenClaw during RCA runs:

```bash
export OPENCLAW_MCP_MODE=stdio
export OPENCLAW_MCP_COMMAND=openclaw
export OPENCLAW_MCP_ARGS="mcp serve"
```

Keep the OpenClaw Gateway running while you investigate:

```bash
openclaw gateway run
```

Verify:

```bash
opensre integrations verify openclaw
```
