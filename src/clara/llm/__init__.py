"""Model providers, the catalogue, and the router that picks a model and its fallbacks."""

from .catalog import Catalog, ModelInfo
from .providers import Provider, ProviderError, Providers, make_ref, split_ref
from .router import Choice, NoModel, Router
from .types import Backend, Capabilities, LlmChunk, LlmError, ToolCall

__all__ = [
    "Backend",
    "Capabilities",
    "Catalog",
    "Choice",
    "LlmChunk",
    "LlmError",
    "ModelInfo",
    "NoModel",
    "Provider",
    "ProviderError",
    "Providers",
    "Router",
    "ToolCall",
    "make_ref",
    "split_ref",
]
