"""Configurator handlers for productivity/ticketing integrations."""

from __future__ import annotations

from config.env_file import sync_env_values
from infrastructure.terminal.theme import SECONDARY
from integrations.google_docs import GOOGLE_DOCS_SETUP
from integrations.servicenow import SERVICENOW_SETUP
from integrations.store import upsert_integration
from surfaces.cli.wizard.components import (
    console,
    integration_defaults,
    prompt_value,
)
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec
from surfaces.cli.wizard.integration_health import (
    validate_jira_integration,
    validate_notion_integration,
)
from surfaces.cli.wizard.summaries import render_integration_result


def _configure_notion() -> tuple[str, str]:
    _, credentials = integration_defaults("notion")
    console.print("\n[bold]Notion Integration[/bold]")
    console.print("Create an internal integration at https://www.notion.so/my-integrations")
    console.print("then share your target database with the integration.\n")

    while True:
        api_key = prompt_value("Notion API key (secret_...)", secret=True)
        database_id = prompt_value("Notion database ID")

        with console.status("Validating Notion connection...", spinner="dots"):
            result = validate_notion_integration(api_key=api_key, database_id=database_id)
        render_integration_result("Notion", result)

        if result.ok:
            upsert_integration(
                "notion", {"credentials": {"api_key": api_key, "database_id": database_id}}
            )
            env_path = sync_env_values({"NOTION_DATABASE_ID": database_id})
            return "Notion", str(env_path)
        console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_jira() -> tuple[str, str]:
    _, credentials = integration_defaults("jira")
    console.print("\n[bold]Jira Integration[/bold]")
    console.print(
        "Create an API token at https://id.atlassian.com/manage-profile/security/api-tokens\n"
    )

    while True:
        base_url = prompt_value("Jira base URL (e.g. https://myteam.atlassian.net)")
        email = prompt_value("Jira account email")
        api_token = prompt_value("Jira API token", secret=True)
        project_key = prompt_value("Jira project key (e.g. OPS)")

        with console.status("Validating Jira connection...", spinner="dots"):
            result = validate_jira_integration(
                base_url=base_url,
                email=email,
                api_token=api_token,
                project_key=project_key,
            )
        render_integration_result("Jira", result)

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
        console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_servicenow() -> tuple[str, str]:
    return configure_from_spec(
        SERVICENOW_SETUP,
        title="ServiceNow",
        intro=(
            "\n[bold]ServiceNow Integration[/bold]\n"
            "Use a user with read access to the sys_user table "
            "(a free developer instance from https://developer.servicenow.com works).\n"
        ),
    )


def _configure_google_docs() -> tuple[str, str]:
    return configure_from_spec(
        GOOGLE_DOCS_SETUP,
        title="Google Docs",
        intro=(
            "\n[bold]Google Docs Integration[/bold]\n"
            "Use a service account JSON key and share a Drive folder with that "
            "account as Editor.\n"
        ),
    )
