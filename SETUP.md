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
uv run python -m platform.analytics.install
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
uv run python -m platform.analytics.install

uv run ruff check config core gateway integrations platform surfaces tools tests/
uv run ruff format --check config core gateway integrations platform surfaces tools tests/
uv run mypy config core gateway integrations platform surfaces tools

uv run pytest -n auto -v \
  --cov=config --cov=core --cov=gateway --cov=integrations \
  --cov=platform --cov=surfaces --cov=tools --cov-report=term-missing \
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

### Boot capability warnings (`curl`, `shell`, `network`, `python` gaps)

When OpenSRE boots, `boot_capability_warnings` runs lightweight runtime probes (checking system `PATH` tools and sandbox execution capabilities). If a capability is unavailable or restricted, it surfaces non-fatal boot warnings:

| Boot Warning | Root Cause | Impact | Concrete Fix / Remediation |
| :--- | :--- | :--- | :--- |
| **`curl is not on PATH`** | The `curl` binary is missing from system `PATH`. | Sandboxed tools and HTTP health probes cannot run. | **macOS:** `brew install curl`<br />**Linux:** `sudo apt-get install -y curl`<br />**Windows:** `winget install cURL.cURL` |
| **`no interactive shell (bash/sh) on PATH`** | Neither `bash` nor `sh` shell interpreter is found on `PATH`. | Subprocess execution and interactive shell actions cannot run. | **Linux:** `sudo apt-get install -y bash`<br />**macOS:** Ensure `/bin` is in `PATH`.<br />**Windows:** Install Git Bash (`winget install Git.Git`) or WSL. |
| **`network egress is blocked for sandboxed code by default`** | Outbound network access is restricted by sandbox security policy by default. Setting `OPENSRE_ALLOW_NETWORK=1` updates the boot metadata fact, but sandbox code execution still isolates raw sockets by default. | Sandboxed scripts cannot make arbitrary outbound raw socket connections. | Set `export OPENSRE_ALLOW_NETWORK=1` (or add `OPENSRE_ALLOW_NETWORK=1` in `.env`) to update runtime metadata facts. For external communications, use configured integration clients (e.g. Datadog, Slack, Prometheus) rather than raw socket scripts in sandbox. |
| **`python execution is unavailable in this environment`** | The sandbox probe failed to run test code via the running interpreter (`sys.executable`), or the OpenSRE temporary directory (`/tmp/opensre` or `%TEMP%\opensre`) is not writable. | Python-based tools and sandboxed script evaluation cannot run. | 1. Ensure the temporary directory is writable: `mkdir -p /tmp/opensre && chmod 700 /tmp/opensre`<br />2. Ensure the active Python environment is healthy and `sys.executable` can spawn subprocesses (`uv sync --frozen --extra dev` or `make install`). |
| **`shell commands is unavailable in this environment`** | Sandbox cannot spawn a shell interpreter. | Shell-based investigation tools are disabled. | Ensure `bash` or `sh` is installed and has process execution permissions. |
| **`file reading is unavailable in this environment`** | Process lacks read permission for the current working directory. | Agent cannot read local files or workspace context. | Ensure read permissions on the repository directory (`chmod -R u+r .`). |

### `opensre` does not pick up local code edits

`make install` installs this repo in **editable** mode into `.venv`, but another **`opensre`** may appear earlier on **`PATH`** (installer binary, version manager, `~/.local/bin`, etc.).

1. Prefer **`uv run opensre …`** from the repository root.
2. Or prepend the venv: `export PATH="$(pwd)/.venv/bin:$PATH"` (macOS/Linux), then **`hash -r`** / new shell, and confirm **`which opensre`** points at **`<repo>/.venv/bin/opensre`**.

---

## Verify your setup

```bash
make lint && make format-check && make typecheck && make test-cov
```

If those pass, you are ready to develop. Contribution flow: **[CONTRIBUTING.md](CONTRIBUTING.md)**. Deeper contributor topics (benchmark, deployment, telemetry detail): **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**.

---

## Connecting OpenClaw

OpenSRE no longer exposes a separate `opensre-mcp` server. Instead, OpenSRE connects to the OpenClaw bridge directly to read recent conversation context and write RCA findings back into OpenClaw.

### 1. Configure observability

Run the full wizard once (**recommended**):

```bash
uv run opensre onboard
```

To add or reconfigure a **single** integration non-interactively:

```bash
uv run opensre integrations setup <service>
```

### 2. Configure the OpenClaw bridge

Use the wizard or the direct setup flow:

```bash
uv run opensre integrations setup openclaw
uv run opensre integrations verify openclaw
```

Recommended local settings:

```bash
OPENCLAW_MCP_MODE=stdio
OPENCLAW_MCP_COMMAND=openclaw
OPENCLAW_MCP_ARGS="mcp serve"
```

### 3. Run a test

```bash
uv run opensre investigate -i tests/fixtures/openclaw_test_alert.json
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
