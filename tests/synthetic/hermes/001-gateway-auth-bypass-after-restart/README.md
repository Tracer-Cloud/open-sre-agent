# 001-gateway-auth-bypass-after-restart — Telegram polling conflict + gateway restart processes unauthorized message (#23778)

## Source
https://github.com/NousResearch/hermes-agent/issues/23778

## Notes
Real timeline from issue #23778 (P0 security): four Telegram polling conflicts in a ~5 minute window, gateway restart, then the **first inbound batch after reconnect processes the attacker's message with no "Unauthorized" warning**. The polling burst must fire as `warning_burst` to give on-call lead time, and the trailing `auth bypass` ERROR must fire as `error_severity` so it pages immediately. `traceback` count is asserted to be **zero** to catch any regression that mis-classifies the bypass logline as a continuation frame.

## Fixture
`errors.log` is reproduced from the cited issue with minimal
reformatting to match Hermes's standard `logging` output
(timestamp + LEVEL + logger + message). Lines, loggers, and key
message text are taken **verbatim** from the bug report so the
classifier is exercised on real Hermes log shapes.
