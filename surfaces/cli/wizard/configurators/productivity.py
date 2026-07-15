"""Configurator handlers for productivity/ticketing integrations."""

from __future__ import annotations

from integrations.store import upsert_integration
from platform.terminal.theme import SECONDARY
from surfaces.cli.wizard._ui import (
    _console,
    _integration_defaults,
    _prompt_value,
    _render_integration_result,
    _string_value,
)
from surfaces.cli.wizard.env_sync import sync_env_values
from surfaces.cli.wizard.integration_health import (
    validate_google_docs_integration,
    validate_jira_integration,
    validate_notion_integration,
)


def _configure_notion() -> tuple[str, str]:
    _, credentials = _integration_defaults("notion")
    _console.print("\n[bold]Notion Integration[/bold]")
    _console.print("Create an internal integration at https://www.notion.so/my-integrations")
    _console.print("then share your target database with the integration.\n")

    while True:
        api_key = _prompt_value("Notion API key (secret_...)", secret=True)
        database_id = _prompt_value("Notion database ID")

        with _console.status("Validating Notion connection...", spinner="dots"):
            result = validate_notion_integration(api_key=api_key, database_id=database_id)
        _render_integration_result("Notion", result)

        if result.ok:
            upsert_integration(
                "notion", {"credentials": {"api_key": api_key, "database_id": database_id}}
            )
            env_path = sync_env_values({"NOTION_DATABASE_ID": database_id})
            return "Notion", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_jira() -> tuple[str, str]:
    _, credentials = _integration_defaults("jira")
    _console.print("\n[bold]Jira Integration[/bold]")
    _console.print(
        "Create an API token at https://id.atlassian.com/manage-profile/security/api-tokens\n"
    )

    while True:
        base_url = _prompt_value("Jira base URL (e.g. https://myteam.atlassian.net)")
        email = _prompt_value("Jira account email")
        api_token = _prompt_value("Jira API token", secret=True)
        project_key = _prompt_value("Jira project key (e.g. OPS)")

        with _console.status("Validating Jira connection...", spinner="dots"):
            result = validate_jira_integration(
                base_url=base_url,
                email=email,
                api_token=api_token,
                project_key=project_key,
            )
        _render_integration_result("Jira", result)

        if result.ok:
            upsert_integration(
                "jira",
                {
                    "credentials": {
                        "base_url": base_url,
                        "email": email,
                        "api_token": api_token,
                        "project_key": project_key,
                    }
                },
            )
            env_path = sync_env_values({})
            return "Jira", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_google_docs() -> tuple[str, str]:
    _, credentials = _integration_defaults("google_docs")
    while True:
        credentials_file = _prompt_value(
            "Path to Google service account credentials JSON file",
            default=_string_value(credentials.get("credentials_file")),
        )
        folder_id = _prompt_value(
            "Google Drive folder ID for incident reports",
            default=_string_value(credentials.get("folder_id")),
        )
        with _console.status("Validating Google Docs integration...", spinner="dots"):
            result = validate_google_docs_integration(
                credentials_file=credentials_file,
                folder_id=folder_id,
            )
        _render_integration_result("Google Docs", result)
        if result.ok:
            upsert_integration(
                "google_docs",
                {
                    "credentials": {
                        "credentials_file": credentials_file,
                        "folder_id": folder_id,
                    }
                },
            )
            env_path = sync_env_values(
                {
                    "GOOGLE_CREDENTIALS_FILE": credentials_file,
                    "GOOGLE_DRIVE_FOLDER_ID": folder_id,
                }
            )
            return "Google Docs", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")
