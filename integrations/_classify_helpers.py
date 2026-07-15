"""Shared validate-classify wrapper for integration vendor classifiers.

Consolidates the repeated try-except-validate-check pattern across vendor
``classify()`` functions into a single reusable helper. Ensures consistent
error reporting via :func:`integrations._validation_helpers.report_classify_failure`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from integrations._validation_helpers import report_classify_failure

logger = logging.getLogger(__name__)


def validate_classify[ConfigT: BaseModel](
    model_cls: type[ConfigT],
    record_id: str,
    data: dict[str, Any],
    *,
    integration: str,
    resolved_key: str,
    check_fn: Callable[[ConfigT], bool] | None = None,
    pre_check: Callable[[dict[str, Any]], bool] | None = None,
) -> tuple[ConfigT | None, str | None]:
    """Validate *data* against *model_cls* and return a classified config tuple.

    Consolidates the pattern shared across vendor ``classify()`` functions:
    optional pre-validation guard → ``model_validate`` → error reporting →
    optional post-validation check → return ``(cfg, resolved_key)``.

    Args:
        model_cls: Pydantic model class to validate *data* against.
        record_id: Integration record ID (used in Sentry error reporting).
        data: Raw credentials dict to pass to ``model_cls.model_validate``.
        integration: Vendor key for Sentry tagging (e.g. ``"twilio"``).
        resolved_key: The service key returned on success (e.g. ``"twilio"``).
        check_fn: Optional post-validation callable; receives the validated
            config and returns ``False`` to reject it (e.g.
            ``lambda cfg: bool(cfg.api_key)``).
        pre_check: Optional pre-validation guard; receives the raw *data* dict
            and returns ``False`` to skip validation entirely (e.g.
            ``lambda d: bool((d.get("bot_token") or "").strip())``).

    Returns:
        ``(cfg, resolved_key)`` on success, ``(None, None)`` on any failure.
    """
    if pre_check is not None and not pre_check(data):
        return None, None
    try:
        cfg: ConfigT = model_cls.model_validate(data)
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration=integration, record_id=record_id)
        return None, None
    if check_fn is not None and not check_fn(cfg):
        return None, None
    return cfg, resolved_key
