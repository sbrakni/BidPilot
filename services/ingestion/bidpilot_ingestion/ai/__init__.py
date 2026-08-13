"""The LLM gateway (SPEC §17.4).

*"No service calls a model except through this gateway - this is how cost, quality and
swap-ability stay controlled."*

Everything a model call needs is assembled here: which tier resolves to which model, which
prompt version was used, whether the answer validated against its schema, what it cost, and
which org to bill. A call made directly against a provider SDK would have none of that, which is
why the rule is absolute rather than a guideline.
"""

from .cache import ResponseCache
from .gateway import (
    CircuitOpenError,
    Gateway,
    GatewayError,
    ModelResponse,
    SchemaValidationError,
    Transport,
)
from .registry import ModelTier, PromptSpec, load_prompt, resolve_model
from .transports import AnthropicTransport, RecordedTransport, ScriptedTransport

__all__ = [
    "AnthropicTransport",
    "CircuitOpenError",
    "Gateway",
    "GatewayError",
    "ModelResponse",
    "ModelTier",
    "PromptSpec",
    "RecordedTransport",
    "ResponseCache",
    "SchemaValidationError",
    "ScriptedTransport",
    "Transport",
    "load_prompt",
    "resolve_model",
]
