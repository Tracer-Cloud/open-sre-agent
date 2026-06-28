"""Bedrock model ID helpers shared by LLM client modules."""

from __future__ import annotations


def is_anthropic_bedrock_model(model_id: str) -> bool:
    """Return True when *model_id* should be routed through the AnthropicBedrock SDK.

    Anthropic model IDs on Bedrock look like:
      - ``anthropic.claude-*``
      - ``us.anthropic.claude-*``  (cross-region inference profiles)
      - ``arn:aws:bedrock:*:foundation-model/anthropic.claude-*``
      - ``arn:aws:bedrock:*:application-inference-profile/*`` (unknown vendor → Converse)

    For ARN-based application inference profiles we cannot tell the backing
    foundation model from the ID alone (it may point at Mistral, Llama, etc.).
    Those ARNs route to the model-agnostic Converse API rather than forcing
    the Anthropic SDK (which would fail for non-Claude pools).
    """
    model_lower = model_id.lower()
    if "anthropic.claude" in model_lower:
        return True
    # Application inference profile ARNs encode no vendor — use converse (all models).
    if model_lower.startswith("arn:") and "application-inference-profile" in model_lower:
        return False
    # Anything else (mistral.*, openai.*, meta.*, etc.) → boto3 converse
    return False
