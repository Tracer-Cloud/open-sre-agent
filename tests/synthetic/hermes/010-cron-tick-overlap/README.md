# 010-cron-tick-overlap — cron .tick.lock held by stuck pid + weekly_maintenance hardcoded path (#24034, #24035)

## Source
https://github.com/NousResearch/hermes-agent/issues/24035

## Notes
Captures both #24034 and #24035 simultaneously: the cron tick is stuck because the previous `weekly_maintenance` run hasn't returned (it's chewing through a different profile's database, per #24035), and the hardcoded-path ERROR explains *why* the WAL from #24034 isn't being truncated despite the maintenance job 'running'.

## Fixture
`errors.log` is reproduced from the cited issue with minimal
reformatting to match Hermes's standard `logging` output
(timestamp + LEVEL + logger + message). Lines, loggers, and key
message text are taken **verbatim** from the bug report so the
classifier is exercised on real Hermes log shapes.
