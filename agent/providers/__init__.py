"""Concrete model providers. The engine depends only on engine.model.ModelProvider; these
implementations import provider SDKs and are wired in by the app's composition root.

**Provider selection is per role** (ADR-13 amendment): the LLM role
(``extract_events``/``plan``/``narrate``) and the embedding role (``embed``) are chosen
independently, because they vary independently — the LLM is freely swappable, while the
embedding provider is pinned by ``VECTOR(512)`` and is effectively a one-way door once
memories exist. When both roles resolve to the same provider the concrete instance is
returned directly; only a genuinely mixed configuration builds a ``CompositeProvider``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from engine.config import Settings
from engine.model import ExtractedEvent, HistoryTurn, ModelProvider, ToolCall, ToolSpec

if TYPE_CHECKING:  # same deferral as engine/model.py — avoids importing assembly eagerly
    from engine.assembly import ContextBlock

BEDROCK = "bedrock"
CLAUDE_API = "claude_api"


class CompositeProvider:
    """Routes each ``ModelProvider`` method to the provider configured for its role.

    Satisfies ``ModelProvider`` structurally (it is a ``Protocol``), so every consumer —
    ``IngestionService``, ``agent/graph.py``, ``engine/retrieval.embed_query``,
    ``agent/tools.prepare_call`` — keeps its existing signature and cannot tell the
    difference between this and a single-vendor provider.

    Deliberately does nothing but delegate: no fallback, no retry, no error translation.
    Exceptions propagate from whichever provider raised them, which is what keeps the
    load-bearing contracts intact — ``ExtractionError`` still drives the note fallback
    (never-lose-input) and ``EmbeddingError`` still yields a NULL embedding for later
    backfill, exactly as with a single provider.
    """

    def __init__(self, *, llm: ModelProvider, embedder: ModelProvider) -> None:
        self._llm = llm
        self._embedder = embedder

    @property
    def llm(self) -> ModelProvider:
        return self._llm

    @property
    def embedder(self) -> ModelProvider:
        return self._embedder

    # ── LLM role ──────────────────────────────────────────────────────────────────────
    def extract_events(self, text: str, *, now: datetime, tz: str) -> list[ExtractedEvent]:
        return self._llm.extract_events(text, now=now, tz=tz)

    def extract_from_image(
        self, image_bytes: bytes, mime_type: str, *, now: datetime, tz: str, caption: str = ""
    ) -> list[ExtractedEvent]:
        return self._llm.extract_from_image(image_bytes, mime_type, now=now, tz=tz, caption=caption)

    def plan(
        self,
        question: str,
        tools: list[ToolSpec],
        *,
        now: datetime,
        tz: str,
        history: Sequence[HistoryTurn] = (),
    ) -> list[ToolCall]:
        return self._llm.plan(question, tools, now=now, tz=tz, history=history)

    def narrate(
        self, question: str, context: ContextBlock, *, history: Sequence[HistoryTurn] = ()
    ) -> str:
        return self._llm.narrate(question, context, history=history)

    def estimate_nutrition(self, items: list[dict], *, context: str = "") -> list[dict]:
        return self._llm.estimate_nutrition(items, context=context)

    @property
    def nutrition_model_id(self) -> str:
        """Delegated so a stored estimate names the model that actually produced it, not the
        composite wrapper — the whole point of recording it (``engine/nutrition.py``)."""
        return str(getattr(self._llm, "nutrition_model_id", "unknown"))

    @property
    def nutrition_prompt_version(self) -> str:
        return str(getattr(self._llm, "nutrition_prompt_version", "unknown"))

    # ── embedding role ────────────────────────────────────────────────────────────────
    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._embedder.embed(texts)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"CompositeProvider(llm={type(self._llm).__name__}, "
            f"embedder={type(self._embedder).__name__})"
        )


def _build_one(name: str, settings: Settings, *, env_var: str) -> ModelProvider:
    """Construct a single concrete provider by name.

    ``env_var`` is the environment variable that supplied ``name``, so a misconfiguration
    error points at the variable the operator actually has to fix rather than at an internal
    role label.

    Imports are lazy so a deployment only needs the SDK of the provider it actually uses —
    a Bedrock-only deployment never imports ``anthropic``, and vice versa.
    """
    if name == BEDROCK:
        from agent.providers.bedrock import BedrockProvider

        return BedrockProvider(
            region=settings.aws_region,
            extraction_model_id=settings.extraction_model_id,
            embedding_model_id=settings.embedding_model_id,
            embed_dims=settings.embed_dims,
            default_tz=settings.default_tz,
        )

    if name == CLAUDE_API:
        from agent.providers.claude_api import ClaudeAPIProvider

        return ClaudeAPIProvider(
            model_id=settings.claude_api_model_id,
            effort=settings.claude_api_effort,
            default_tz=settings.default_tz,
        )

    raise ValueError(f"unknown {env_var} {name!r}; expected {BEDROCK!r} or {CLAUDE_API!r}")


def build_default_provider(settings: Settings) -> ModelProvider:
    """Construct the configured provider(s). Swapping providers is a change *here only* —
    zero engine edits (model-independence, ADR-1/ADR-13).

    Roles resolve independently (``LLM_PROVIDER`` / ``EMBEDDING_PROVIDER``, each falling back
    to ``MODEL_PROVIDER`` then the default). When both name the same provider the concrete
    instance is returned **unwrapped**, so every pre-existing configuration keeps not only its
    behavior but its exact object type — that is the backward-compatibility guarantee, and it
    is why the existing provider tests need no changes.

    ``claude_api`` is no longer only a development adapter: paired with a Bedrock embedding
    role it is a supported production configuration for the LLM role. It still cannot embed,
    so ``EMBEDDING_PROVIDER=claude_api`` leaves memories with NULL embeddings for later
    backfill (T15) — legal, but semantic recall stays dead until a real embedder runs.
    """
    llm_name = settings.resolved_llm_provider
    embedding_name = settings.resolved_embedding_provider

    if llm_name == embedding_name:
        # Both roles agree, so report whichever variable actually supplied the value.
        source = "LLM_PROVIDER" if settings.llm_provider else "MODEL_PROVIDER"
        return _build_one(llm_name, settings, env_var=source)

    return CompositeProvider(
        llm=_build_one(llm_name, settings, env_var="LLM_PROVIDER"),
        embedder=_build_one(embedding_name, settings, env_var="EMBEDDING_PROVIDER"),
    )
