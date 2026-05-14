# REPL watchdog — PR demo (copy into “Demo/Screenshot”)

Paste the block below into your pull request so reviewers see the demo **in the PR** (see [docs/DEVELOPMENT.md](../../docs/DEVELOPMENT.md#interactive-shell-repl-watchdog-demo)).

```text
$ uv run opensre
> /trust on
> /watch <PID> --max-cpu 80
task <id> started.
> /watches
(table: id, pid, kind, status, thresholds, last sample)
> /unwatch <id>
> /watches
(status: cancelled)
```

Replace `<PID>` with a real process id (for example your REPL’s Python pid). Replace `<id>` with the task id from `/watches`.

Optional (Telegram): set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_DEFAULT_CHAT_ID`, use a lower `--max-cpu` so the threshold trips; expect one line: `[task …] alarm fired: … (telegram delivered)`.

---

Automated proof (CI): `uv run pytest tests/cli/interactive_shell/test_watchdog_repl_e2e_demo.py -v --tb=short`
