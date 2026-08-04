"""Integration management CLI commands."""

from __future__ import annotations

import click

from platform.analytics.cli import (
    capture_integration_removed,
    capture_integration_setup_completed,
    capture_integration_setup_started,
    capture_integration_verified,
    capture_integrations_listed,
)
from surfaces.cli.constants import (
    MANAGED_INTEGRATION_SERVICES,
    SETUP_SERVICES,
    VERIFY_SERVICES,
)


class IntegrationServiceChoice(click.Choice):
    """``click.Choice`` that resolves integration-management service aliases.

    Applies ``resolve_management_service`` before the enum check so management-only
    aliases (when defined) are accepted while keeping Click's friendly
    ``[[a|b|c]]`` usage/error display and shell completion.
    """

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> object:
        if isinstance(value, str):
            from integrations.registry import resolve_management_service

            value = resolve_management_service(value)
        return super().convert(value, param, ctx)


@click.group(name="integrations")
def integrations() -> None:
    """Manage local integration credentials."""


@integrations.command(name="setup")
@click.argument(
    "service", required=False, default=None, type=IntegrationServiceChoice(SETUP_SERVICES)
)
def setup_integration(service: str | None) -> None:
    """Set up credentials for a service."""
    from integrations.cli import cmd_setup, cmd_verify

    normalized_service = service or "prompt"
    capture_integration_setup_started(normalized_service)
    resolved_service = cmd_setup(service)
    capture_integration_setup_completed(resolved_service)

    if resolved_service in VERIFY_SERVICES:
        click.echo(f"  Verifying {resolved_service}...\n")
        exit_code = cmd_verify(resolved_service)
        if exit_code == 0:
            capture_integration_verified(resolved_service)
        raise SystemExit(exit_code)


@integrations.command(name="list")
def list_integrations() -> None:
    """List all configured integrations."""
    from integrations.cli import cmd_list

    capture_integrations_listed()
    cmd_list()


@integrations.command(name="show")
@click.argument("service", type=IntegrationServiceChoice(MANAGED_INTEGRATION_SERVICES))
def show_integration(service: str) -> None:
    """Show details for a configured integration."""
    from integrations.cli import cmd_show

    cmd_show(service)


@integrations.command(name="remove")
@click.argument("service", type=IntegrationServiceChoice(MANAGED_INTEGRATION_SERVICES))
def remove_integration(service: str) -> None:
    """Remove a configured integration."""
    from integrations.cli import cmd_remove

    cmd_remove(service)
    capture_integration_removed(service)


@integrations.command(name="verify")
@click.argument(
    "service", required=False, default=None, type=IntegrationServiceChoice(VERIFY_SERVICES)
)
@click.option(
    "--send-slack-test", is_flag=True, help="Send a test message to the configured Slack webhook."
)
def verify_integration(
    service: str | None,
    send_slack_test: bool,
) -> None:
    """Verify integration connectivity (all services, or a specific one)."""
    from integrations.cli import cmd_verify

    exit_code = cmd_verify(
        service,
        send_slack_test=send_slack_test,
    )
    if exit_code == 0:
        capture_integration_verified(service or "all")
    raise SystemExit(exit_code)


def _parse_tags(pairs: tuple[str, ...]) -> dict[str, str]:
    """Parse repeatable ``KEY=VALUE`` ``--tag`` options into a dict."""
    tags: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ValueError(f"Invalid --tag '{pair}'; expected KEY=VALUE.")
        tags[key] = value.strip()
    return tags


@integrations.command(name="add-cluster")
@click.option("--name", default=None, help="Name for this cluster (e.g. gke-prod).")
@click.option("--kubeconfig-path", default="", help="Path to a kubeconfig file on disk.")
@click.option("--kubeconfig", "kubeconfig_inline", default="", help="Inline kubeconfig YAML.")
@click.option("--context", default="", help="Kubeconfig context (blank uses current-context).")
@click.option("--namespace", default="default", show_default=True, help="Default namespace.")
@click.option(
    "--tag",
    "tags",
    multiple=True,
    metavar="KEY=VALUE",
    help="Attach a tag; repeatable (e.g. --tag env=prod).",
)
@click.option("--no-verify", is_flag=True, help="Skip the connectivity probe before saving.")
def add_cluster_command(
    name: str | None,
    kubeconfig_path: str,
    kubeconfig_inline: str,
    context: str,
    namespace: str,
    tags: tuple[str, ...],
    no_verify: bool,
) -> None:
    """Register an additional named Kubernetes cluster for multi-cluster investigation.

    Each cluster is one GKE/EKS/etc. context — e.g. one GKE cluster per GCP
    project. Tools then target it with the ``cluster`` argument. The default
    cluster is still managed by ``opensre integrations setup kubernetes``.
    """
    from integrations.kubernetes.clusters import add_cluster
    from platform.common.exit_codes import ERROR, SUCCESS

    # Passing --name signals non-interactive intent (scripts/CI): use the flags
    # as given. Omitting it runs the interactive wizard, prompting for the rest.
    if name is None:
        name = click.prompt("Cluster name (e.g. gke-prod)").strip()
        if not kubeconfig_path and not kubeconfig_inline:
            kubeconfig_path = click.prompt("Kubeconfig file path", default="~/.kube/config").strip()
        if not kubeconfig_inline and not context:
            context = click.prompt(
                "Kubeconfig context (blank uses current-context)", default="", show_default=False
            ).strip()
        namespace = click.prompt("Default namespace", default=namespace).strip() or "default"

    try:
        parsed_tags = _parse_tags(tags)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(ERROR) from exc

    result = add_cluster(
        name=name,
        kubeconfig_path=kubeconfig_path,
        kubeconfig=kubeconfig_inline,
        context=context,
        namespace=namespace,
        tags=parsed_tags,
        verify=not no_verify,
    )
    click.echo(result.detail if result.ok else f"Error: {result.detail}", err=not result.ok)
    raise SystemExit(SUCCESS if result.ok else ERROR)


@integrations.command(name="list-clusters")
def list_clusters_command() -> None:
    """List the registered Kubernetes clusters you can target by name."""
    from integrations.kubernetes.clusters import list_clusters
    from platform.common.exit_codes import SUCCESS

    clusters = list_clusters()
    if not clusters:
        click.echo(
            "No Kubernetes clusters registered. Run 'opensre integrations setup kubernetes' "
            "or 'opensre integrations add-cluster'."
        )
        raise SystemExit(SUCCESS)
    for index, cluster in enumerate(clusters):
        marker = " (default)" if index == 0 else ""
        context = f"  context: {cluster.context}" if cluster.context else ""
        tag_str = f"  tags: {cluster.tags}" if cluster.tags else ""
        click.echo(f"- {cluster.name}{marker}  namespace: {cluster.namespace}{context}{tag_str}")
    raise SystemExit(SUCCESS)


@integrations.command(name="remove-cluster")
@click.argument("name")
def remove_cluster_command(name: str) -> None:
    """Remove a registered Kubernetes cluster by name."""
    from integrations.kubernetes.clusters import remove_cluster
    from platform.common.exit_codes import ERROR, SUCCESS

    result = remove_cluster(name)
    click.echo(result.detail if result.ok else f"Error: {result.detail}", err=not result.ok)
    raise SystemExit(SUCCESS if result.ok else ERROR)
