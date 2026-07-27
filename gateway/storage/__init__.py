"""Gateway persistence: bindings, installs, and session resolution."""

from __future__ import annotations

from config.scope_context import bound_storage_scope, current_principal, current_scope
from gateway.storage.db import connect_gateway_db, default_gateway_db_path, gateway_dir
from gateway.storage.principal_resolve import (
    PrincipalResolutionError,
    resolve_slack_principal,
    resolve_slack_scope,
)
from gateway.storage.session import SessionBindingStore, SessionResolver
from gateway.storage.session.binding_store import open_binding_store
from gateway.storage.slack_installs import SlackInstall, get_slack_install, upsert_slack_install

__all__ = [
    "PrincipalResolutionError",
    "SessionBindingStore",
    "SessionResolver",
    "SlackInstall",
    "bound_storage_scope",
    "connect_gateway_db",
    "current_principal",
    "current_scope",
    "default_gateway_db_path",
    "gateway_dir",
    "get_slack_install",
    "open_binding_store",
    "resolve_slack_principal",
    "resolve_slack_scope",
    "upsert_slack_install",
]
