# 005-vision-routing-bypass — Non-vision model receives image_url on /v1/chat/completions profile branch (#23733)

## Source
https://github.com/NousResearch/hermes-agent/issues/23733

## Notes
Reproduces the exact provider error string from issue #23733 — `unknown variant image_url, expected text` from the DeepSeek deserializer. The trailing `502 802` access-log line is also captured to make sure the parser correctly treats the aiohttp INFO record as a fresh log entry rather than a traceback continuation.

## Fixture
`errors.log` is reproduced from the cited issue with minimal
reformatting to match Hermes's standard `logging` output
(timestamp + LEVEL + logger + message). Lines, loggers, and key
message text are taken **verbatim** from the bug report so the
classifier is exercised on real Hermes log shapes.
