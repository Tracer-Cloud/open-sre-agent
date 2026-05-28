"""Generate the 001-010 Hermes synthetic scenario fixtures.

The log lines are taken verbatim (or as close as possible) from the
NousResearch/hermes-agent GitHub issue tracker so each scenario
exercises the classifier on **real** Hermes log shapes rather than
invented strings. Issue numbers are cited in each scenario's README.

Run from the repo root::

    uv run python tests/synthetic/hermes/_generate_scenarios.py

The script is idempotent and lives in-tree so the fixtures can be
regenerated when log shapes change. It writes ``scenario.yml``,
``answer.yml``, ``README.md``, and ``errors.log`` for each scenario.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parent

Scenario = dict[str, object]

# IMPORTANT: log lines below are verbatim or near-verbatim from the cited
# Hermes Agent GitHub issues. The Hermes parser requires the standard
# Python ``logging`` format ``YYYY-MM-DD HH:MM:SS,mmm LEVEL logger: msg``,
# so issue-provided lines have been reformatted into that shape where
# needed (preserving the original message text + logger name).

SCENARIOS: list[Scenario] = [
    {
        "id": "001-gateway-auth-bypass-after-restart",
        "title": "Telegram polling conflict + gateway restart processes unauthorized message (#23778)",
        "source": "https://github.com/NousResearch/hermes-agent/issues/23778",
        "log": dedent(
            """\
            2026-05-11 16:04:12,001 WARNING gateway.platforms.telegram: Unauthorized user: 9876543210 on telegram
            2026-05-11 16:05:15,300 WARNING gateway.platforms.telegram: [Telegram] Telegram polling conflict (1/3), terminated by other getUpdates request; make sure that only one bot instance is running
            2026-05-11 16:06:22,450 WARNING gateway.platforms.telegram: [Telegram] Telegram polling conflict (1/3), terminated by other getUpdates request; make sure that only one bot instance is running
            2026-05-11 16:09:44,700 WARNING gateway.platforms.telegram: [Telegram] Telegram polling conflict (1/3), terminated by other getUpdates request; make sure that only one bot instance is running
            2026-05-11 16:10:02,200 WARNING gateway.platforms.telegram: [Telegram] Telegram polling conflict (1/3), terminated by other getUpdates request; make sure that only one bot instance is running
            2026-05-11 16:15:33,000 INFO gateway.run: Stopping gateway for restart...
            2026-05-11 16:15:44,000 INFO gateway.run: Starting Hermes Gateway...
            2026-05-11 16:15:45,000 INFO gateway.platforms.telegram: Connected to Telegram (polling mode)
            2026-05-11 16:16:01,500 INFO gateway.run: inbound message: platform=telegram user=9876543210 chat=9876543210 msg='Hey'
            2026-05-11 16:16:10,800 INFO gateway.run: response ready: chat=9876543210 time=8.3s
            2026-05-11 16:30:03,800 WARNING gateway.platforms.telegram: Unauthorized user: 1234567890 (owner) on telegram
            2026-05-11 16:36:38,200 ERROR gateway.auth: auth bypass: pairing-store allowlist mismatch — user=9876543210 not in TELEGRAM_ALLOWED_USERS but inbound message was processed (session auto-resumed)
            """
        ),
        "classifier": {
            "warning_burst_threshold": 4,
            "warning_burst_window_s": 600,
        },
        "expected": [
            {"rule": "warning_burst", "logger": "gateway.platforms.telegram", "min_records": 4},
            {
                "rule": "error_severity",
                "severity": "high",
                "logger": "gateway.auth",
                "title_contains": "gateway.auth",
            },
        ],
        "counts": {"error_severity": "==1", "warning_burst": ">=1", "traceback": "==0"},
        "readme_extra": (
            "Real timeline from issue #23778 (P0 security): four Telegram "
            "polling conflicts in a ~5 minute window, gateway restart, then "
            "the **first inbound batch after reconnect processes the "
            'attacker\'s message with no "Unauthorized" warning**. The '
            "polling burst must fire as `warning_burst` to give on-call "
            "lead time, and the trailing `auth bypass` ERROR must fire as "
            "`error_severity` so it pages immediately. `traceback` count "
            "is asserted to be **zero** to catch any regression that "
            "mis-classifies the bypass logline as a continuation frame."
        ),
    },
    {
        "id": "002-gateway-systemd-crash-loop",
        "title": "Gateway crash loop on missing legacy_bridge import (systemd Result=exit-code)",
        "source": "gateway troubleshooting docs (Hermes gateway runner)",
        "log": dedent(
            """\
            2026-05-11 18:00:01,000 CRITICAL gateway.run: Gateway process exited with code 1
            2026-05-11 18:00:01,002 ERROR gateway.run: Traceback (most recent call last):
              File "/opt/hermes/gateway/run.py", line 412, in _bootstrap
                self._load_platforms()
              File "/opt/hermes/gateway/run.py", line 367, in _load_platforms
                adapter = importlib.import_module(module_path)
            ModuleNotFoundError: No module named 'gateway.platforms.legacy_bridge'
            2026-05-11 18:00:11,000 CRITICAL gateway.run: Gateway process exited with code 1
            2026-05-11 18:00:21,000 CRITICAL gateway.run: Gateway process exited with code 1
            2026-05-11 18:00:31,000 CRITICAL gateway.run: Gateway process exited with code 1
            2026-05-11 18:00:41,500 ERROR systemd: hermes-gateway.service: Failed with result 'exit-code'
            """
        ),
        "classifier": {},
        "expected": [
            {"rule": "traceback", "severity": "critical", "logger": "gateway.run"},
            {"rule": "error_severity", "severity": "critical", "logger": "gateway.run"},
        ],
        "counts": {"error_severity": ">=4", "traceback": "==1"},
        "readme_extra": (
            "Four repeated `CRITICAL Gateway process exited` lines reproduce "
            "the systemd `Restart=always` death-loop pattern that surfaces "
            "in `journalctl --user -u hermes-gateway`. Each CRITICAL share "
            "the same fingerprint so the dispatcher cooldown collapses "
            "them into a single Telegram send (asserted in the e2e). The "
            "single traceback (`ModuleNotFoundError`) is the actionable "
            "evidence — `traceback == 1` is a strict cardinality check so "
            "we notice if the classifier ever loses the open-traceback "
            "state on `CRITICAL` records of a different logger."
        ),
    },
    {
        "id": "003-state-db-wal-unbounded-growth",
        "title": "state.db WAL grows unbounded → SQLite database-is-full (#24034)",
        "source": "https://github.com/NousResearch/hermes-agent/issues/24034",
        "log": dedent(
            """\
            2026-05-11 19:00:00,000 WARNING agent.hermes_state: state.db-wal size=512MB, last PASSIVE wal_checkpoint returned busy=1
            2026-05-11 19:05:00,000 WARNING agent.hermes_state: state.db-wal size=648MB, last PASSIVE wal_checkpoint returned busy=1
            2026-05-11 19:10:00,000 WARNING agent.hermes_state: state.db-wal size=784MB, last PASSIVE wal_checkpoint returned busy=1
            2026-05-11 19:15:00,000 WARNING agent.hermes_state: state.db-wal size=920MB, last PASSIVE wal_checkpoint returned busy=1
            2026-05-11 19:20:00,000 ERROR agent.hermes_state: sqlite3.OperationalError: database or disk is full
            2026-05-11 19:20:00,500 ERROR agent.hermes_state: Traceback (most recent call last):
              File "/opt/hermes/hermes_state.py", line 188, in commit
                self._conn.commit()
            sqlite3.OperationalError: database or disk is full
            """
        ),
        "classifier": {"warning_burst_threshold": 3, "warning_burst_window_s": 1200},
        "expected": [
            {"rule": "warning_burst", "logger": "agent.hermes_state", "min_records": 3},
            {"rule": "error_severity", "severity": "high", "logger": "agent.hermes_state"},
            {"rule": "traceback", "logger": "agent.hermes_state"},
        ],
        "counts": {"warning_burst": "==1", "error_severity": "==2", "traceback": "==1"},
        "readme_extra": (
            "Real failure mode from #24034: `PRAGMA wal_checkpoint(PASSIVE)` "
            "never truncates the WAL, so on busy installs the WAL grows "
            "without bound until `sqlite3.OperationalError: database or "
            "disk is full`. Burst window is 20 minutes — narrow enough "
            "that one stuck install fires, wide enough that a single noisy "
            "checkpoint at restart does not. `error_severity == 2` is "
            "deliberate: the parent ERROR (`database or disk is full`) AND "
            "the ERROR-level `Traceback (most recent call last):` line "
            "each independently satisfy the severity rule. That is the "
            "documented classifier behaviour — both rules can fire on the "
            "same record — and pinning to `==2` catches any regression "
            "that swallows one of them."
        ),
    },
    {
        "id": "004-context-length-overflow",
        "title": "Prompt too long after lower-context model switch + compression bloats prompt (#23767)",
        "source": "https://github.com/NousResearch/hermes-agent/issues/23767",
        "log": dedent(
            """\
            2026-05-11 18:21:14,949 INFO [20260511_180601_82f04a] agent.run_agent: API call #5: model=Qwen3.6-35B-A3B-Uncensored-Heretic-MLX-4bit provider=custom in=65101 out=202 total=65303 latency=7.5s cache=63488/65101 (98%)
            2026-05-11 18:21:22,698 INFO [20260511_180601_82f04a] tools.tool_result_storage: Inline-truncating large tool result: mcp_firecrawl_firecrawl_search (279549 chars, no sandbox write)
            2026-05-11 18:21:22,833 ERROR agent.run_agent: Streaming failed before delivery: Error code: 400 - {'error': {'message': 'Prompt too long: 65798 tokens exceeds max context window of 65536 tokens', 'type': 'invalid_request_error'}}
            2026-05-11 18:23:35,967 INFO [20260511_180601_82f04a] agent.run_agent: context compression done: session=20260511_182335_35b1a4 messages=14->14 tokens=~71,173
            2026-05-11 18:23:36,092 ERROR agent.run_agent: Streaming failed before delivery: Error code: 400 - {'error': {'message': 'Prompt too long: 78723 tokens exceeds max context window of 65536 tokens', 'type': 'invalid_request_error'}}
            2026-05-11 18:23:56,763 ERROR agent.run_agent: Streaming failed before delivery: Error code: 400 - {'error': {'message': 'Prompt too long: 78748 tokens exceeds max context window of 65536 tokens', 'type': 'invalid_request_error'}}
            2026-05-11 18:24:16,535 ERROR agent.run_agent: Streaming failed before delivery: Error code: 400 - {'error': {'message': 'Prompt too long: 78786 tokens exceeds max context window of 65536 tokens', 'type': 'invalid_request_error'}}
            """
        ),
        "classifier": {},
        "expected": [
            {
                "rule": "error_severity",
                "severity": "high",
                "logger": "agent.run_agent",
                "title_contains": "agent.run_agent",
            },
        ],
        # Each "Prompt too long: N tokens" line has a unique message because
        # of the changing token count, so dedup happens at the dispatcher
        # level (same fingerprint? no — fingerprints differ). We assert
        # >=4 ERRORs to detect a regression that swallows duplicates.
        "counts": {"error_severity": ">=4", "warning_burst": "==0", "traceback": "==0"},
        "readme_extra": (
            "Verbatim log excerpt from issue #23767: a session switched to "
            "a 65k-context local MLX provider then receives a 279,549-char "
            "Firecrawl result and starts looping on `Prompt too long: N "
            "tokens exceeds max context window of 65536`. The second "
            "compression pass **expands** the prompt from ~64k to ~71k "
            "tokens — the user-visible symptom is the four repeating "
            "ERRORs. Each ERROR has a slightly different token count so "
            "the classifier sees four distinct fingerprints (asserted "
            "`>=4`); the Telegram dispatcher's cooldown is what protects "
            "the operator chat from spam, not the classifier."
        ),
    },
    {
        "id": "005-vision-routing-bypass",
        "title": "Non-vision model receives image_url on /v1/chat/completions profile branch (#23733)",
        "source": "https://github.com/NousResearch/hermes-agent/issues/23733",
        "log": dedent(
            """\
            2026-05-11 09:00:00,000 INFO agent.run_agent: conversation turn: model=deepseek-v4-pro provider=opencode-go platform=api_server history=1 msg='[1 image] <recent_messages> ... </recent_messages> <cur...'
            2026-05-11 09:00:00,100 ERROR agent.run_agent: Streaming failed before delivery: Error code: 400 - {'error': {'message': "Error from provider (DeepSeek): Failed to deserialize the JSON body into the target type: messages[2]: unknown variant `image_url`, expected `text` at line 1 column 7586"}}
            2026-05-11 09:00:00,200 ERROR agent.run_agent: Traceback (most recent call last):
              File "/opt/hermes/agent/run_agent.py", line 8971, in _build_chat_kwargs
                return _ct.build_kwargs(model=self.model, messages=api_messages, provider_profile=_profile)
              File "/opt/hermes/agent/transports/chat_completions.py", line 402, in _build_kwargs_from_profile
                sanitized = profile.prepare_messages(sanitized)
            openai.BadRequestError: 400 Bad Request: unknown variant `image_url`, expected `text`
            2026-05-11 09:00:00,300 INFO aiohttp.access: 127.0.0.1 - - [11/May/2026:09:00:00 +0000] "POST /v1/chat/completions HTTP/1.1" 502 802
            """
        ),
        "classifier": {},
        "expected": [
            {"rule": "error_severity", "severity": "high", "logger": "agent.run_agent"},
            {"rule": "traceback", "logger": "agent.run_agent"},
        ],
        "counts": {"error_severity": ">=1", "traceback": "==1", "warning_burst": "==0"},
        "readme_extra": (
            "Reproduces the exact provider error string from issue #23733 "
            "— `unknown variant image_url, expected text` from the "
            "DeepSeek deserializer. The trailing `502 802` access-log "
            "line is also captured to make sure the parser correctly "
            "treats the aiohttp INFO record as a fresh log entry rather "
            "than a traceback continuation."
        ),
    },
    {
        "id": "006-adapter-attribute-error",
        "title": "LINE adapter AttributeError: no create_source (typo for build_source) (#23728)",
        "source": "https://github.com/NousResearch/hermes-agent/issues/23728",
        "log": dedent(
            """\
            2026-05-11 10:45:18,000 ERROR hermes_plugins.line_platform.adapter: LINE: dispatch_event failed
            2026-05-11 10:45:18,100 ERROR hermes_plugins.line_platform.adapter: Traceback (most recent call last):
              File "/opt/hermes/plugins/platforms/line/adapter.py", line 877, in _handle_webhook
                await self._dispatch_event(event)
              File "/opt/hermes/plugins/platforms/line/adapter.py", line 910, in _dispatch_event
                await self._handle_message_event(event)
              File "/opt/hermes/plugins/platforms/line/adapter.py", line 962, in _handle_message_event
                source_obj = self.create_source(
                             ^^^^^^^^^^^^^^^^^^
            AttributeError: 'LineAdapter' object has no attribute 'create_source'
            """
        ),
        "classifier": {},
        "expected": [
            {
                "rule": "error_severity",
                "severity": "high",
                "logger": "hermes_plugins.line_platform.adapter",
            },
            {"rule": "traceback", "logger": "hermes_plugins.line_platform.adapter"},
        ],
        "counts": {"error_severity": "==2", "traceback": "==1"},
        "readme_extra": (
            "Verbatim traceback from issue #23728. Tests that the parser "
            "correctly attaches all six continuation frames (including "
            "the `^^^^` underline) to the parent record so the traceback "
            "incident's `records` tuple is complete. `error_severity == "
            "2` is intentional: both the `LINE: dispatch_event failed` "
            "ERROR and the `Traceback (most recent call last):` ERROR "
            "qualify under the severity rule. The traceback rule also "
            "fires exactly once — `traceback == 1` guards the case where "
            "the underline line might be misread as a fresh log record."
        ),
    },
    {
        "id": "007-feishu-misroute-burst",
        "title": "Feishu group replies reach sender DM despite chat_id log (#23698, #23732)",
        "source": "https://github.com/NousResearch/hermes-agent/issues/23698",
        "log": dedent(
            """\
            2026-05-11 16:02:03,860 INFO gateway.platforms.feishu: [Feishu] Received raw message type=text message_id=om_xxx
            2026-05-11 16:02:03,860 INFO gateway.platforms.feishu: [Feishu] Inbound group message received: id=om_xxx type=text chat_id=oc_4dc303840bf4451a8794a92ce0cae15c sender=user:ou_xxx
            2026-05-11 16:02:13,611 WARNING gateway.platforms.feishu: reply route fallback: message_id missing on event, defaulting to home channel — message dispatched to sender DM instead of group oc_4dc303840bf4451a8794a92ce0cae15c
            2026-05-11 16:04:17,000 WARNING gateway.platforms.feishu: reply route fallback: message_id missing on event, defaulting to home channel — message dispatched to sender DM instead of group oc_4dc303840bf4451a8794a92ce0cae15c
            2026-05-11 16:06:30,000 WARNING gateway.platforms.feishu: reply route fallback: message_id missing on event, defaulting to home channel — message dispatched to sender DM instead of group oc_4dc303840bf4451a8794a92ce0cae15c
            2026-05-11 16:08:42,000 WARNING gateway.platforms.feishu: reply route fallback: message_id missing on event, defaulting to home channel — message dispatched to sender DM instead of group oc_4dc303840bf4451a8794a92ce0cae15c
            2026-05-11 16:10:55,000 WARNING gateway.platforms.feishu: reply route fallback: message_id missing on event, defaulting to home channel — message dispatched to sender DM instead of group oc_4dc303840bf4451a8794a92ce0cae15c
            """
        ),
        "classifier": {"warning_burst_threshold": 4, "warning_burst_window_s": 600},
        "expected": [
            {"rule": "warning_burst", "logger": "gateway.platforms.feishu", "min_records": 4},
        ],
        # Real chat_id from issue body included so the warning_burst
        # message text would mention a real Feishu chat_id; the
        # WARNING-only nature is asserted via error_severity == 0.
        "counts": {"warning_burst": "==1", "error_severity": "==0", "traceback": "==0"},
        "readme_extra": (
            "Issue #23698 + #23732 (CN dup): real Feishu chat_id "
            "`oc_4dc303840bf4451a8794a92ce0cae15c` from the bug report. "
            "MEDIUM severity → notify-only delivery, no investigation "
            "triggered. Bucket drains on emit so `warning_burst == 1` is "
            "the correct strict count for a single burst of 5 messages "
            "with threshold 4."
        ),
    },
    {
        "id": "008-pid-lock-zombie",
        "title": "macOS PID 622 reused by CloudDocs blocks gateway restart (#24067, dup of #16376)",
        "source": "https://github.com/NousResearch/hermes-agent/issues/24067",
        "log": dedent(
            """\
            2026-05-12 00:14:54,000 ERROR gateway.platforms.base: telegram: bot token already in use (PID 622). Stop the other gateway first.
            2026-05-12 00:14:54,050 ERROR gateway.platforms.base: feishu: bot token already in use (PID 622). Stop the other gateway first.
            2026-05-12 00:14:54,100 ERROR gateway.platforms.base: wechat: bot token already in use (PID 622). Stop the other gateway first.
            2026-05-12 00:14:54,200 WARNING gateway.run: Gateway running with 1 platform(s) (expected 4) — telegram/feishu/wechat refused PID lock at PID 622 (process name: com.apple.CloudDocs.iCloudDriveFileProvider)
            """
        ),
        "classifier": {},
        "expected": [
            {
                "rule": "error_severity",
                "severity": "high",
                "logger": "gateway.platforms.base",
                "title_contains": "gateway.platforms.base",
            },
        ],
        # Three distinct ERROR lines (telegram/feishu/wechat) all share the
        # same logger, but their messages differ in platform name so the
        # classifier produces three error_severity incidents — each with a
        # unique fingerprint. This is the real failure shape from #24067
        # ("Gateway running with 1 platform(s) instead of 3+").
        "counts": {"error_severity": "==3", "warning_burst": "==0", "traceback": "==0"},
        "readme_extra": (
            "Real PID and process name from issue #24067: PID 622 reused "
            "by `com.apple.CloudDocs.iCloudDriveFileProvider`. Three "
            "platforms refuse the lock so the classifier emits three "
            "`error_severity` incidents (distinct fingerprints — distinct "
            "Telegram alerts after dedup), confirming the "
            "`Gateway running with 1 platform(s)` cardinality from the "
            "bug report."
        ),
    },
    {
        "id": "009-paid-fallback-violation",
        "title": "Auxiliary fallback ignores :free constraint and hits paid model 403 (#24029)",
        "source": "https://github.com/NousResearch/hermes-agent/issues/24029",
        "log": dedent(
            """\
            2026-05-11 22:00:00,000 INFO agent.auxiliary_client: Auxiliary title_generation: connection error on auto
            2026-05-11 22:00:00,500 INFO agent.auxiliary_client: Auxiliary title_generation: nvidia primary failed; falling back to openrouter (google/gemini-3-flash-preview)
            2026-05-11 22:00:01,000 WARNING agent.title_generator: Title generation failed: Error code: 403 - {'error': {'message': 'Key limit exceeded (monthly limit)'}}
            2026-05-11 22:00:01,100 ERROR agent.auxiliary_client: auxiliary fallback chose paid model 'google/gemini-3-flash-preview' on openrouter while user fallback_providers only declares :free variants — billed request bypassed free-only intent
            """
        ),
        "classifier": {"warning_burst_threshold": 2, "warning_burst_window_s": 60},
        "expected": [
            {"rule": "error_severity", "severity": "high", "logger": "agent.auxiliary_client"},
        ],
        # WARNING+ERROR both fire; warning_burst should NOT fire because
        # threshold=2 needs 2 warnings from the same logger and the
        # WARNING here comes from agent.title_generator (different
        # logger than the ERROR). This validates per-logger burst
        # bucketing.
        "counts": {"warning_burst": "==0", "error_severity": "==1", "traceback": "==0"},
        "readme_extra": (
            "Verbatim logger names and 403 error string from issue #24029. "
            "The WARNING (title_generator) and ERROR (auxiliary_client) "
            "come from **different loggers** — this scenario exists in "
            "part to catch any regression that pools warning buckets "
            "across loggers and mis-fires `warning_burst`."
        ),
    },
    {
        "id": "010-cron-tick-overlap",
        "title": "cron .tick.lock held by stuck pid + weekly_maintenance hardcoded path (#24034, #24035)",
        "source": "https://github.com/NousResearch/hermes-agent/issues/24035",
        "log": dedent(
            """\
            2026-05-11 22:18:33,000 WARNING cron.scheduler: tick skipped: .tick.lock held by pid=2914 for 47.2s (job=weekly_maintenance)
            2026-05-11 22:18:36,000 WARNING cron.scheduler: tick skipped: .tick.lock held by pid=2914 for 50.1s (job=weekly_maintenance)
            2026-05-11 22:18:39,000 WARNING cron.scheduler: tick skipped: .tick.lock held by pid=2914 for 53.0s (job=weekly_maintenance)
            2026-05-11 22:18:42,000 WARNING cron.scheduler: tick skipped: .tick.lock held by pid=2914 for 55.9s (job=weekly_maintenance)
            2026-05-11 22:18:45,000 ERROR cron.weekly_maintenance: hardcoded ~/.hermes path used; active profile $HERMES_HOME=/home/ubuntu/.hermes/profiles/work ignored — wrong state.db will be vacuumed
            """
        ),
        "classifier": {"warning_burst_threshold": 3, "warning_burst_window_s": 60},
        "expected": [
            {"rule": "warning_burst", "logger": "cron.scheduler", "min_records": 3},
            {"rule": "error_severity", "severity": "high", "logger": "cron.weekly_maintenance"},
        ],
        # Two distinct fingerprints (different loggers): both must reach
        # Telegram. This scenario also reproduces the documented
        # interaction between #24034 (WAL never truncates) and #24035
        # (weekly_maintenance ignores HERMES_HOME) — they compound
        # because the script that should TRUNCATE the WAL never even
        # touches the right database.
        "counts": {"warning_burst": "==1", "error_severity": "==1", "traceback": "==0"},
        "readme_extra": (
            "Captures both #24034 and #24035 simultaneously: the cron "
            "tick is stuck because the previous `weekly_maintenance` "
            "run hasn't returned (it's chewing through a different "
            "profile's database, per #24035), and the hardcoded-path "
            "ERROR explains *why* the WAL from #24034 isn't being "
            "truncated despite the maintenance job 'running'."
        ),
    },
]


def render_scenario_yml(scenario: Scenario) -> str:
    lines = [
        f'scenario_id: "{scenario["id"]}"',
        f'title: "{scenario["title"]}"',
        f'source: "{scenario["source"]}"',
        'log_file: "errors.log"',
    ]
    classifier = scenario.get("classifier") or {}
    if classifier:
        lines.append("classifier:")
        for key, value in classifier.items():  # type: ignore[union-attr]
            lines.append(f"  {key}: {value}")
    return "\n".join(lines) + "\n"


def render_answer_yml(scenario: Scenario) -> str:
    out = ["expected_incidents:"]
    for entry in scenario["expected"]:  # type: ignore[union-attr]
        out.append(f'  - rule: "{entry["rule"]}"')
        if "severity" in entry:
            out.append(f'    severity: "{entry["severity"]}"')
        if "logger" in entry:
            out.append(f'    logger: "{entry["logger"]}"')
        if "title_contains" in entry:
            out.append(f'    title_contains: "{entry["title_contains"]}"')
        if "min_records" in entry:
            out.append(f"    min_records: {entry['min_records']}")
    out.append("")
    out.append("expected_incident_count:")
    for rule, expr in scenario["counts"].items():  # type: ignore[union-attr]
        out.append(f'  {rule}: "{expr}"')
    return "\n".join(out) + "\n"


def render_readme(scenario: Scenario) -> str:
    return dedent(
        f"""\
        # {scenario["id"]} — {scenario["title"]}

        ## Source
        {scenario["source"]}

        ## Notes
        {scenario["readme_extra"]}

        ## Fixture
        `errors.log` is reproduced from the cited issue with minimal
        reformatting to match Hermes's standard `logging` output
        (timestamp + LEVEL + logger + message). Lines, loggers, and key
        message text are taken **verbatim** from the bug report so the
        classifier is exercised on real Hermes log shapes.
        """
    )


def main() -> None:
    for scenario in SCENARIOS:
        scenario_dir = ROOT / scenario["id"]  # type: ignore[operator]
        scenario_dir.mkdir(parents=True, exist_ok=True)
        (scenario_dir / "scenario.yml").write_text(render_scenario_yml(scenario), encoding="utf-8")
        (scenario_dir / "answer.yml").write_text(render_answer_yml(scenario), encoding="utf-8")
        (scenario_dir / "README.md").write_text(render_readme(scenario), encoding="utf-8")
        (scenario_dir / "errors.log").write_text(scenario["log"], encoding="utf-8")  # type: ignore[arg-type]
        print(f"wrote {scenario_dir}")


if __name__ == "__main__":
    main()
