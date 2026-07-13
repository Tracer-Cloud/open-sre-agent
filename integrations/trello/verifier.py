"""Trello credential and connectivity verification."""

import logging
from dataclasses import dataclass

import httpx

import integrations.trello.client as client
from integrations._validation_helpers import report_validation_failure
from integrations.trello.config import TrelloConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrelloValidationResult:
    """Result of validating a Trello integration."""

    ok: bool
    detail: str


def validate_trello_config(config: TrelloConfig) -> TrelloValidationResult:
    """Validate Trello connectivity."""
    if not config.api_key:
        return TrelloValidationResult(ok=False, detail="Trello API key is required.")
    if not config.token:
        return TrelloValidationResult(ok=False, detail="Trello token is required.")

    try:
        member = client.validate_trello_connection(config=config)
        username = member.get("username", "unknown")
        return TrelloValidationResult(
            ok=True,
            detail=f"Trello connectivity successful. Authenticated as @{username}",
        )
    except httpx.HTTPStatusError as err:
        detail = err.response.text.strip() or str(err)
        return TrelloValidationResult(ok=False, detail=f"Trello validation failed: {detail}")
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="trello",
            method="validate_trello_config",
        )
        return TrelloValidationResult(ok=False, detail=f"Trello validation failed: {err}")
