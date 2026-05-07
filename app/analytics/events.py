"""Analytics event definitions."""

from __future__ import annotations

from enum import StrEnum


class Event(StrEnum):
    # Lifecycle
    CLI_INVOKED = "cli_invoked"
    REPL_EXECUTION_POLICY_DECISION = "repl_execution_policy_decision"
    INSTALL_DETECTED = "install_detected"
    USER_ID_LOAD_FAILED = "user_id_load_failed"
    SENTRY_INIT_SKIPPED = "sentry_init_skipped"

    # Onboarding
    ONBOARD_STARTED = "onboard_started"
    ONBOARD_COMPLETED = "onboard_completed"
    ONBOARD_FAILED = "onboard_failed"

    # Investigation
    INVESTIGATION_STARTED = "investigation_started"
    INVESTIGATION_COMPLETED = "investigation_completed"
    INVESTIGATION_FAILED = "investigation_failed"

    # Integrations
    INTEGRATION_SETUP_STARTED = "integration_setup_started"
    INTEGRATION_SETUP_COMPLETED = "integration_setup_completed"
    INTEGRATION_REMOVED = "integration_removed"
    INTEGRATION_VERIFIED = "integration_verified"
    INTEGRATIONS_LISTED = "integrations_listed"

    # Tests
    TESTS_PICKER_OPENED = "tests_picker_opened"
    TESTS_LISTED = "tests_listed"
    TEST_RUN_STARTED = "test_run_started"
    TEST_RUN_COMPLETED = "test_run_completed"
    TEST_RUN_FAILED = "test_run_failed"
    TEST_SYNTHETIC_STARTED = "test_synthetic_started"
    TEST_SYNTHETIC_COMPLETED = "test_synthetic_completed"
    TEST_SYNTHETIC_FAILED = "test_synthetic_failed"

    # Evaluation metrics
    EVAL_PROCESS_STARTED = "eval_process_started"
    EVAL_PROCESS_COMPLETED = "eval_process_completed"
    EVAL_PROCESS_FAILED = "eval_process_failed"
    EVAL_PROCESS_SKIPPED = "eval_process_skipped"
    EVAL_PROCESS_PARSE_FAILED = "eval_process_parse_failed"

    # Interactive terminal analytics
    TERMINAL_ACTIONS_PLANNED = "terminal_actions_planned"
    TERMINAL_ACTIONS_EXECUTED = "terminal_actions_executed"
    TERMINAL_TURN_SUMMARIZED = "terminal_turn_summarized"

    # Update
    UPDATE_STARTED = "update_started"
    UPDATE_COMPLETED = "update_completed"
    UPDATE_FAILED = "update_failed"

    # Deploy
    DEPLOY_STARTED = "deploy_started"
    DEPLOY_COMPLETED = "deploy_completed"
    DEPLOY_FAILED = "deploy_failed"
