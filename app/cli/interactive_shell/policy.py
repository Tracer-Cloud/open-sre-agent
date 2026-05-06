"""Execution Policy Gate for interactive-shell actions."""

from __future__ import annotations

import logging
from typing import Literal

from app.cli.interactive_shell.agent_actions import PlannedAction
from app.cli.interactive_shell.session import ReplSession

logger = logging.getLogger(__name__)


class ExecutionPolicyGate:
    """Central policy engine for interactive-shell actions."""

    def evaluate(
        self, action: PlannedAction, session: ReplSession
    ) -> tuple[Literal["allow", "ask", "deny"], str]:
        """Evaluate a planned action against the current session context.

        Returns:
            A tuple of (decision, reason) where decision is "allow", "ask", or "deny".
        """
        # 1. Deny destructive actions (e.g., shell commands containing rm, rmdir, delete, kill, format, etc.)
        if action.kind == "shell":
            cmd = action.content.strip().lower()
            destructive_keywords = [
                "rm ",
                "rmdir",
                "delete ",
                "kill ",
                "format ",
                "> /dev/",
                "mkfs",
            ]
            for kw in destructive_keywords:
                if kw in cmd or cmd.startswith(kw.strip()):
                    reason = f"Command contains potentially destructive keyword '{kw.strip()}'"
                    logger.warning(
                        "Policy Decision: DENY (action_kind=shell, command=%s): %s",
                        action.content,
                        reason,
                    )
                    return "deny", reason

            # 2. If trust_mode is True, allow shell commands. If False, ask for user confirmation.
            if session.trust_mode:
                reason = "Shell execution allowed in trust mode"
                logger.info(
                    "Policy Decision: ALLOW (action_kind=shell, command=%s): %s",
                    action.content,
                    reason,
                )
                return "allow", reason
            else:
                reason = "Requires user confirmation outside of trust mode"
                logger.info(
                    "Policy Decision: ASK (action_kind=shell, command=%s): %s",
                    action.content,
                    reason,
                )
                return "ask", reason

        # 3. Safe actions (slash, llm_provider, sample_alert, synthetic_test) are allowed
        reason = f"Safe built-in action '{action.kind}' is always allowed"
        logger.info(
            "Policy Decision: ALLOW (action_kind=%s, content=%s): %s",
            action.kind,
            action.content,
            reason,
        )
        return "allow", reason
