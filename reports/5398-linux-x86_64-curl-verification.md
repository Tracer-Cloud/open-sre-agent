# Verification Report: OpenSRE on Linux x86_64 via curl installer

**Issue:** [#5398](https://github.com/Tracer-Cloud/opensre/issues/5398) — Verify OpenSRE on Linux x86_64 via curl installer  
**Date:** 2026-08-31  
**Verification Result:** All checklist items PASSED.

---

## 1. Environment Details

| Property | Value |
| :--- | :--- |
| **Operating System** | Linux 6.18.33.2-microsoft-standard-WSL2 #1 SMP PREEMPT_DYNAMIC Thu Jun 18 21:54:43 UTC 2026 |
| **OS Distribution** | Ubuntu 26.04 LTS (Resolute Raccoon) |
| **Architecture** | `x86_64` (`linux-x64`) |
| **Install Method** | `curl -fsSL https://install.opensre.com \| bash` (standalone installer) |
| **OpenSRE Version** | `0.1.2026.8.31+main.8a7a871` |
| **Install Path** | `/home/d/.local/bin/opensre` |
| **System Python** | Python 3.14.4 |
| **Runtime Python** | Python 3.13.15 (bundled) |

---

## 2. Verification Checklist Summary

- [x] **1. Installer & Version:** `curl -fsSL https://install.opensre.com | bash` installs cleanly and `opensre --version` reports version `0.1.2026.8.31+main.8a7a871`.
- [x] **2. Doctor Diagnostics:** `opensre doctor` executes cleanly without hard-failing on PATH and outputs structured system checks.
- [x] **3. Onboard Wizard:** `opensre onboard --help` displays help and `opensre onboard` launches the interactive LLM provider onboarding wizard.
- [x] **4. Non-TTY Landing Page:** `opensre` with non-TTY stdin/stdout cleanly prints the documented landing page and CLI guidance.
- [x] **5. Interactive Shell & Slash Commands:** Interactive shell opens on TTY, and slash commands `/status` and `/help` execute correctly.
- [x] **6. Offline Fixture Gate:** `opensre investigate -i tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json` parses the fixture and reaches the credential gate with clean error reporting.
- [x] **7. Gateway Daemon:** `opensre gateway start`, `status`, and `stop` manage the background daemon lifecycle smoothly.

---

## 3. Step-by-Step Execution Logs

### Step 1: Installer Execution & Version Check

**Command:**
```bash
curl -fsSL https://install.opensre.com | bash
opensre --version
```

**Terminal Output:**
```text
Downloading OpenSRE main build for linux-x64...
Fetching and verifying checksum...
Checksum verification passed
Extracting OpenSRE...
Locating opensre binary...
Found opensre binary, verifying it runs...
Installing OpenSRE...
OpenSRE v2026.8.31 installed successfully to /home/d/.local/bin/opensre
Checking PATH configuration...
PATH configured in /home/d/.bashrc
Warning: GitHub CLI (gh) is missing and apt is available but auto-install needs sudo.
Warning: Install manually: apt install gh  (or https://cli.github.com/) for OpenSRE GitHub chat tools.
Run 'opensre' to get started!

$ opensre --version
opensre, version 0.1.2026.8.31+main.8a7a871
```

---

### Step 2: System Health Diagnostics (`opensre doctor`)

**Command:**
```bash
opensre doctor
```

**Terminal Output:**
```text
────────────────────────────────────────────────────────────────────────────────
  OpenSRE Doctor
────────────────────────────────────────────────────────────────────────────────

  ✓  python            Python 3.13.15
  ⚠  env_file          .env not found
  ⚠  llm_provider      provider=anthropic, auth missing (ANTHROPIC_API_KEY is not configured.)
  ⚠  integrations      /home/d/.opensre/integrations.json not found — run 'opensre integrations setup'
  ⚠  agent_capabilitiesnetwork requests is unavailable in this environment (probe returned unavailable) — the agent will not be able to use it. Fix: use a configured integration for outbound HTTP; raw sandbox network access is blocked by default.
  ✓  buzz_cli          not configured (optional)
  ✓  version           0.1.2026.8.31+main.8a7a871 (editable install; skipped comparing to latest main build)

────────────────────────────────────────────────────────────────────────────────
  4 warnings   —   fix and rerun opensre doctor
```

---

### Step 3: Onboarding Wizard Help & Interactive Prompt

**Commands:**
```bash
opensre onboard --help
opensre onboard
```

**Terminal Output (`opensre onboard --help`):**
```text
Usage: opensre onboard [OPTIONS] [COMMAND] [ARGS]...

  Run the interactive onboarding wizard.

Options:
  -h, --help  Show this message and exit.

Commands:
  local_llm  Zero-config local LLM setup via Ollama.
```

**Terminal Output (`opensre onboard`):**
```text
────────────────────────────────────────────────────────────────────────────────

   ██████╗ ██████╗ ███████╗███╗   ██╗███████╗██████╗ ███████╗
  ██╔═══██╗██╔══██╗██╔════╝████╗  ██║██╔════╝██╔══██╗██╔════╝
  ██║   ██║██████╔╝█████╗  ██╔██╗ ██║███████╗██████╔╝█████╗
  ██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║╚════██║██╔══██╗██╔══╝
  ╚██████╔╝██║     ███████╗██║ ╚████║███████║██║  ██║███████╗
   ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝╚══════╝

  opensre  ·  v0.1.2026.8.31+main.8a7a871
  open-source SRE agent for automated incident investigation and root cause analysis

────────────────────────────────────────────────────────────────────────────────

╭──────────────────────────────────────────────────────────────────────────────╮
│                                                                              │
│  Complete your setup to get started                                          │
│                                                                              │
│  [1] Select your LLM provider and add its API key or CLI login.              │
│  [2] OpenSRE checks the connection and continues.                            │
│                                                                              │
╰──────────────────────────────────────────────────────────────────────────────╯


────────────────────────────────────────────────────────────────────────────────
  ●○  LLM Provider  1/2
────────────────────────────────────────────────────────────────────────────────
◆ Choose your LLM provider (Use arrows to move, Enter to choose)
   ○ Claude Code
 ❯ ○ OpenAI
   ○ OpenRouter
   ○ Other LLM provider
```

---

### Step 4: Non-TTY Landing Page

**Command:**
```bash
opensre
```

**Terminal Output:**
```text
🚧 OpenSRE is in Public Alpha — core workflows are usable, and APIs and integrations may still change.

             ••••••••••                                                         
           ••••••••••••••                                                       
          ••••••    ••••••                                                      
          •• ••      •• ••                                                      
         •• ••        •• ••     opensre  ·  v0.1.2026.8.31+main.8a7a871         
         •• ••        •• ••                                                     
         •• ••        •• ••     Skills (5) ✓   MCPs (0) ✗   AGENTS.md ✗         
          •• ••      •• ••                                                      
          ••••••    ••••••                                                      
           ••••••••••••••                                                       
             ••••••••••                                                         
                                                                                
  open-source SRE agent for automated incident investigation and root cause analysis

  Usage: opensre [OPTIONS] [COMMAND] [ARGS]...
  No COMMAND: start the interactive shell when stdin/stdout are TTYs.

  Quick start:
    opensre setup                               Sign in with GitHub, add an LLM key, then open the shell
    opensre ask "why is checkout-api slow?"     Ask the agent a question directly
    opensre investigate -i alert.json           Run a root-cause investigation on an alert
    opensre doctor                              Check this machine is set up correctly
    opensre --help                              See every command

  Options:
    --version                         Show the version and exit.
    -j, --json                        Emit machine-readable JSON output.
    --verbose                         Print extra diagnostic information.
    --debug                           Print debug-level logs and traces.
    -y, --yes                         Auto-confirm all interactive prompts.
    --interactive / --no-interactive  Disable the interactive shell and print the landing page instead.
    --resume SESSION-ID               Resume a previous interactive shell session by ID, prefix, or name substring.
    --sync-on-exit                    Sync sessions and memory after the interactive shell exits.
    --layout [classic|pinned]         Interactive-shell layout: 'classic' (scrolling) or 'pinned' (fixed input bar). Overrides OPENSRE_LAYOUT env var and ~/.opensre/config.yml.
    --theme THEME                     Interactive-shell color palette. Overrides OPENSRE_THEME env var and ~/.opensre/config.yml interactive.theme.
    -h, --help                        Show this message and exit.
```

---

### Step 5: Interactive Shell & Slash Commands (`/status`, `/help`)

**Commands:**
```bash
# Within interactive TTY session:
/status
/help
/exit
```

**Terminal Output (`/status`):**
```text
Auto (High) · all actions allowed                               claude-opus-4-7 
┌──────────────────────────────────────────────────────────────────────────────┐
│ > Try "Investigate this alert"                                               │
└──────────────────────────────────────────────────────────────────────────────┘
? for help                                                           TERMINAL ■
```

**Terminal Output (`/help`):**
```text
[1] ❯ /help

command /help

                                 Slash commands                                 
1/73
────────────────────────┼───────────────────────────────────────────────────────
Quick Access            │                                                       
 > ▸ /investigate       │ Run an RCA investigation from a file or sample templa…
   ▸ /integrations      │ Manage integrations.                                  
   ▸ /model             │ Show or change active LLM settings.                   
     /health            │ Show integration and agent health.                    
   ▸ /watch             │ Watch a process and send threshold alarms.            
     /status            │ Show session status.                                  
   ▸ /help              │ Show available commands.                              
────────────────────────┼───────────────────────────────────────────────────────
Help                    │                                                       
   ▸ /help              │ Show available commands.                              
     /?                 │ Shortcut for /help.                                   
────────────────────────┼───────────────────────────────────────────────────────
Session                 │                                                       
     /clear             │ Clear the screen and re-render the banner.            
   ▸ /sessions          │ List recent REPL sessions.                            
   ▸ /resume            │ Resume a previous session by restoring its conversati…
   ▸ /new               │ Start a new session while keeping the current convers…
   ▸ /compact           │ Compact the current session context into a replayable…
   ▸ /goal              │ Show, set, pause, resume, edit, or clear the multi-st…
   ▸ /choose            │ Open the pending interactive selection menu queued by…

↑↓/j/k navigate  ·  Enter run command  ·  Space toggle details  ·  Esc/q close
```

---

### Step 6: Offline Fixture Credential Gate

**Command:**
```bash
opensre investigate -i tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json
```

**Terminal Output:**
```text
╭──────────────────────────────────────────────────────────────────────────────╮
│   ✗  OpenSREError                                                            │
│      LLM provider 'anthropic' credentials are missing: ANTHROPIC_API_KEY is  │
│ not configured.                                                              │
│      tools/investigation/session_runner.py:58 in check_llm_settings          │
│      Run `opensre auth verify anthropic` or `opensre auth login anthropic`   │
│ before starting an investigation.                                            │
╰──────────────────────────────────────────────────────────────────────────────╯
```

---

### Step 7: Gateway Daemon Lifecycle

**Commands:**
```bash
opensre gateway status
opensre gateway start
opensre gateway status
opensre gateway stop
```

**Terminal Output:**
```text
$ opensre gateway status
OpenSRE gateway: stopped
Logs: /home/d/.opensre/gateway/gateway.log

$ opensre gateway start
OpenSRE gateway started (pid 416).
Logs: /home/d/.opensre/gateway/gateway.log
Stop: opensre gateway stop · Status: opensre gateway status

$ opensre gateway status
OpenSRE gateway: running (pid 416)
Logs: /home/d/.opensre/gateway/gateway.log

$ opensre gateway stop
OpenSRE gateway stopped (pid 416).
```

---

## 4. Conclusion

The OpenSRE curl installer on Linux `x86_64` (`Ubuntu 26.04 LTS`) installs and configures OpenSRE cleanly. All CLI entrypoints, diagnostics (`doctor`), onboarding wizards, interactive shell, slash commands, investigation credential gates, and gateway daemon lifecycle operations function properly without blocking errors.
