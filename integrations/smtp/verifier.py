"""SMTP integration verifier — connection probe via verify_smtp_connection."""

from __future__ import annotations

from typing import Any

from integrations.config_models import SMTPIntegrationConfig
from integrations.verification import register_verifier, result


@register_verifier("smtp")
def verify_smtp(source: str, config: dict[str, Any]) -> dict[str, str]:
    try:
        smtp_config = SMTPIntegrationConfig.model_validate(config)
    except Exception as err:
        return result("smtp", source, "missing", str(err))

    from integrations.smtp.delivery import verify_smtp_connection

    ok, detail = verify_smtp_connection(smtp_config.model_dump())
    return result("smtp", source, "passed" if ok else "failed", detail)
