"""Railway integration verifier.

Beyond the usual probe (CLI present + authenticated), Railway carries a
cross-field rule: a token **or** a complete default project/service/environment
must be present for the integration to be usable. Enforcing it here — rather
than in a setup-only check — keeps ``opensre integrations setup railway`` and
``opensre integrations verify railway`` agreeing on what "configured" means.
"""

from __future__ import annotations

from typing import Any

from integrations.config_models import RailwayIntegrationConfig
from integrations.railway.client import RailwayClient
from integrations.verification.probe import result
from integrations.verification.registry import register_verifier


@register_verifier("railway")
def verify_railway(source: str, config: dict[str, Any]) -> dict[str, str]:
    try:
        normalized = RailwayIntegrationConfig.model_validate(config)
    except Exception as err:
        return result("railway", source, "missing", str(err))
    if not (normalized.token or normalized.has_default_scope):
        return result(
            "railway",
            source,
            "missing",
            "Provide a Railway token or a complete default project, service, and environment.",
        )
    try:
        probe = RailwayClient(normalized).probe_access()
    except Exception as err:
        return result("railway", source, "failed", str(err))
    return result("railway", source, probe.status, probe.detail)
