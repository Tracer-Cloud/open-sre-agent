"""Wire integrations-layer helpers into :mod:`platform.harness_ports`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path


def register_harness_adapters() -> None:
    from integrations.catalog import (
        classify_integrations,
        configured_integration_services,
        load_env_integrations,
        merge_integrations_by_service,
        merge_local_integrations,
    )
    from integrations.github.repo_scope import apply_github_repo_scope, infer_github_repo_scope
    from integrations.store import STORE_PATH, load_integrations
    from platform.harness_ports import (
        set_github_repo_scope_adapters,
        set_integration_resolution_adapters,
    )

    set_integration_resolution_adapters(
        load_integrations=load_integrations,
        integration_store_path=lambda: str(STORE_PATH),
        load_env_integrations=load_env_integrations,
        classify_integrations=classify_integrations,
        merge_local_integrations=merge_local_integrations,
        merge_integrations_by_service=merge_integrations_by_service,
        configured_services=lambda: tuple(configured_integration_services()),
    )

    def _infer(
        message: str,
        conversation_messages: Sequence[tuple[str, str]] | None,
        env: Mapping[str, str] | None,
        cwd: str | Path | None,
        cached: tuple[str, str] | None,
    ) -> tuple[str, str] | None:
        # Port uses positional args; integrations API is keyword-only.
        return infer_github_repo_scope(
            message=message,
            conversation_messages=conversation_messages,
            env=env,
            cwd=cwd,
            cached=cached,
        )

    set_github_repo_scope_adapters(infer_scope=_infer, apply_scope=apply_github_repo_scope)
