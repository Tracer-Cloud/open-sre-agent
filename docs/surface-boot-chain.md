# How surfaces boot

OpenSRE can be reached in several ways — the `opensre` CLI, the interactive
shell, the gateway daemon (chat channels), the gateway's web app, scheduled
`cron` commands, and even a plain Python script embedding the agent. All of
them start up the same way: each calls one function,
`configure_process(<profile>)`, defined in
[`bootstrap/process.py`](../bootstrap/process.py), before doing anything
else. A surface's own code is only responsible for its channel and UX —
rendering to a terminal, replying in chat, serving an HTTP route, parsing CLI
arguments. None of them assemble their own startup sequence.

## What `configure_process` actually does

Startup is a short checklist of setup work that has to happen in the same
order every time:

1. **Load the environment** — read configuration and secrets.
2. **Start error reporting** — so failures later in startup are still
   captured.
3. **Register adapters** — connect the agent to the integrations and tools it
   can use.
4. **Register the scheduler's task runners** — so scheduled work (digests,
   reports, cron jobs) knows how to run.
5. **Log capability warnings** — flag anything the sandbox can't do in this
   environment.
6. **Warm up the LLM client** — so the first real request isn't slowed down
   by connection setup.

Not every surface needs every step. A `<profile>` is just a name for "the
subset of this checklist a given surface needs" — the CLI needs far less
than the gateway daemon does. Calling `configure_process` with a profile runs
exactly those steps, in the order above, and it's safe to call more than
once (later calls for the same surface are simply skipped).

## The six profiles

| Profile | What it sets up | Used by |
| --- | --- | --- |
| CLI | Just the environment. | The `opensre` command |
| Gateway | Environment, error reporting, adapters, capability warnings, LLM warm-up. | The gateway daemon (chat channels) |
| Web | Environment, error reporting, adapters. | The gateway's standalone web app |
| Scheduler worker | Environment, error reporting, adapters, scheduler task runners. | The `opensre cron start` daemon |
| Scheduled command | Environment, adapters, scheduler task runners. | One-off CLI commands that create, run, or dispatch scheduled work |
| Embedded | Environment, adapters. | Driving the agent from another Python program |

## Where each surface calls it

- **CLI** — [`surfaces/cli/startup.py`](../surfaces/cli/startup.py) calls the
  CLI profile first, then handles CLI-only setup: its own error reporting
  (tolerant of a missing dependency during `opensre update`), terminal output
  styling, and keyboard-interrupt handling. The interactive shell runs inside
  this same already-started CLI process, so it doesn't start up again.
- **Interactive shell's saved loops** — when a saved prompt loop needs its
  own background scheduler, it starts up with the scheduled-command profile
  in
  [`surfaces/interactive_shell/runtime/loop_scheduler.py`](../surfaces/interactive_shell/runtime/loop_scheduler.py)
  and
  [`surfaces/interactive_shell/command_registry/loops_cmds.py`](../surfaces/interactive_shell/command_registry/loops_cmds.py).
- **Gateway daemon** — [`gateway/core/runtime/manager.py`](../gateway/core/runtime/manager.py)
  sets up its own logging and credentials first, then starts up with the
  gateway profile before connecting chat channels and the scheduler.
- **Gateway web app** — [`gateway/web/webapp.py`](../gateway/web/webapp.py)
  starts up with the web profile as soon as the module loads, so both the
  in-process gateway and a standalone web server have everything they need
  before handling a request.
- **Scheduled/cron CLI commands** — one-off commands like `cron run` in
  [`surfaces/cli/commands/cron.py`](../surfaces/cli/commands/cron.py),
  [`sentry_digest.py`](../surfaces/cli/commands/sentry_digest.py), and
  [`posthog_report.py`](../surfaces/cli/commands/posthog_report.py) start up
  with the scheduled-command profile. The long-running `cron start` daemon
  uses the scheduler-worker profile instead, since it needs its own error
  reporting and only needs to register task runners once, not on every run.
- **Embedded Python usage** — [`main.py`](../main.py) and
  [`bootstrap/embedded.py`](../bootstrap/embedded.py) start up with the
  embedded profile. This is also the pattern to follow if you're driving the
  agent from your own Python script: call `configure_process` with the
  embedded profile before your first request.

## Why it works this way

Keeping one shared startup checklist means every surface gets the same
guarantees — nothing is skipped, and nothing runs out of order — no matter
how it's launched. It also means adding or changing a startup step only
needs to happen in one place. For how this fits into OpenSRE's broader
package layout, see [`docs/ARCHITECTURE.md`](ARCHITECTURE.md).
