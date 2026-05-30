"""GraphQL query strings issued by the Dagster client.

Each constant is the GraphQL document for one tool's primary query. Variables
are passed alongside the query in the POST body. Query shapes verified
against the Dagster GraphQL schema dump at
``js_modules/ui-core/src/graphql/schema.graphql`` in the upstream repo,
plus the canonical ``.graphql`` examples in the
``dagster-rest-resources`` library.

Notes on Dagster's schema worth knowing when reading or editing these:

- Pipeline-to-job rename: prefer ``jobName`` over ``pipelineName`` for new
  callers. ``pipelineName`` survives as a legacy alias.
- ``runsOrError`` is a three-member union: ``Runs | InvalidPipelineRunsFilterError | PythonError``.
  All three must be handled.
- Event log access goes through the top-level ``logsForRun`` query returning
  ``EventConnectionOrError``. The events are a ``DagsterRunEvent`` union, so
  inline fragments per event type are required to read error details.
- ``SensorSelector`` requires all three of ``repositoryName``,
  ``repositoryLocationName``, ``sensorName``. Just the sensor name is not
  enough to identify a sensor in Dagster.
"""

from __future__ import annotations

# List recent runs, optionally filtered by RunStatus values.
# Valid statuses (from schema): QUEUED, NOT_STARTED, MANAGED, STARTING,
# STARTED, SUCCESS, FAILURE, CANCELING, CANCELED.
LIST_RUNS = """
query ListRuns($limit: Int!, $statuses: [RunStatus!], $pipelineName: String) {
  runsOrError(filter: {statuses: $statuses, pipelineName: $pipelineName}, limit: $limit) {
    __typename
    ... on Runs {
      results {
        runId
        status
        jobName
        startTime
        endTime
        creationTime
      }
      count
    }
    ... on InvalidPipelineRunsFilterError {
      message
    }
    ... on PythonError {
      message
    }
  }
}
"""

# Fetch a run's event log via top-level logsForRun (preferred over
# runOrError.eventConnection); events are a DagsterRunEvent union.
GET_RUN_LOGS = """
query GetRunLogs($runId: ID!, $limit: Int, $afterCursor: String) {
  logsForRun(runId: $runId, limit: $limit, afterCursor: $afterCursor) {
    __typename
    ... on EventConnection {
      events {
        __typename
        ... on MessageEvent {
          runId
          message
          timestamp
          level
          stepKey
          eventType
        }
        ... on ExecutionStepFailureEvent {
          error {
            message
            stack
            className
            cause {
              message
              stack
              className
            }
          }
        }
        ... on RunFailureEvent {
          error {
            message
            stack
            className
            cause {
              message
              stack
              className
            }
          }
        }
      }
      cursor
      hasMore
    }
    ... on RunNotFoundError {
      message
    }
    ... on PythonError {
      message
    }
  }
}
"""

# List assets with their most recent materialization (limit=1 per asset).
LIST_ASSETS = """
query ListAssets($limit: Int!) {
  assetsOrError(limit: $limit) {
    __typename
    ... on AssetConnection {
      nodes {
        key {
          path
        }
        assetMaterializations(limit: 1) {
          timestamp
          runId
          partition
        }
      }
      cursor
    }
    ... on PythonError {
      message
    }
  }
}
"""

LIST_SENSOR_TICKS = """
query SensorTicks($sensorSelector: SensorSelector!, $limit: Int) {
  sensorOrError(sensorSelector: $sensorSelector) {
    __typename
    ... on Sensor {
      name
      sensorState {
        ticks(limit: $limit) {
          id
          status
          timestamp
          endTimestamp
          runIds
          skipReason
          error {
            message
            stack
          }
        }
      }
    }
    ... on SensorNotFoundError {
      message
    }
    ... on PythonError {
      message
    }
  }
}
"""

LIST_SCHEDULE_TICKS = """
query ScheduleTicks($scheduleSelector: ScheduleSelector!, $limit: Int) {
  scheduleOrError(scheduleSelector: $scheduleSelector) {
    __typename
    ... on Schedule {
      name
      scheduleState {
        ticks(limit: $limit) {
          id
          status
          timestamp
          endTimestamp
          runIds
          skipReason
          error {
            message
            stack
          }
        }
      }
    }
    ... on ScheduleNotFoundError {
      message
    }
    ... on PythonError {
      message
    }
  }
}
"""
