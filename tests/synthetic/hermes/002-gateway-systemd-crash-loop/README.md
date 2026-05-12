# 002-gateway-systemd-crash-loop — Gateway crash loop on missing legacy_bridge import (systemd Result=exit-code)

## Source
gateway troubleshooting docs (Hermes gateway runner)

## Notes
Four repeated `CRITICAL Gateway process exited` lines reproduce the systemd `Restart=always` death-loop pattern that surfaces in `journalctl --user -u hermes-gateway`. Each CRITICAL share the same fingerprint so the dispatcher cooldown collapses them into a single Telegram send (asserted in the e2e). The single traceback (`ModuleNotFoundError`) is the actionable evidence — `traceback == 1` is a strict cardinality check so we notice if the classifier ever loses the open-traceback state on `CRITICAL` records of a different logger.

## Fixture
`errors.log` is reproduced from the cited issue with minimal
reformatting to match Hermes's standard `logging` output
(timestamp + LEVEL + logger + message). Lines, loggers, and key
message text are taken **verbatim** from the bug report so the
classifier is exercised on real Hermes log shapes.
