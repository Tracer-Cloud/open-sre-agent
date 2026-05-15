"""Per-channel renderers that walk the shared section list.

Each renderer module exposes the public entry points the report node calls:

- ``renderers.slack``    — ``format_slack_message`` (mrkdwn text fallback)
                            and ``build_slack_blocks`` (Block Kit).
- ``renderers.telegram`` — ``format_telegram_message`` (HTML).

Renderers consume ``prepare_sections_for(ctx)`` and never reach into ctx for
section content directly. The only ctx access remaining is for helpers that
are not yet section-aware (cited evidence, cloudwatch link); those will be
folded into sections in a follow-up.
"""
