"""Report every registered instance of a service, not just the probed one.

Verification probes the **default** instance, because that is the flat config
the effective entry carries (see ``integrations.selectors``). For a service
registered once that is the whole story. For a service registered several
times — eight GKE clusters under ``kubernetes``, two Grafana orgs — the result
read as though a single instance existed, and that line is what an operator
(or an agent quoting ``/integrations show``) then reports.

Probing every instance is the other way to fix it and is deliberately not done
here: ``verify_integrations`` already fans out across 50+ services on a bounded
pool, and each extra instance is another live round trip on that budget.
Instances registered through auto-discovery were probed at registration time
anyway.
"""

from __future__ import annotations

from typing import Any

# A large estate would otherwise push the probe's own result off the end of the
# detail column, which is the part that says whether anything works.
MAX_LISTED_INSTANCES = 8


def instance_names(integration: dict[str, Any]) -> list[str]:
    """Return the named instances published on an effective integration entry.

    ``instances`` is only published when there is more than one, or when the
    single one is not called ``default`` — so an empty list here means "nothing
    worth reporting beyond the probe", not "misconfigured".
    """
    instances = integration.get("instances")
    if not isinstance(instances, list):
        return []
    names = [
        str(instance.get("name", "")).strip()
        for instance in instances
        if isinstance(instance, dict)
    ]
    return [name for name in names if name]


def with_instance_inventory(
    outcome: dict[str, str],
    integration: dict[str, Any],
) -> dict[str, str]:
    """Append the registered-instance inventory to a verification *outcome*.

    Returns *outcome* unchanged when the service has fewer than two instances.
    Applied on the failure path too: knowing eight clusters are registered is
    useful even when the one that was probed is down.
    """
    names = instance_names(integration)
    if len(names) < 2:
        return outcome

    listed = ", ".join(names[:MAX_LISTED_INSTANCES])
    if len(names) > MAX_LISTED_INSTANCES:
        listed = f"{listed}, +{len(names) - MAX_LISTED_INSTANCES} more"
    note = f"{len(names)} instances registered ({listed}); this check probed '{names[0]}' only."

    detail = str(outcome.get("detail", "")).strip()
    return {**outcome, "detail": f"{detail} {note}" if detail else note}


__all__ = ["MAX_LISTED_INSTANCES", "instance_names", "with_instance_inventory"]
