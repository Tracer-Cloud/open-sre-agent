"""Primitives shared by both transports (sdk and litellm).

Cross-transport building blocks — OpenAI Chat Completions helpers, retry/error
classification, tool-schema normalization, structured-output wrapping, and usage
accounting — that the transport client families are built on.
"""

from __future__ import annotations
