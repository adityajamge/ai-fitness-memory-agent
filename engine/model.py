"""The model-independence boundary (ADR-1, 05-agent-architecture.md).

The Memory Engine depends on this ``ModelProvider`` Protocol and nothing else about the
LLM: it never imports a provider SDK. The concrete Bedrock implementation lives outside the
engine (``agent/providers/bedrock.py``); tests inject a fake. Swapping providers is a config
change with zero engine edits.

Phase 2 needs only extraction + embeddings. Phase 3 (M4) adds the two agent-facing
surfaces — ``plan`` (question → typed tool calls) and ``narrate`` (assembled context →
cited prose); vision (Phase 5) extends this Protocol later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from engine.assembly import ContextBlock


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


class PlanningError(Exception):
    """Raised when retrieval planning fails (call error, malformed output). The graph
    (M5) surfaces this as a failed turn — distinct from an empty plan, which is a positive
    'no memory operation needed' assertion (see ``plan``)."""


class NarrationError(Exception):
    """Raised when narration fails (call error, empty/malformed output)."""


@dataclass(frozen=True)
class ToolSpec:
    """A provider-agnostic description of one tool the planner may call. The engine/agent
    owns the tool vocabulary; the provider only needs each tool's name, a description, and
    a JSON Schema for its arguments (05 model-independence — the provider never hardcodes
    the memory tools). M5 builds these from the retrieval specs."""

    name: str
    description: str
    input_schema: dict  # JSON Schema for the tool's arguments


@dataclass(frozen=True)
class ToolCall:
    """One tool call the planner emitted: a tool name plus a raw argument dict. Arguments
    are **unvalidated** here — slot validation against the typed specs (RetrievalSpecError)
    happens in the agent's tools layer before execution (M5). Keeping ``plan`` output raw
    keeps the model-independence boundary clean: the provider knows nothing about slot
    types."""

    tool: str
    arguments: dict = field(default_factory=dict)


@runtime_checkable
class ModelProvider(Protocol):
    """The only LLM surface the engine knows about."""

    def extract_events(self, text: str, *, now: datetime, tz: str) -> list[ExtractedEvent]:
        """Turn a user message into typed events. ``now``/``tz`` anchor relative dates
        ("yesterday", "this morning").

        **The empty-result contract (load-bearing — this is what keeps never-lose-input
        honest).** A provider has exactly three outcomes, and an empty list is a *positive
        assertion*, not a shrug:

        =========================  ===================================================
        Outcome                    Meaning
        =========================  ===================================================
        ``[ExtractedEvent, ...]``  Loggable content found and typed.
        ``[]``                     **The provider affirms the turn holds no loggable
                                   health content** — "thanks!", "what did I eat?".
                                   The engine records nothing and replies "nothing to
                                   log". No note is written.
        ``ExtractionError``        Everything else: the call failed, the output was
                                   malformed, OR the turn plainly carried content the
                                   provider could not type. The engine falls back to a
                                   note, so the input survives.
        =========================  ===================================================

        A provider that cannot tell "nothing to log" from "I failed to parse this" **must
        raise** rather than return ``[]`` — returning ``[]`` on unparsed content is the one
        way to silently drop a user's input. See
        ``docs/engineering/ingestion-transaction-boundaries.md`` §5.
        """
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one normalized 512-dim vector per input text, in order. Titan V2 with
        ``normalize=true`` (ADR-13.2). Raises EmbeddingError on failure. All-or-nothing:
        either every text embeds or the call raises (transaction-boundaries doc §6)."""
        ...

    def plan(
        self, question: str, tools: list[ToolSpec], *, now: datetime, tz: str
    ) -> list[ToolCall]:
        """Turn a user turn into typed tool calls — the **only** natural-language
        understanding step in the system (05 query-planning boundary). The planner
        classifies the turn and selects tools by filling their typed slots; it never
        executes anything and never issues SQL. ``now``/``tz`` ground relative date ranges
        ("last 30 days").

        Routing is tool selection: with ``log_memory`` among ``tools``, selecting it is an
        *ingest* turn, selecting retrieval tools is a *query* turn, selecting both is a
        *both* turn. "Mixed retrieval" is simply several retrieval calls in the returned
        list (assembly merges them into one ranked set with one trace).

        **The empty-plan contract (mirrors the extraction empty-result contract above).**

        =========================  ===================================================
        Outcome                    Meaning
        =========================  ===================================================
        ``[ToolCall, ...]``        Tools to run for this turn (one or more).
        ``[]``                     **The planner affirms no memory operation is needed** —
                                   small talk, a greeting, a meta-question the graph can
                                   answer conversationally. A positive assertion, not a
                                   failure.
        ``PlanningError``          The call failed or returned malformed output.
        =========================  ===================================================

        Arguments in each ToolCall are raw and unvalidated by design; the agent's tools
        layer validates them against the typed slots before any engine call (M5).
        """
        ...

    def narrate(self, question: str, context: ContextBlock) -> str:
        """Turn assembled evidence into a natural-language answer. The narrator produces
        **prose only** (05 answer contract): every factual claim carries a ``[memory-id]``
        citation marker drawn from the IDs present in ``context`` (``context.citable_ids``).
        It must not invent numbers, dates, or IDs; an empty context yields an honest
        "no logged data" reply.

        Mechanical citation validation is Phase 6 (T7) and numeric/directional fidelity is
        the citation eval's job (ADR-13.13); at this layer the markers are produced by
        instruction. Raises NarrationError on failure."""
        ...
