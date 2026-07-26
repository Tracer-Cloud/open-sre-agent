"""GitHub Projects V2 GraphQL operations."""

from __future__ import annotations

import logging
from typing import Any

from integrations.github.client import GitHubApiError, GitHubRestClient

logger = logging.getLogger(__name__)


def resolve_project_node_id(
    client: GitHubRestClient, owner: str, project_number: int
) -> str | None:
    """Resolve a GitHub project node ID from its owner and project number."""
    query = """
    query($owner: String!, $number: Int!) {
      organization(login: $owner) {
        projectV2(number: $number) {
          id
        }
      }
      user(login: $owner) {
        projectV2(number: $number) {
          id
        }
      }
    }
    """
    try:
        response = client.graphql(query, {"owner": owner, "number": project_number})
    except GitHubApiError as exc:
        logger.warning(
            "Failed to resolve project node ID for %s/%s: %s", owner, project_number, exc
        )
        return None

    if not isinstance(response, dict) or not isinstance(response.get("data"), dict):
        return None

    data = response["data"]
    org_project = data.get("organization")
    if org_project and isinstance(org_project, dict) and org_project.get("projectV2"):
        return str(org_project["projectV2"]["id"])

    user_project = data.get("user")
    if user_project and isinstance(user_project, dict) and user_project.get("projectV2"):
        return str(user_project["projectV2"]["id"])

    return None


def fetch_project_fields(client: GitHubRestClient, project_node_id: str) -> list[dict[str, Any]]:
    """Fetch all fields and options for a project."""
    query = """
    query($id: ID!) {
      node(id: $id) {
        ... on ProjectV2 {
          fields(first: 50) {
            nodes {
              ... on ProjectV2FieldCommon {
                id
                name
                dataType
              }
              ... on ProjectV2SingleSelectField {
                options {
                  id
                  name
                }
              }
              ... on ProjectV2IterationField {
                configuration {
                  iterations {
                    id
                    title
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    try:
        response = client.graphql(query, {"id": project_node_id})
    except GitHubApiError as exc:
        logger.warning("Failed to fetch fields for project %s: %s", project_node_id, exc)
        return []

    if not isinstance(response, dict) or not isinstance(response.get("data"), dict):
        return []

    node = response["data"].get("node")
    if not node or not isinstance(node, dict) or "fields" not in node:
        return []

    nodes = node["fields"].get("nodes", [])
    if isinstance(nodes, list):
        return [f for f in nodes if isinstance(f, dict)]
    return []


def add_issue_to_project_v2(
    client: GitHubRestClient, project_node_id: str, issue_node_id: str
) -> str | None:
    """Add an issue to a project and return the created item node ID."""
    query = """
    mutation($projectId: ID!, $contentId: ID!) {
      addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
        item {
          id
        }
      }
    }
    """
    try:
        response = client.graphql(query, {"projectId": project_node_id, "contentId": issue_node_id})
    except GitHubApiError as exc:
        logger.warning(
            "Failed to add issue %s to project %s: %s", issue_node_id, project_node_id, exc
        )
        return None

    if not isinstance(response, dict) or not isinstance(response.get("data"), dict):
        return None

    data = response["data"]
    mutation_res = data.get("addProjectV2ItemById")
    if mutation_res and isinstance(mutation_res, dict) and mutation_res.get("item"):
        return str(mutation_res["item"]["id"])
    return None


def update_project_v2_field(
    client: GitHubRestClient,
    project_node_id: str,
    item_node_id: str,
    field_node_id: str,
    value: dict[str, Any],
) -> bool:
    """Update a specific field on a project item.

    The value must be formatted as the expected GraphQL input object, e.g.:
    {"singleSelectOptionId": "..."} or {"text": "..."} or {"iterationId": "..."}
    """
    query = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $projectId,
        itemId: $itemId,
        fieldId: $fieldId,
        value: $value
      }) {
        projectV2Item {
          id
        }
      }
    }
    """
    try:
        client.graphql(
            query,
            {
                "projectId": project_node_id,
                "itemId": item_node_id,
                "fieldId": field_node_id,
                "value": value,
            },
        )
        return True
    except GitHubApiError as exc:
        logger.warning("Failed to update field %s on item %s: %s", field_node_id, item_node_id, exc)
        return False


def sync_project_fields(
    client: GitHubRestClient,
    owner: str,
    project_number: int,
    issue_node_id: str,
    project_fields: dict[str, str],
) -> bool:
    """Add an issue to a project and sync the provided fields."""
    project_node_id = resolve_project_node_id(client, owner, project_number)
    if not project_node_id:
        return False

    item_node_id = add_issue_to_project_v2(client, project_node_id, issue_node_id)
    if not item_node_id:
        return False

    if not project_fields:
        return True

    fields = fetch_project_fields(client, project_node_id)
    field_map = {f.get("name", "").lower(): f for f in fields}

    for name, value in project_fields.items():
        field = field_map.get(name.lower())
        if not field:
            logger.warning("Project field %s not found.", name)
            continue

        field_node_id = field.get("id")
        data_type = field.get("dataType")

        graphql_value: dict[str, Any] = {}

        if data_type == "SINGLE_SELECT":
            options = field.get("options", [])
            option = next(
                (o for o in options if o.get("name", "").lower() == str(value).lower()), None
            )
            if not option:
                logger.warning("Option %s not found for single select field %s.", value, name)
                continue
            graphql_value = {"singleSelectOptionId": option["id"]}
        elif data_type == "ITERATION":
            configuration = field.get("configuration", {})
            iterations = configuration.get("iterations", [])
            iteration = next(
                (i for i in iterations if i.get("title", "").lower() == str(value).lower()), None
            )
            if not iteration:
                logger.warning("Iteration %s not found for iteration field %s.", value, name)
                continue
            graphql_value = {"iterationId": iteration["id"]}
        elif data_type == "TEXT":
            graphql_value = {"text": str(value)}
        elif data_type == "NUMBER":
            try:
                graphql_value = {"number": float(value)}
            except ValueError:
                logger.warning("Invalid number %s for field %s.", value, name)
                continue
        elif data_type == "DATE":
            graphql_value = {"date": str(value)}
        else:
            logger.warning("Unsupported data type %s for field %s.", data_type, name)
            continue

        update_project_v2_field(client, project_node_id, item_node_id, field_node_id, graphql_value)

    return True
