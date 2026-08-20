# 011-session-history-unavailable — Hermes API session history fails open

## Source contract

- Emitter: `NousResearch/hermes-agent` `gateway/platforms/api_server.py::_conversation_history_for_session`
- Source pinned by the issue: [`530028c`](https://github.com/NousResearch/hermes-agent/blob/530028c213ae9eed5d7f1a826451e0edf24a11d2/gateway/platforms/api_server.py#L4182-L4190)
- Verified unchanged on current `main`: [`739bc55`](https://github.com/NousResearch/hermes-agent/blob/739bc555b1932e66c169b20edec3a48368e2dd3f/gateway/platforms/api_server.py#L4211-L4219)
- Logging contract: [`hermes_logging.py`](https://github.com/NousResearch/hermes-agent/blob/739bc555b1932e66c169b20edec3a48368e2dd3f/hermes_logging.py#L330-L338)
- Watched log: `~/.hermes/logs/errors.log`
- Logger allowlist: `gateway.platforms.api_server`
- Level: `WARNING`
- Case-sensitive message template: `Failed to load session history for %s: %s`

The source catches an exception from the persisted-session message lookup, emits
this warning, and immediately returns an empty conversation. It does not retry
or emit a recovery marker on this path, so the warning is the fail-open event.

## Fixture provenance

`errors.log` is a sanitized, source-derived byte-faithful rendering of the
pinned emitter, not a captured production log. The Python logging header matches
the format accepted by `integrations.hermes.parser`; the message preserves the
emitter's exact static text and replaces only the session identifier and
exception with opaque values.

The `session_history_unavailable` rule binds both the exact logger and message.
A first event is `MEDIUM` and routes explicitly to `TELEGRAM`, without RCA. Its
fingerprint intentionally groups this rule and logger across session IDs during
the correlator window because a shared history-store failure can affect several
sessions. The second hit is deduplicated; the third hit is delivered as `HIGH`
to the same explicit `TELEGRAM` route, still without RCA. The fixture stays below
the warning-burst threshold and does not emit `error_severity`.

Discord channel history, Slack thread context, Matrix room history, and custom
bridge transcript failures have different emitters and semantics. They are not
matched by this rule.
