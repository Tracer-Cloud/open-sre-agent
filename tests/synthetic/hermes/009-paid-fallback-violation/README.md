# 009-paid-fallback-violation — Auxiliary fallback ignores :free constraint and hits paid model 403 (#24029)

## Source
https://github.com/NousResearch/hermes-agent/issues/24029

## Notes
Verbatim logger names and 403 error string from issue #24029. The WARNING (title_generator) and ERROR (auxiliary_client) come from **different loggers** — this scenario exists in part to catch any regression that pools warning buckets across loggers and mis-fires `warning_burst`.

## Fixture
`errors.log` is reproduced from the cited issue with minimal
reformatting to match Hermes's standard `logging` output
(timestamp + LEVEL + logger + message). Lines, loggers, and key
message text are taken **verbatim** from the bug report so the
classifier is exercised on real Hermes log shapes.
