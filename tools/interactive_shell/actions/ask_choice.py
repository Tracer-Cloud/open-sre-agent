"""Block on a surface-owned human choice and return it to the agent."""

from __future__ import annotations

import re
from typing import Any

from core.agent_harness.ports import (
    UserChoiceOption,
    UserChoiceRequest,
)
from core.agent_harness.tools import ActionToolScope, execute_with_action_context
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool, SideEffectLevel

_MIN_OPTIONS = 2
_MAX_OPTIONS = 3
_MAX_ID_CHARS = 64
_MAX_QUESTION_CHARS = 240
_MAX_LABEL_CHARS = 80
_MAX_DESCRIPTION_CHARS = 240
_QUESTION_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_FALLBACK_INSTRUCTION = (
    "No human-interaction UI is available on this surface. Present the "
    "options as a short numbered list instead and ask the user to reply with "
    "the number or the option text."
)

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 1,
            "description": "Exactly one short question to show the user.",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "maxLength": _MAX_ID_CHARS,
                        "description": "Stable snake_case identifier for the answer.",
                    },
                    "header": {
                        "type": "string",
                        "maxLength": 12,
                        "description": "Short UI header (12 or fewer characters).",
                    },
                    "question": {
                        "type": "string",
                        "maxLength": _MAX_QUESTION_CHARS,
                        "description": "Single-sentence prompt shown to the user.",
                    },
                    "options": {
                        "type": "array",
                        "minItems": _MIN_OPTIONS,
                        "maxItems": _MAX_OPTIONS,
                        "description": (
                            "Two or three mutually exclusive choices. Put the "
                            "recommended option first and suffix its label with "
                            "'(Recommended)'. The UI adds an Other choice."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {
                                    "type": "string",
                                    "maxLength": _MAX_LABEL_CHARS,
                                    "description": "User-facing label (1-5 words).",
                                },
                                "description": {
                                    "type": "string",
                                    "maxLength": _MAX_DESCRIPTION_CHARS,
                                    "description": (
                                        "One short sentence explaining the impact or trade-off."
                                    ),
                                },
                            },
                            "required": ["label", "description"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "header", "question", "options"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}


def _parse_choice_request(args: dict[str, Any]) -> tuple[UserChoiceRequest | None, str | None]:
    raw_questions = args.get("questions")
    if not isinstance(raw_questions, list) or len(raw_questions) != 1:
        return None, "exactly one question is required"
    raw_question = raw_questions[0]
    if not isinstance(raw_question, dict):
        return None, "question must be an object"

    question_id = str(raw_question.get("id", "")).strip()
    header = str(raw_question.get("header", "")).strip()
    question = str(raw_question.get("question", "")).strip()
    if not _QUESTION_ID_RE.fullmatch(question_id):
        return None, "question id must be snake_case"
    if len(question_id) > _MAX_ID_CHARS:
        return None, f"question id must be {_MAX_ID_CHARS} characters or fewer"
    if not header:
        return None, "question header is required"
    if len(header) > 12:
        return None, "question header must be 12 characters or fewer"
    if not question:
        return None, "question text is required"
    if len(question) > _MAX_QUESTION_CHARS:
        return None, f"question text must be {_MAX_QUESTION_CHARS} characters or fewer"

    raw_options = raw_question.get("options")
    if not isinstance(raw_options, list):
        return None, "question options must be a list"
    if not _MIN_OPTIONS <= len(raw_options) <= _MAX_OPTIONS:
        return None, f"question requires {_MIN_OPTIONS} to {_MAX_OPTIONS} options"
    options: list[UserChoiceOption] = []
    seen: set[str] = set()
    for item in raw_options:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        description = str(item.get("description", "")).strip()
        normalized_label = label.casefold()
        if (
            label
            and description
            and len(label) <= _MAX_LABEL_CHARS
            and len(description) <= _MAX_DESCRIPTION_CHARS
            and normalized_label not in seen
        ):
            seen.add(normalized_label)
            options.append(UserChoiceOption(label=label, description=description))
    if len(options) < _MIN_OPTIONS:
        return None, f"at least {_MIN_OPTIONS} distinct complete options are required"
    if len(options) > _MAX_OPTIONS:
        return None, f"at most {_MAX_OPTIONS} options are supported"
    return (
        UserChoiceRequest(
            id=question_id,
            header=header,
            question=question,
            options=tuple(options),
        ),
        None,
    )


def execute_ask_user_choice_tool(args: dict[str, Any], ctx: ActionToolScope) -> dict[str, Any]:
    request, error = _parse_choice_request(args)
    if request is None:
        return {"ok": False, "error": error or "invalid question"}
    if ctx.human_interaction is None:
        return {"ok": True, "menu": "unavailable", "instruction": _FALLBACK_INSTRUCTION}

    answer = ctx.human_interaction.choose(request)
    if answer is None:
        return {"ok": False, "cancelled": True}
    return {
        "ok": True,
        "answers": {request.id: {"answers": [answer]}},
        "summary": f"{request.header}: {answer}",
    }


def run_ask_user_choice(
    *,
    questions: list[dict[str, Any]] | None = None,
    context: Any,
) -> dict[str, Any]:
    return execute_with_action_context(
        {"questions": questions or []},
        context,
        execute_ask_user_choice_tool,
    )


ask_user_choice_tool = RegisteredTool(
    name="ask_user_choice",
    description=(
        "Ask the user one short multiple-choice question and wait for the "
        "surface-rendered response. Use this instead of writing a numbered list "
        "whenever a required decision blocks progress. Provide two or three "
        "options with concise trade-off descriptions; the UI also accepts a "
        "custom answer. Continue from the structured answer returned by this "
        "tool. If the UI is unavailable, fall back to a numbered list."
    ),
    use_cases=[
        (
            "A workflow is blocked on one required decision between a small "
            "fixed set of actions (e.g. stash vs commit vs worktree)"
        ),
        "A skill instructs presenting a structured choice / dropdown to the user",
    ],
    anti_examples=[
        "Open-ended questions with no fixed option set (ask in plain text)",
        "Yes/no confirmations already covered by the execution confirmation flow",
        "Presenting information that requires no decision",
    ],
    input_schema=_INPUT_SCHEMA,
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_ask_user_choice,
    tags=("safe", "human-handoff", "no-credentials"),
    side_effect_level=SideEffectLevel.READ_ONLY,
)


__all__ = [
    "ask_user_choice_tool",
    "execute_ask_user_choice_tool",
    "run_ask_user_choice",
]
