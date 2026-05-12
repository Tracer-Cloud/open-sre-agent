# 003-state-db-wal-unbounded-growth — state.db WAL grows unbounded → SQLite database-is-full (#24034)

## Source
https://github.com/NousResearch/hermes-agent/issues/24034

## Notes
Real failure mode from #24034: `PRAGMA wal_checkpoint(PASSIVE)` never truncates the WAL, so on busy installs the WAL grows without bound until `sqlite3.OperationalError: database or disk is full`. Burst window is 20 minutes — narrow enough that one stuck install fires, wide enough that a single noisy checkpoint at restart does not. `error_severity == 2` is deliberate: the parent ERROR (`database or disk is full`) AND the ERROR-level `Traceback (most recent call last):` line each independently satisfy the severity rule. That is the documented classifier behaviour — both rules can fire on the same record — and pinning to `==2` catches any regression that swallows one of them.

## Fixture
`errors.log` is reproduced from the cited issue with minimal
reformatting to match Hermes's standard `logging` output
(timestamp + LEVEL + logger + message). Lines, loggers, and key
message text are taken **verbatim** from the bug report so the
classifier is exercised on real Hermes log shapes.
