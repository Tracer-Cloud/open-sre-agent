"""Integration management CLI commands."""

from __future__ import annotations

import click

from config.constants.kubernetes import KUBECONFIG_CONTENT_ENV, KUBERNETES_INSTANCES_ENV
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


@integrations.command(name="add-gke-clusters")
@click.option(
    "--project",
    default="",
    help="GCP project to scan. Omit for the default project, comma-separate several, or '*'.",
)
@click.option(
    "--cluster",
    "clusters",
    multiple=True,
    metavar="NAME",
    help="Register only this cluster; repeatable. Omit to register every cluster found.",
)
@click.option(
    "--tag",
    "tags",
    multiple=True,
    metavar="KEY=VALUE",
    help="Attach a tag to every cluster registered; repeatable (e.g. --tag env=prod).",
)
@click.option(
    "--overwrite",
    is_flag=True,
    help="Replace an existing instance whose name matches but points at another cluster.",
)
@click.option("--no-verify", is_flag=True, help="Skip the connectivity probe before saving.")
@click.option("--dry-run", is_flag=True, help="Report what would be registered; change nothing.")
def add_gke_clusters_command(
    project: str,
    clusters: tuple[str, ...],
    tags: tuple[str, ...],
    overwrite: bool,
    no_verify: bool,
    dry_run: bool,
) -> None:
    """Discover GKE clusters in your GCP projects and register them for investigation.

    Each cluster becomes a named Kubernetes instance the ``kubernetes_*`` tools
    can target, so ``gcp_list_gke_clusters`` stops reporting it as unregistered.
    Re-running is safe: clusters already registered are skipped.

    The generated kubeconfig stores no credentials — it delegates to
    ``gke-gcloud-auth-plugin``, which must be on PATH.
    """
    from integrations.catalog import resolve_local_classified_integrations
    from integrations.gcp.gke import (
        AUTH_PLUGIN,
        Outcome,
        env_declares_kubernetes,
        plugin_installed,
        register_gke_clusters,
        scopes_from_cluster_names,
    )
    from platform.common.exit_codes import ERROR, SUCCESS

    try:
        parsed_tags = _parse_tags(tags)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(ERROR) from exc

    if env_declares_kubernetes() and not dry_run:
        # The store overrides the environment for the whole kubernetes service,
        # not per instance, so the first cluster written here makes every cluster
        # declared in the environment disappear from the effective config. Nothing
        # is destroyed — clearing the store brings them back — but the loss is
        # silent, and worth saying out loud before it happens.
        #
        # Same helper the boot-time path uses, so the warning and the stand-down
        # cannot disagree about what "the environment declares a cluster" means.
        click.echo(
            f"Warning: the environment already declares the kubernetes integration "
            f"({KUBERNETES_INSTANCES_ENV} or {KUBECONFIG_CONTENT_ENV}). Registering a "
            "cluster in the local store makes the store authoritative for kubernetes, "
            "so those clusters will stop being used until the store entry is removed. "
            "Declare this cluster in the environment instead to keep one source of truth.",
            err=True,
        )

    verify = not no_verify
    if not plugin_installed():
        # Without the plugin every kubeconfig written here is inert, so a probe
        # would fail for a reason that has nothing to do with the cluster. Say
        # so once, up front, instead of once per cluster.
        message = (
            f"'{AUTH_PLUGIN}' is not on PATH. Install it with: "
            "gcloud components install gke-gcloud-auth-plugin"
        )
        if verify and not dry_run:
            click.echo(f"Error: {message}", err=True)
            raise SystemExit(ERROR)
        click.echo(f"Warning: {message}", err=True)

    report = register_gke_clusters(
        # Classified, not "effective": `register_gke_clusters` feeds this to
        # `gcp_tool_params`, which reads the same flat-config shape the tools
        # get at runtime. The effective shape nests the config under a "config"
        # key, and the sanitizer drops what it does not recognise — so passing
        # it reports "no GCP projects are configured" with GCP_PROJECT_ID set.
        resolved=resolve_local_classified_integrations(),
        project=project,
        # --cluster filters what --project discovered; passing no names leaves
        # the spec empty, which admits everything and keeps the default behavior.
        cluster_scope=scopes_from_cluster_names(clusters) if clusters else None,
        tags=parsed_tags,
        overwrite=overwrite,
        verify=verify,
        dry_run=dry_run,
    )

    for result in report.results:
        marker = {Outcome.REGISTERED: "+", Outcome.SKIPPED: "=", Outcome.FAILED: "!"}[
            result.outcome
        ]
        click.echo(
            f"{marker} {result.cluster} ({result.project}) -> {result.instance}: {result.detail}"
        )
    for problem in report.errors:
        click.echo(f"! {problem}", err=True)

    if report.excluded:
        # Naming them turns the one real failure mode of --cluster — a typo —
        # from "nothing happened" into a spelling the operator can compare
        # against, without a second discovery run.
        click.echo(f"- skipped by --cluster: {', '.join(report.excluded)}")

    if report.no_gke:
        # Not an error — a project without the API cannot hold a cluster. Named
        # anyway, because the way --project goes wrong is a project the operator
        # expected clusters in that turns out to have none.
        click.echo(f"- no Kubernetes Engine API: {', '.join(report.no_gke)}")

    if not report.results and not report.errors:
        click.echo(
            "No GKE clusters found in the configured projects."
            if not report.excluded
            else "No GKE cluster matched --cluster; the names above are what was found."
        )

    registered = report.count(Outcome.REGISTERED)
    failed = report.count(Outcome.FAILED)
    verb = "would register" if dry_run else "registered"
    click.echo(f"{verb} {registered}, skipped {report.count(Outcome.SKIPPED)}, failed {failed}.")
    raise SystemExit(ERROR if failed or report.errors else SUCCESS)
