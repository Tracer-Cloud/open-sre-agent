# Product Funnel Analysis Query Pack

This document replaces the old dashboard pattern of comparing independent
`COUNT(DISTINCT fingerprint)` rows. Those rows are useful for event volume, but
they are not a funnel: each event is counted against its own population, so a
downstream event can appear to have more "unique users" than an upstream event.

The trusted funnel unit should be a stable install/user key. For current CLI
analytics, use `distinct_id` first. The CLI persists it in
`~/.config/opensre/anonymous_id` and sends it on every event. Use
`composite_fingerprint` only as a diagnostic fallback or cross-check because it
is derived from hashed local machine signals and can still fragment or merge
real people in shared environments.

## Root Causes

1. The old dashboard counts distinct fingerprints independently per event. It
   does not restrict each step to the cohort that reached the previous step.
2. The displayed stages mix linear and non-linear journeys. Onboarding,
   integration setup, tests, investigation, and deploy can happen in different
   orders.
3. Fingerprints are not stable enough to be the source of truth for conversion.
   A persisted `distinct_id` is the primary identifier; `composite_fingerprint`
   is for QA and backfill analysis.
4. Failure events must be measured against users who started the same flow.
   Counting `deploy_failed` as a standalone unique-user row overstates failure
   if users retry or emit failure telemetry without a matching start event in
   the same window.

## Query 1: Event and Identity Health

Run this before trusting any funnel. It verifies that the events exist and that
the new identity fields are populated.

```sql
SELECT
    event,
    count() AS event_count,
    count(DISTINCT distinct_id) AS unique_distinct_ids,
    count(DISTINCT JSONExtractString(properties, 'composite_fingerprint')) AS unique_fingerprints,
    countIf(empty(toString(distinct_id))) AS missing_distinct_id_events,
    countIf(empty(JSONExtractString(properties, 'composite_fingerprint'))) AS missing_fingerprint_events,
    countIf(JSONExtractString(properties, 'identity_persistence') = 'disk') AS disk_identity_events,
    countIf(JSONExtractString(properties, 'identity_persistence') != 'disk') AS non_disk_identity_events
FROM events
WHERE timestamp >= now() - INTERVAL 90 DAY
  AND event IN (
      'install_detected',
      'cli_invoked',
      'investigation_started',
      'investigation_completed',
      'investigation_failed',
      'deploy_started',
      'deploy_completed',
      'deploy_failed',
      'onboard_started',
      'onboard_completed',
      'integration_setup_started',
      'integration_setup_completed',
      'integration_verified',
      'tests_listed'
  )
GROUP BY event
ORDER BY event_count DESC
```

Interpretation:

- `missing_distinct_id_events` should be zero or near-zero after the rollout.
- `unique_fingerprints` may differ from `unique_distinct_ids`; that is expected
  and should not drive funnel conversion.
- `non_disk_identity_events` are useful for investigating read-only or broken
  local config environments.

## Query 2: Canonical Sequential Funnel

This is the leadership/product funnel for install to CLI to investigation to
deploy. It counts only users who first reached `install_detected`, then reached
each later step within 30 days and in order.

```sql
WITH now() - INTERVAL 120 DAY AS range_start
SELECT
    count() AS install_users,
    countIf(cli_at >= install_at AND cli_at <= install_at + INTERVAL 30 DAY) AS cli_users,
    round(100.0 * cli_users / install_users, 1) AS install_to_cli_pct,
    countIf(
        cli_at >= install_at
        AND investigation_at >= cli_at
        AND investigation_at <= install_at + INTERVAL 30 DAY
    ) AS investigation_users,
    round(100.0 * investigation_users / install_users, 1) AS install_to_investigation_pct,
    round(100.0 * investigation_users / nullIf(cli_users, 0), 1) AS cli_to_investigation_pct,
    countIf(
        cli_at >= install_at
        AND investigation_at >= cli_at
        AND deploy_at >= investigation_at
        AND deploy_at <= install_at + INTERVAL 30 DAY
    ) AS deploy_users,
    round(100.0 * deploy_users / install_users, 1) AS install_to_deploy_pct,
    round(100.0 * deploy_users / nullIf(investigation_users, 0), 1) AS investigation_to_deploy_pct
FROM (
    SELECT
        if(
            notEmpty(toString(distinct_id)),
            toString(distinct_id),
            concat('fp:', JSONExtractString(properties, 'composite_fingerprint'))
        ) AS user_key,
        minIf(timestamp, event = 'install_detected') AS install_at,
        minIf(timestamp, event = 'cli_invoked') AS cli_at,
        minIf(timestamp, event = 'investigation_started') AS investigation_at,
        minIf(timestamp, event = 'deploy_started') AS deploy_at
    FROM events
    WHERE timestamp >= range_start
      AND event IN (
          'install_detected',
          'cli_invoked',
          'investigation_started',
          'deploy_started'
      )
    GROUP BY user_key
    HAVING install_at > toDateTime('1970-01-02')
)
```

Notes:

- The date range is 120 days so a 30-day conversion window can be evaluated for
  installs from the last 90 days. For a complete historical backfill, extend the
  range.
- The fallback `fp:` key should shrink over time. If it remains material, fix
  identity persistence before using the numbers for executive reporting.

## Query 3: Funnel Stage Rows for a Dashboard

Use this when the dashboard needs one row per stage.

```sql
WITH now() - INTERVAL 120 DAY AS range_start
SELECT
    stage,
    event_name,
    users,
    round(100.0 * users / nullIf(install_users, 0), 1) AS pct_of_install_users,
    round(100.0 * users / nullIf(previous_users, 0), 1) AS pct_of_previous_stage
FROM (
    SELECT
        count() AS install_users,
        countIf(cli_at >= install_at AND cli_at <= install_at + INTERVAL 30 DAY) AS cli_users,
        countIf(
            cli_at >= install_at
            AND investigation_at >= cli_at
            AND investigation_at <= install_at + INTERVAL 30 DAY
        ) AS investigation_users,
        countIf(
            cli_at >= install_at
            AND investigation_at >= cli_at
            AND deploy_at >= investigation_at
            AND deploy_at <= install_at + INTERVAL 30 DAY
        ) AS deploy_users
    FROM (
        SELECT
            if(
                notEmpty(toString(distinct_id)),
                toString(distinct_id),
                concat('fp:', JSONExtractString(properties, 'composite_fingerprint'))
            ) AS user_key,
            minIf(timestamp, event = 'install_detected') AS install_at,
            minIf(timestamp, event = 'cli_invoked') AS cli_at,
            minIf(timestamp, event = 'investigation_started') AS investigation_at,
            minIf(timestamp, event = 'deploy_started') AS deploy_at
        FROM events
        WHERE timestamp >= range_start
          AND event IN (
              'install_detected',
              'cli_invoked',
              'investigation_started',
              'deploy_started'
          )
        GROUP BY user_key
        HAVING install_at > toDateTime('1970-01-02')
    )
)
ARRAY JOIN
    ['1. Installed', '2. CLI used', '3. Investigation started', '4. Deploy started'] AS stage,
    ['install_detected', 'cli_invoked', 'investigation_started', 'deploy_started'] AS event_name,
    [install_users, cli_users, investigation_users, deploy_users] AS users,
    [install_users, install_users, cli_users, investigation_users] AS previous_users
ORDER BY stage ASC
```

## Query 4: Branch Activation Paths

Onboarding, integrations, and tests are not strict linear steps. Measure them as
activation branches from the install cohort.

```sql
WITH now() - INTERVAL 120 DAY AS range_start
SELECT
    count() AS install_users,
    countIf(cli_at >= install_at AND cli_at <= install_at + INTERVAL 30 DAY) AS cli_users,
    countIf(onboard_started_at >= install_at AND onboard_started_at <= install_at + INTERVAL 30 DAY) AS onboard_started_users,
    countIf(onboard_completed_at >= onboard_started_at AND onboard_completed_at <= install_at + INTERVAL 30 DAY) AS onboard_completed_users,
    round(100.0 * onboard_completed_users / nullIf(onboard_started_users, 0), 1) AS onboard_completion_pct,
    countIf(integration_started_at >= install_at AND integration_started_at <= install_at + INTERVAL 30 DAY) AS integration_started_users,
    countIf(integration_completed_at >= integration_started_at AND integration_completed_at <= install_at + INTERVAL 30 DAY) AS integration_completed_users,
    countIf(integration_verified_at >= integration_started_at AND integration_verified_at <= install_at + INTERVAL 30 DAY) AS integration_verified_users,
    round(100.0 * integration_completed_users / nullIf(integration_started_users, 0), 1) AS integration_completion_pct,
    countIf(tests_listed_at >= install_at AND tests_listed_at <= install_at + INTERVAL 30 DAY) AS tests_listed_users
FROM (
    SELECT
        if(
            notEmpty(toString(distinct_id)),
            toString(distinct_id),
            concat('fp:', JSONExtractString(properties, 'composite_fingerprint'))
        ) AS user_key,
        minIf(timestamp, event = 'install_detected') AS install_at,
        minIf(timestamp, event = 'cli_invoked') AS cli_at,
        minIf(timestamp, event = 'onboard_started') AS onboard_started_at,
        minIf(timestamp, event = 'onboard_completed') AS onboard_completed_at,
        minIf(timestamp, event = 'integration_setup_started') AS integration_started_at,
        minIf(timestamp, event = 'integration_setup_completed') AS integration_completed_at,
        minIf(timestamp, event = 'integration_verified') AS integration_verified_at,
        minIf(timestamp, event = 'tests_listed') AS tests_listed_at
    FROM events
    WHERE timestamp >= range_start
      AND event IN (
          'install_detected',
          'cli_invoked',
          'onboard_started',
          'onboard_completed',
          'integration_setup_started',
          'integration_setup_completed',
          'integration_verified',
          'tests_listed'
      )
    GROUP BY user_key
    HAVING install_at > toDateTime('1970-01-02')
)
```

## Query 5: Failure Rates Tied to Starts

Use this to separate real flow failures from standalone failure telemetry.

```sql
WITH now() - INTERVAL 120 DAY AS range_start
SELECT
    countIf(investigation_started_at > toDateTime('1970-01-02')) AS investigation_started_users,
    countIf(investigation_completed_at >= investigation_started_at) AS investigation_completed_users,
    countIf(investigation_failed_at >= investigation_started_at) AS investigation_failed_users,
    countIf(
        investigation_failed_at > toDateTime('1970-01-02')
        AND investigation_started_at <= toDateTime('1970-01-02')
    ) AS investigation_failed_without_start_users,
    round(100.0 * investigation_failed_users / nullIf(investigation_started_users, 0), 1) AS investigation_failure_pct,
    countIf(deploy_started_at > toDateTime('1970-01-02')) AS deploy_started_users,
    countIf(deploy_completed_at >= deploy_started_at) AS deploy_completed_users,
    countIf(deploy_failed_at >= deploy_started_at) AS deploy_failed_users,
    countIf(
        deploy_failed_at > toDateTime('1970-01-02')
        AND deploy_started_at <= toDateTime('1970-01-02')
    ) AS deploy_failed_without_start_users,
    round(100.0 * deploy_failed_users / nullIf(deploy_started_users, 0), 1) AS deploy_failure_pct
FROM (
    SELECT
        if(
            notEmpty(toString(distinct_id)),
            toString(distinct_id),
            concat('fp:', JSONExtractString(properties, 'composite_fingerprint'))
        ) AS user_key,
        minIf(timestamp, event = 'investigation_started') AS investigation_started_at,
        minIf(timestamp, event = 'investigation_completed') AS investigation_completed_at,
        minIf(timestamp, event = 'investigation_failed') AS investigation_failed_at,
        minIf(timestamp, event = 'deploy_started') AS deploy_started_at,
        minIf(timestamp, event = 'deploy_completed') AS deploy_completed_at,
        minIf(timestamp, event = 'deploy_failed') AS deploy_failed_at
    FROM events
    WHERE timestamp >= range_start
      AND event IN (
          'investigation_started',
          'investigation_completed',
          'investigation_failed',
          'deploy_started',
          'deploy_completed',
          'deploy_failed'
      )
    GROUP BY user_key
)
```

## Dashboard Recommendations

1. Replace the current table with Query 3 as the canonical conversion view.
2. Add Query 4 as a separate "Activation branches" view instead of forcing
   onboarding, integrations, and tests into a single sequence.
3. Add Query 5 as the health view for investigations and deploys.
4. Keep Query 1 as a hidden QA tile. Alert if `missing_distinct_id_events` or
   `non_disk_identity_events` materially increases.

## Product Bottleneck Method

After running the queries, identify bottlenecks using this order:

1. The lowest `pct_of_previous_stage` in Query 3 is the main linear funnel
   bottleneck.
2. In Query 4, compare branch starts from install users and completion rates
   from branch starters. A low start rate is discovery or motivation; a low
   completion rate is friction inside that branch.
3. In Query 5, inspect `*_failed_without_start_users` first. If this is high,
   instrumentation is noisy. If it is low and failure rates are high, the
   product flow is genuinely failing.

## Proposed Tracking Changes

Already implemented in the current analytics branch:

- Persist `distinct_id` in `~/.config/opensre/anonymous_id`.
- Emit `identity_persistence` so dashboards can filter or audit fallback IDs.
- Emit hashed `composite_fingerprint` and its version/components for backfill
  and fragmentation analysis.
- Emit deterministic `$insert_id` for `install_detected` so retries do not
  duplicate one-time install rows.

Recommended next changes:

- Add a `flow_id` for investigation and deploy attempts so failure/completion
  can be tied to a specific start, not just a user.
- Add `account_id` or authenticated `user_id` when cloud login exists. Use that
  as the reporting identity and keep `distinct_id` as install/device identity.
- Add dashboard filters for `cli_version`, `identity_persistence`, and
  `composite_fingerprint_version` so rollout effects can be isolated.

Effort estimates:

- Replace dashboard SQL with the queries above: 0.5 day.
- Add branch and failure-health dashboard tiles: 0.5 day.
- Add `flow_id` instrumentation for investigation and deploy attempts: 1 day.
- Add authenticated account/user identity once login is available in the CLI:
  1 to 2 days, depending on auth state access.
