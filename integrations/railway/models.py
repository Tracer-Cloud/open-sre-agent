from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RailwayScope:
    project: str
    service: str
    environment: str


@dataclass(frozen=True)
class DeploymentInfo:
    deployment_id: str
    status: str | None
    commit_hash: str | None
    commit_message: str | None


@dataclass(frozen=True)
class RedeployInfo:
    deployment_id: str | None
