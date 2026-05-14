# 004-context-length-overflow — Prompt too long after lower-context model switch + compression bloats prompt (#23767)

## Source
https://github.com/NousResearch/hermes-agent/issues/23767

## Notes
Verbatim log excerpt from issue #23767: a session switched to a 65k-context local MLX provider then receives a 279,549-char Firecrawl result and starts looping on `Prompt too long: N tokens exceeds max context window of 65536`. The second compression pass **expands** the prompt from ~64k to ~71k tokens — the user-visible symptom is the four repeating ERRORs. Each ERROR has a slightly different token count so the classifier sees four distinct fingerprints (asserted `>=4`); the Telegram dispatcher's cooldown is what protects the operator chat from spam, not the classifier.

## Fixture
`errors.log` is reproduced from the cited issue with minimal
reformatting to match Hermes's standard `logging` output
(timestamp + LEVEL + logger + message). Lines, loggers, and key
message text are taken **verbatim** from the bug report so the
classifier is exercised on real Hermes log shapes.
