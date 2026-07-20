"""Concrete model providers. The engine depends only on engine.model.ModelProvider; these
implementations import provider SDKs and are wired in by the app's composition root."""

from __future__ import annotations

from engine.config import Settings
from engine.model import ModelProvider


def build_default_provider(settings: Settings) -> ModelProvider:
    """Construct the default provider (Amazon Bedrock). Swapping providers is a change
    here only — zero engine edits (model-independence, ADR-1/ADR-13)."""
    from agent.providers.bedrock import BedrockProvider

    return BedrockProvider(
        region=settings.aws_region,
        extraction_model_id=settings.extraction_model_id,
        embedding_model_id=settings.embedding_model_id,
        embed_dims=settings.embed_dims,
        default_tz=settings.default_tz,
    )
