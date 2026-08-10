"""Cloud SQL ``DatabaseInstance`` normalization.

The managed database under a failing service. Four things about it explain most
incidents, and none of them are visible from the application side: the instance
is not RUNNABLE, it is mid-failover or in maintenance, it is out of disk, or the
caller is talking to a read replica and wondering why writes fail. Those are the
fields kept here.

Disk is the one worth spelling out. ``currentDiskSize`` against
``settings.dataDiskSizeGb`` gives the ratio, and Cloud SQL separately publishes
``outOfDiskReport`` once it has decided the instance is in trouble — an instance
that fills its disk stops accepting writes and reports itself RUNNABLE
throughout, so the state field alone never shows it.

Kept separate from the tool entrypoint so shape handling is testable without an
API client.
"""

from __future__ import annotations

from typing import Any

#: The only state in which an instance is unambiguously serving. SUSPENDED,
#: MAINTENANCE, FAILED, REPAIRING and the PENDING_* states are all worth
#: surfacing, so ``healthy`` is a whitelist.
RUNNABLE = "RUNNABLE"

#: ``instanceType`` of an instance that accepts writes.
PRIMARY = "CLOUD_SQL_INSTANCE"

#: Fraction of provisioned disk above which an instance is worth flagging.
#: Cloud SQL's own automatic-increase trigger sits near this, and a database
#: refusing writes for want of disk reads as an application fault until someone
#: looks here.
DISK_WARN_RATIO = 0.85

_BYTES_PER_GB = 1024**3


def _sub_object(parent: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``parent[key]`` when it is an object, otherwise an empty one."""
    value = parent.get(key)
    return value if isinstance(value, dict) else {}


def _as_int(value: Any) -> int | None:
    """Parse Cloud SQL's string-encoded int64 fields."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def disk_usage(instance: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Return provisioned/used disk in GB plus the used fraction.

    ``currentDiskSize`` is bytes while ``dataDiskSizeGb`` is gigabytes; mixing
    the two is the obvious way to get this wrong, so the conversion happens once,
    here.
    """
    used_bytes = _as_int(instance.get("currentDiskSize"))
    size_gb = _as_int(settings.get("dataDiskSizeGb"))
    if used_bytes is None or not size_gb:
        return {}
    used_gb = used_bytes / _BYTES_PER_GB
    return {
        "disk_size_gb": size_gb,
        "disk_used_gb": round(used_gb, 2),
        "disk_used_ratio": round(used_gb / size_gb, 3),
    }


def _ip_addresses(instance: dict[str, Any]) -> list[str]:
    """Render the instance's IPs as ``TYPE:address`` strings."""
    raw = instance.get("ipAddresses")
    if not isinstance(raw, list):
        return []
    rendered: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        address = str(entry.get("ipAddress", "")).strip()
        if not address:
            continue
        kind = str(entry.get("type", "")).strip()
        rendered.append(f"{kind}:{address}" if kind else address)
    return rendered


def _maintenance(instance: dict[str, Any]) -> dict[str, Any]:
    """Return the scheduled-maintenance window, when one is pending."""
    scheduled = _sub_object(instance, "scheduledMaintenance")
    start = str(scheduled.get("startTime", "")).strip()
    if not start:
        return {}
    window: dict[str, Any] = {"starts_at": start}
    deadline = str(scheduled.get("scheduleDeadlineTime", "")).strip()
    if deadline:
        window["deadline"] = deadline
    window["can_defer"] = bool(scheduled.get("canDefer"))
    return window


def normalize_instance(instance: dict[str, Any], project: str) -> dict[str, Any]:
    """Flatten one Cloud SQL instance into the compact shape the agent consumes."""
    settings = _sub_object(instance, "settings")
    backup = _sub_object(settings, "backupConfiguration")
    out_of_disk = _sub_object(instance, "outOfDiskReport")
    state = str(instance.get("state", ""))
    instance_type = str(instance.get("instanceType", ""))
    usage = disk_usage(instance, settings)
    ratio = usage.get("disk_used_ratio")
    disk_pressure = isinstance(ratio, float) and ratio >= DISK_WARN_RATIO
    reasons = instance.get("suspensionReason")
    labels = settings.get("userLabels")

    normalized: dict[str, Any] = {
        "project": project,
        "name": str(instance.get("name", "")),
        "state": state,
        "healthy": state == RUNNABLE and not disk_pressure,
        "database_version": str(
            instance.get("databaseInstalledVersion") or instance.get("databaseVersion") or ""
        ),
        "region": str(instance.get("region", "")),
        "zone": str(instance.get("gceZone", "")),
        "tier": str(settings.get("tier", "")),
        # REGIONAL means a standby in a second zone; ZONAL has none, so a zone
        # outage is an outage rather than a failover.
        "availability_type": str(settings.get("availabilityType", "")),
        "instance_type": instance_type,
        "accepts_writes": instance_type == PRIMARY,
        "connection_name": str(instance.get("connectionName", "")),
        "backups_enabled": bool(backup.get("enabled")),
    }
    normalized.update(usage)

    if disk_pressure:
        normalized["disk_pressure"] = True
    state_of_disk = str(out_of_disk.get("sqlOutOfDiskState", "")).strip()
    if state_of_disk:
        normalized["out_of_disk_state"] = state_of_disk
    primary = str(instance.get("masterInstanceName", "")).strip()
    if primary:
        # Present only on replicas, and the answer to "why is this read-only".
        normalized["replica_of"] = primary.rsplit(":", 1)[-1]
    replicas = instance.get("replicaNames")
    if isinstance(replicas, list) and replicas:
        normalized["replicas"] = [str(name).rsplit(":", 1)[-1] for name in replicas]
    failover = _sub_object(instance, "failoverReplica")
    if failover:
        normalized["failover_replica_available"] = bool(failover.get("available"))
    maintenance = _maintenance(instance)
    if maintenance:
        normalized["scheduled_maintenance"] = maintenance
    if isinstance(reasons, list) and reasons:
        normalized["suspension_reasons"] = [str(reason) for reason in reasons]
    addresses = _ip_addresses(instance)
    if addresses:
        normalized["ip_addresses"] = addresses
    if isinstance(labels, dict) and labels:
        normalized["labels"] = {str(key): str(value) for key, value in labels.items()}
    return normalized


def normalize_instances(instances: list[Any], project: str) -> list[dict[str, Any]]:
    """Normalize a listing, skipping anything that is not an object."""
    return [normalize_instance(item, project) for item in instances if isinstance(item, dict)]
