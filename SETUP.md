# Development environment setup

## Prerequisites

- **Python 3.12+** — required by [`pyproject.toml`](pyproject.toml) (`requires-python = ">=3.12"`). CI workflows use **Python 3.13** (see [`.github/workflows/ci.yml`](.github/workflows/ci.yml)). [`.tool-versions`](.tool-versions) pins Python **3.13**, **uv**, **ruff**, and **mypy** (versions aligned with [`uv.lock`](uv.lock) where applicable) plus Node/pnpm for mise/asdf-style managers — optional; normal flows install **ruff** and **mypy** into `.venv` via **`make install`** / **`uv sync`**.
- Git
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — required for `make install` (locked deps from `uv.lock`)
- **Make** — standard on macOS/Linux; Windows options below

## Quick setup (all platforms)

1. Fork and clone:

```bash
git clone https://github.com/YOUR_USERNAME/opensre.git
cd opensre
```

2. Install uv if needed:

- **macOS/Linux:** `curl -LsSf https://astral.sh/uv/install.sh | sh` (or the [uv install guide](https://docs.astral.sh/uv/getting-started/installation/))
- **Windows (PowerShell):** `irm https://astral.sh/uv/install.ps1 | iex`  
  Or: `winget install --id astral-sh.uv -e`

3. Install dependencies:

```bash
make install
```

Without Make (equivalent to `make install`):

```bash
uv sync --frozen --extra dev
uv run python -m app.analytics.install
```

4. Verify:

```bash
make lint && make format-check && make typecheck && make test-cov
```

`format-check` is what CI enforces for formatting; include it before opening a PR.

---

## VS Code dev container

1. Install the **Dev Containers** extension in VS Code.
2. Start Docker Desktop, OrbStack, Colima, or another Docker-compatible runtime on the host.
3. Open the repository and run **Dev Containers: Reopen in Container**.

The image is built from [`.devcontainer/Dockerfile`](.devcontainer/Dockerfile) (**Python 3.13**). **`postCreateCommand`** creates `.venv-devcontainer` and runs **`pip install -e '.[dev]'`** (not `uv`). The interpreter VS Code uses is `.venv-devcontainer/bin/python`.

On the host, most contributors use **`make install`** + **`uv run`** instead; both approaches are valid.

---

## Windows-specific setup

Windows does not ship **make**. Pick one path below.

### Option A: Chocolatey (recommended)

1. Open PowerShell **as Administrator**.
2. Install Chocolatey (review the script first):

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
```

3. Install make:

```powershell
choco install make
```

4. Restart the terminal and verify: `make --version`.

### Option B: winget

```powershell
winget install GnuWin32.Make
```

Restart the terminal, then `make --version`.

### Option C: No Make

Run equivalents from the repo root (same shell where `uv` is on `PATH`). Prefer **`make test-cov`** when possible — the full pytest line is in the [`Makefile`](Makefile) under the `test-cov` target (`pytest -n auto`, coverage, and ignores).

```bash
uv sync --frozen --extra dev
uv run python -m app.analytics.install

uv run ruff check app/ tests/
uv run ruff format --check app/ tests/
uv run mypy app/

uv run pytest -n auto -v --cov=app --cov-report=term-missing \
  --ignore=tests/e2e/kubernetes_local_alert_simulation \
  --ignore=tests/synthetic \
  -m "not synthetic"
```

---

## Troubleshooting

### Commands not using the project environment

- Prefer **`uv run <command>`** from the repo root.
- Refresh deps: **`uv sync --frozen --extra dev`**.

### Command not found: python

- Install Python **3.12+** and ensure it is on `PATH` (`python --version`).

### Command not found: uv

- Install uv (links above), then restart the terminal.

### `make install` / `uv sync` fails

- Run commands from the repository root; ensure **`uv.lock`** is present.
- Upgrade uv: **`uv self update`**.
- If the lockfile does not match **`pyproject.toml`**, run **`uv lock`** locally and commit the updated lockfile (or open a PR).

### make: command not found (Windows)

- Install make (above) or use Option C.

### Import errors when running code

- Use **`uv run`** from the repo root.
- Re-run **`uv sync --frozen --extra dev`**.

### `opensre` does not pick up local code edits

`make install` installs this repo in **editable** mode into `.venv`, but another **`opensre`** may appear earlier on **`PATH`** (installer binary, version manager, `~/.local/bin`, etc.).

1. Prefer **`uv run opensre …`** from the repository root.
2. Or run **`eval "$(./scripts/dev-path.sh)"`** then **`hash -r`** (see script for behavior).
3. Or prepend the venv: `export PATH="$(pwd)/.venv/bin:$PATH"` (macOS/Linux), then **`hash -r`** / new shell, and confirm **`which opensre`** points at **`<repo>/.venv/bin/opensre`**.

---

## Verify your setup

```bash
make lint && make format-check && make typecheck && make test-cov
```

If those pass, you are ready to develop. Contribution flow: **[CONTRIBUTING.md](CONTRIBUTING.md)**. Deeper contributor topics (benchmark, deployment, telemetry detail): **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**.

---

## Running the OpenSRE MCP server

```bash
opensre-mcp
# or from a checkout:
uv run opensre-mcp
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

If `opensre-mcp` is not on your `PATH`:

```json
{ "command": "uv", "args": ["run", "opensre-mcp"] }
```

(use `uv` from the repo root / env where OpenSRE is installed).

### 2. Configure observability

Run the full wizard once (**recommended**):

```bash
opensre onboard
```

To add or reconfigure a **single** integration non-interactively:

```bash
opensre integrations setup <service>
```

### 3. Run a test

```bash
opensre investigate -i tests/fixtures/openclaw_test_alert.json
```

### 4. Optional: OpenSRE calls OpenClaw during RCA

```bash
export OPENCLAW_MCP_MODE=stdio
export OPENCLAW_MCP_COMMAND=openclaw
export OPENCLAW_MCP_ARGS="mcp serve"
```

Keep the OpenClaw gateway running while you investigate, then verify:

```bash
opensre integrations verify openclaw
```
