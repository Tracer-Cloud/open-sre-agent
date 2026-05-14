# 008-pid-lock-zombie — macOS PID 622 reused by CloudDocs blocks gateway restart (#24067, dup of #16376)

## Source
https://github.com/NousResearch/hermes-agent/issues/24067

## Notes
Real PID and process name from issue #24067: PID 622 reused by `com.apple.CloudDocs.iCloudDriveFileProvider`. Three platforms refuse the lock so the classifier emits three `error_severity` incidents (distinct fingerprints — distinct Telegram alerts after dedup), confirming the `Gateway running with 1 platform(s)` cardinality from the bug report.

## Fixture
`errors.log` is reproduced from the cited issue with minimal
reformatting to match Hermes's standard `logging` output
(timestamp + LEVEL + logger + message). Lines, loggers, and key
message text are taken **verbatim** from the bug report so the
classifier is exercised on real Hermes log shapes.
