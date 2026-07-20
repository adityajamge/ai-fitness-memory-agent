"""The model-independence boundary (ADR-1, 05-agent-architecture.md).

The Memory Engine depends on this ``ModelProvider`` Protocol and nothing else about the
LLM: it never imports a provider SDK. The concrete Bedrock implementation lives outside the
engine (``agent/providers/bedrock.py``); tests inject a fake. Swapping providers is a config
change with zero engine edits.

Phase 2 needs only extraction + embeddings. Narration (Phase 3) and vision (Phase 5) extend
this Protocol later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ExtractedEvent:
    """One typed event the model inferred from a user turn, pre-validation.

    ``payload`` is a raw dict; the engine validates it through engine/types.py (stage B of
    the ingestion pipeline). ``event_time``/``tz`` are the model's inference (04 bi-temporal
    design); when the model cannot infer them the provider fills the configured default tz
    and lowers ``confidence`` accordingly.
    """

    type: str
    event_time: datetime
    tz: str
    confidence: float
    summary: str
    payload: dict = field(default_factory=dict)


class ExtractionError(Exception):
    """Raised when extraction fails (throttle, malformed output, or empty result on input
    that clearly contained loggable content). Triggers the note fallback — never lost."""


class EmbeddingError(Exception):
    """Raised when the embedding call fails. Triggers NULL-embedding insert + later
    backfill — never fails the turn (transaction-boundaries doc §6)."""


@runtime_checkable
class ModelProvider(Protocol):
    """The only LLM surface the engine knows about."""

    def extract_events(self, text: str, *, now: datetime, tz: str) -> list[ExtractedEvent]:
        """Turn a user message into typed events. ``now``/``tz`` anchor relative dates
        ("yesterday", "this morning"). Raises ExtractionError on failure."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one normalized 512-dim vector per input text, in order. Titan V2 with
        ``normalize=true`` (ADR-13.2). Raises EmbeddingError on failure. All-or-nothing:
        either every text embeds or the call raises (transaction-boundaries doc §6)."""
        ...
