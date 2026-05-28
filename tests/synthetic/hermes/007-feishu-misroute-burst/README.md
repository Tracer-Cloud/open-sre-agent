# 007-feishu-misroute-burst — Feishu group replies reach sender DM despite chat_id log (#23698, #23732)

## Source
https://github.com/NousResearch/hermes-agent/issues/23698

## Notes
Issue #23698 + #23732 (CN dup): real Feishu chat_id `oc_4dc303840bf4451a8794a92ce0cae15c` from the bug report. MEDIUM severity → notify-only delivery, no investigation triggered. Bucket drains on emit so `warning_burst == 1` is the correct strict count for a single burst of 5 messages with threshold 4.

## Fixture
`errors.log` is reproduced from the cited issue with minimal
reformatting to match Hermes's standard `logging` output
(timestamp + LEVEL + logger + message). Lines, loggers, and key
message text are taken **verbatim** from the bug report so the
classifier is exercised on real Hermes log shapes.
