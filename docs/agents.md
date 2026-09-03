---
name: opensre-cli-setup
description: Set up and maintain the OpenSRE CLI (install, setup, verify, first run)
user-invocable: true
allowed-tools: Bash(command:*), Bash(brew:*), Bash(curl:*), Bash(opensre:*)
metadata:
  author: opensre
---

# OpenSRE CLI Setup

Set up or maintain `opensre` with minimal friction. If you are running this prompt, follow the steps below and set up OpenSRE for the user so they can ask about their infrastructure from the interactive shell.

Do not clone the OpenSRE repo unless the user asked to contribute. The published CLI is enough.

## Step 1: Install or upgrade the CLI

Check whether the CLI exists:

```bash
command -v opensre
opensre --version
```

On Windows PowerShell:

```powershell
Get-Command opensre
opensre --version
```

If `opensre` is missing, install with one of these methods. Prefer the one-line installer unless the user already uses Homebrew.

1. macOS / Linux (one-line installer):

```bash
curl -fsSL https://install.opensre.com | OPENSRE_AUTO_LAUNCH=0 bash
```

`OPENSRE_AUTO_LAUNCH=0` skips auto-starting setup so you can run the remaining steps yourself.

2. macOS (Homebrew):

```bash
brew tap tracer-cloud/tap
brew install tracer-cloud/tap/opensre
```

3. Windows (PowerShell):

```powershell
irm https://install.opensre.com | iex
```

No sudo on macOS/Linux in the usual case. If the installer cannot use a writable directory already on `PATH`, it puts the binary in `~/.local/bin` and prints the command to add that directory. Apply that PATH update (or open a new terminal), then re-check `command -v opensre`.

When `opensre` is already present, upgrade it:

```bash
opensre update
```

Homebrew installs can also use:

```bash
brew update && brew upgrade tracer-cloud/tap/opensre
```

Confirm it runs:

```bash
opensre --help
```

## Step 2: Setup

Setup is interactive and needs a TTY. Do not try to fake the wizard. Run it and prompt the user when it asks for input:

```bash
opensre setup
```

The wizard asks for:

1. **GitHub sign-in** — browser device authorization (required).
2. **An LLM** — OpenAI, Anthropic, a local model (Ollama), or another provider they already use. They need an API key, or they can leave the key blank and add it later with `opensre auth login <provider>`.

When setup finishes, it opens the interactive shell. Add tools later with `opensre integrations setup <service>` when the agent should query them.

Zero-config local LLM (Ollama, no API key):

```bash
opensre onboard local_llm
```

To change only the LLM later (without GitHub again):

```bash
opensre onboard
```

Or:

```bash
opensre auth login
```

To connect one tool later:

```bash
opensre integrations setup <service>
```

Replace `<service>` with a slug such as `datadog`, `grafana`, or `slack`.

If setup cannot reach the LLM provider (firewall, proxy, offline), tell the user they can **Save anyway without validating** and continue. If it cannot persist the key, they can **Continue without saving (this session only)** and re-enter it next time.

## Step 3: Verify

```bash
opensre integrations verify
```

One tool only:

```bash
opensre integrations verify datadog
```

If verify fails, check the printed error. Common causes: missing or expired credential, wrong URL, or a skipped setup step. Re-run `opensre integrations setup <service>` for that tool.

## Step 4: Suggest a first run

Open the interactive shell:

```bash
opensre
```

That starts a TTY REPL. Ask the user to describe an incident or ask a question in plain language, or use `/help`.

## Gotchas

- **`opensre: command not found`** — new terminal, or add the bin directory the installer printed (often `~/.local/bin` on macOS/Linux).
- **Setup blocks or looks hung** — it is waiting on the user (GitHub browser approval or LLM key). Show them the prompt; do not kill it.
- **Installer started setup on its own** — that is expected without `OPENSRE_AUTO_LAUNCH=0`. Let the user finish it, then continue from Step 3.
- **The agent needs a configured LLM** — the shell cannot answer without `LLM_PROVIDER` and a matching key. Finish `opensre setup` / `opensre onboard` or `opensre auth login <provider>` first.
- **Only connected tools are queried** — the agent cannot pull Datadog data if Datadog was never set up. Run `opensre integrations verify` before a production run.

Human docs: https://opensre.com/docs/install and https://opensre.com/docs/quickstart
