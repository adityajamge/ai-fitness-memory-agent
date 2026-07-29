"""Concrete model providers. The engine depends only on engine.model.ModelProvider; these
implementations import provider SDKs and are wired in by the app's composition root."""

from __future__ import annotations

from engine.config import Settings
from engine.model import ModelProvider

BEDROCK = "bedrock"
CLAUDE_API = "claude_api"


def build_default_provider(settings: Settings) -> ModelProvider:
    """Construct the configured provider. Swapping providers is a change *here only* — zero
    engine edits (model-independence, ADR-1/ADR-13).

    ``bedrock`` is the architecture's default (ADR-13). ``claude_api`` is a **development
    adapter** for running without Bedrock access; it cannot embed, so memories land with
    NULL embeddings for later backfill (see agent/providers/claude_api.py).

    Imports are lazy so a deployment only needs the SDK of the provider it actually uses.
    """
    provider = (settings.model_provider or BEDROCK).strip().lower()

    if provider == BEDROCK:
        from agent.providers.bedrock import BedrockProvider

        return BedrockProvider(
            region=settings.aws_region,
            extraction_model_id=settings.extraction_model_id,
            embedding_model_id=settings.embedding_model_id,
            embed_dims=settings.embed_dims,
            default_tz=settings.default_tz,
        )

    if provider == CLAUDE_API:
        from agent.providers.claude_api import ClaudeAPIProvider

        return ClaudeAPIProvider(
            model_id=settings.claude_api_model_id,
            effort=settings.claude_api_effort,
            default_tz=settings.default_tz,
        )

    raise ValueError(
        f"unknown MODEL_PROVIDER {settings.model_provider!r}; "
        f"expected {BEDROCK!r} or {CLAUDE_API!r}"
    )
