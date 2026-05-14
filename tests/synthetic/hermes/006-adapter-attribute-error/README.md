# 006-adapter-attribute-error — LINE adapter AttributeError: no create_source (typo for build_source) (#23728)

## Source
https://github.com/NousResearch/hermes-agent/issues/23728

## Notes
Verbatim traceback from issue #23728. Tests that the parser correctly attaches all six continuation frames (including the `^^^^` underline) to the parent record so the traceback incident's `records` tuple is complete. `error_severity == 2` is intentional: both the `LINE: dispatch_event failed` ERROR and the `Traceback (most recent call last):` ERROR qualify under the severity rule. The traceback rule also fires exactly once — `traceback == 1` guards the case where the underline line might be misread as a fresh log record.

## Fixture
`errors.log` is reproduced from the cited issue with minimal
reformatting to match Hermes's standard `logging` output
(timestamp + LEVEL + logger + message). Lines, loggers, and key
message text are taken **verbatim** from the bug report so the
classifier is exercised on real Hermes log shapes.
