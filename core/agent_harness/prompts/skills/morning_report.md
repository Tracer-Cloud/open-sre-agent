══════════════════════════════════════════════════════════
MORNING REPORT SKILL #1 — weather + daily news briefing:
══════════════════════════════════════════════════════════
Recognize a request for a morning briefing — "morning report", "morning
briefing", "daily brief", "give me my morning update", "weather and news
summary", or similar — as a two-source gather that ends in a composed summary.
This is a DATA-DEPENDENT chain (see the COMPOUND TURN RULE box): fetch the raw
inputs first with read-only shell commands, WAIT for their results, then hand
the real content to the assistant to write the report. Never fabricate weather
values or headlines, and never emit the compose/deliver step in the same
response as the fetches.

OUTPUT MUST BE HUMAN-READABLE — NEVER RAW MARKUP. The fetched feed is raw
RSS/XML/HTML; it is INTERMEDIATE data only. NEVER present the raw feed, XML
tags, CDATA blocks, or angle-bracket markup to the user, and NEVER let a raw
`curl` dump be the final answer. The user only ever sees the composed
plain-text briefing produced in the final step. The news fetch below already
strips the feed down to plain-text headline lines so nothing but readable text
comes back.
Steps, in order:
1) Fetch today's weather with shell_run. Use the city the user named; if none
   is given, use their configured/default city, else omit the location:
     → shell_run(command="curl -s 'wttr.in/<city>?format=%l:+%c+%t,+feels+%f,+%h+humidity,+wind+%w'")
2) Fetch current headlines with shell_run as PLAIN TEXT — extract just the
   headline titles from the feed, drop the channel title, and cap the list.
   Do NOT fetch the raw feed without this extraction pipeline:
     → shell_run(command="curl -s 'https://feeds.bbci.co.uk/news/rss.xml' | grep -oE '<title><!\\[CDATA\\[[^]]*\\]\\]>' | sed -E 's/<title><!\\[CDATA\\[//; s/\\]\\]>//' | sed '1d' | head -n 8")
3) After BOTH tool results are in, compose a clean, human-readable briefing
   from the ACTUAL fetched data. Required format — Markdown/plain text only,
   no HTML/XML, no links, no angle brackets:
     Good morning! Here is your briefing.
     Weather — <city>: <one-line conditions from step 1>
     Top headlines:
     - <headline 1, one short sentence>
     - <headline 2>
     - ... (3–5 bullets total)
4) ALWAYS DELIVER TO SLACK — as the FINAL action of this skill you MUST send the
   WHOLE composed briefing (both the weather/temperature line AND the news
   headlines, exactly as formatted in step 3) to Slack via slack_send_message,
   even when the user did NOT explicitly ask to send it anywhere. This is a
   DATA-DEPENDENT step: emit it on the response AFTER the two fetches, with the
   full composed plain-text briefing in `message` — never raw feed markup, never
   a partial report, never a "preparing…" placeholder. The Slack webhook is bound
   to a single preconfigured channel, so do NOT ask which channel to use. If the
   user names another platform (e.g. "post it to telegram"), ALSO deliver there
   with telegram_send_message; Slack stays the default sink. Skip a platform only
   if it is not connected. If neither delivery tool is available, defer to the
   "Delivery tool unavailable" rule above instead of fabricating a command.
Examples:
- "give me my morning report"
    → shell_run(command="curl -s 'wttr.in/Amsterdam?format=%l:+%c+%t,+feels+%f,+%h+humidity,+wind+%w'")
      + shell_run(command="curl -s 'https://feeds.bbci.co.uk/news/rss.xml' | grep -oE '<title><!\\[CDATA\\[[^]]*\\]\\]>' | sed -E 's/<title><!\\[CDATA\\[//; s/\\]\\]>//' | sed '1d' | head -n 8")   [both this turn — independent fetches]
    → (observe both results)
    → slack_send_message(message="<the FULL composed plain-text weather + headlines briefing>")   [next turn — mandatory Slack delivery]
- "morning briefing for Berlin and post it to telegram"
    → shell_run(command="curl -s 'wttr.in/Berlin?format=%l:+%c+%t,+feels+%f,+%h+humidity,+wind+%w'")
      + shell_run(command="curl -s 'https://feeds.bbci.co.uk/news/rss.xml' | grep -oE '<title><!\\[CDATA\\[[^]]*\\]\\]>' | sed -E 's/<title><!\\[CDATA\\[//; s/\\]\\]>//' | sed '1d' | head -n 8")   [both this turn]
    → (observe both results)
    → slack_send_message(message="<the FULL composed briefing>")
      + telegram_send_message(message="<the FULL composed briefing>")   [next turn — Slack always + Telegram because the user named it]
