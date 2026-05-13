# Hermes Synthetic Suite

Real-world Hermes log fixtures used to lock in the behavior of
`app.hermes.IncidentClassifier`. Each scenario captures a production
pattern observed on a Hermes deployment, ships the raw `errors.log`
slice that triggered it, and declares — in `answer.yml` — the incidents
the classifier is expected to emit.

The suite runs offline: there is no LLM, no live infrastructure, and no
mocking. The classifier is rule-based, so the assertions are exact.

## Layout

```
tests/synthetic/hermes/
├── __init__.py
├── README.md                  ← this file
├── scenario_loader.py         ← loads scenarios into typed fixtures
├── test_suite.py              ← parametrized pytest entrypoint
└── 000-telegram-polling-conflict/
    ├── README.md              ← scenario narrative + real-world cause
    ├── errors.log             ← raw Hermes log fixture
    ├── scenario.yml           ← metadata (id, source, evidence files)
    └── answer.yml             ← expected incidents
```

## Scenario schema

### `scenario.yml`

```yaml
scenario_id: "000-telegram-polling-conflict"
title: "Two Hermes instances share a TELEGRAM_BOT_TOKEN"
source: "production-hermes-mac+ec2"
log_file: "errors.log"
classifier:
  warning_burst_threshold: 5
  warning_burst_window_s: 60
  traceback_followup_s: 5
```

The `classifier` block is optional; omit it to use the package
defaults. Override only when a scenario specifically needs different
thresholds.

### `answer.yml`

```yaml
expected_incidents:
  - rule: "warning_burst"
    severity: "medium"
    logger: "gateway.platforms.telegram"
    min_records: 5
expected_incident_count:
  warning_burst: ">=1"
  error_severity: "==0"
  traceback: "==0"
```

`expected_incidents` is an ordered list of partial matches: each entry
must match at least one emitted incident in the order given (without
consuming a previously matched incident). `expected_incident_count`
asserts the total per-rule emission count using the operators `==`,
`>=`, `<=`, `>`, `<`.

## Adding a new scenario

1. Capture a slice of the Hermes log that demonstrates the pattern.
   Strip irrelevant lines but keep timestamps intact — the classifier
   uses them for the warning-burst window.
2. Create a new numbered directory `NNN-short-name/` under this folder.
3. Drop the log into `errors.log` and write the two YAML files.
4. Run `uv run pytest tests/synthetic/hermes -q` to confirm the
   classifier matches your answer key.
5. Commit the scenario and document the real-world cause in the
   per-scenario `README.md`.
