# 000 — Telegram polling conflict (dual-instance bot token)

## Symptom

`~/.hermes/logs/errors.log` is filling with `WARNING gateway.platforms.telegram`
records every ~15–22 seconds:

```
[Telegram] Telegram polling conflict (1/3), will retry in 10s.
Error: Conflict: terminated by other getUpdates request;
make sure that only one bot instance is running
```

The Hermes UI keeps reconnecting and inbound Telegram messages are
delivered intermittently, depending on which instance happens to win
the most recent `getUpdates` race.

## Real-world cause

Two Hermes processes are authenticated with the same
`TELEGRAM_BOT_TOKEN`:

1. A **local Mac** instance (PID 15183) that the developer left running
   from a previous session.
2. The **EC2** Hermes deployment.

Telegram's bot API allows exactly one long-poll per token; whichever
process opens `getUpdates` last terminates the previous holder, which
then retries 10 seconds later, and so on. The result is a sustained
warning storm with no individual `ERROR` records — every retry is
"recoverable", but the bot is effectively unusable.

## Fixture

`errors.log` is a 12-line slice captured on 2026-05-12 between
`00:40:12` and `00:44:23`. The cadence (~22.5s) is the dominant pattern
observed during the incident; the same pattern was visible at ~15s
spacing later in the log.

## Expected classification

With the per-scenario classifier override
(`warning_burst_threshold: 3`, `warning_burst_window_s: 60`):

| Rule           | Count | Severity | Logger                           |
| -------------- | :---: | :------: | -------------------------------- |
| warning_burst  |   4   | medium   | gateway.platforms.telegram       |
| error_severity |   0   | —        | —                                |
| traceback      |   0   | —        | —                                |

Each burst contains exactly 3 records (the bucket clears on emit, so
the next burst requires another threshold's worth of warnings).

## Remediation (out of scope for the test, captured for context)

Identify and stop the stale local instance:

```bash
# On the Mac:
pgrep -af hermes
# Confirm PID 15183 (or whichever one shouldn't be running) and:
kill 15183
```

Then either rotate the bot token or restart the EC2 Hermes process to
take a clean long-poll. Once the conflict clears, the `errors.log`
warning rate drops to zero and the classifier emits no further bursts.
