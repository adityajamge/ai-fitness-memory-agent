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


class VisionError(Exception):
    """Raised when photo extraction fails (call error, malformed output, or unreadable image).
    Same posture as ExtractionError: triggers the note fallback so the turn is never lost —
    see IngestionService.ingest_photo. There is no S3-backed durability for the photo itself
    (M7's ephemeral-storage variant, TODOS.md), so a VisionError does mean the original image
    cannot be recovered; only the caption (or an honest placeholder) survives as a note."""


class NutritionError(Exception):
    """Raised when the nutrition estimate call fails (call error, malformed output, or no
    tool call). Handled exactly like an embedding failure: the meal commits **without**
    ``payload.nutrition`` and stays eligible for ``python -m cli.backfill_nutrition``. A
    nutrition failure must never fail a turn — the user reported a meal, and losing that to a
    *derived* value's problem would invert never-lose-input."""


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

    def extract_from_image(
        self, image_bytes: bytes, mime_type: str, *, now: datetime, tz: str, caption: str = ""
    ) -> list[ExtractedEvent]:
        """Turn a food photo (plus an optional caption) into typed events — M7's vision
        counterpart to ``extract_events``, same three-outcome contract:

        =========================  ===================================================
        Outcome                    Meaning
        =========================  ===================================================
        ``[ExtractedEvent, ...]``  Loggable food found and typed.
        ``[]``                     **The provider affirms the photo holds nothing
                                   loggable** — not food, or nothing identifiable.
        ``VisionError``            The call failed, the output was malformed, or the
                                   image could not be read. The engine falls back to a
                                   note (the caption, or an honest placeholder) — see
                                   ``IngestionService.ingest_photo``.
        =========================  ===================================================

        **Load-bearing quantity rule, honored by every implementation:** a ``MealItem``'s
        ``qty_g``/``qty`` must be set ONLY when ``caption`` explicitly states the amount —
        never from a visual guess. A quantity the provider only *sees* (never stated in
        words) belongs in ``qty_text`` as a described estimate, with ``qty_g``/``qty`` left
        ``None``. This is what keeps ``engine.nutrition``'s existing ``qty_basis`` honesty
        machinery correct without any change to that module: ``nutrition_items_prompt``
        (``agent/providers/_prompts.py``) renders a present ``qty_g`` as "stated", so a
        vision-inferred gram figure placed there would silently mislabel an AI guess as
        something the user said.
        """
        ...

    def estimate_nutrition(self, items: list[dict], *, context: str = "") -> list[dict]:
        """Estimate per-item nutrition for one meal, using the model's own world knowledge.

        **A second call, deliberately separate from ``extract_events``.** Extraction is governed
        by "NEVER invent facts" — load-bearing for time and quantity fidelity. Estimation needs
        the opposite instruction ("infer from what you know about food"). Both in one prompt is
        why macro coverage was previously a coin flip: the same input produced macros, no
        macros, and different macros across consecutive turns. One coherent instruction per call
        fixes that, and makes the estimate independently retryable, cacheable and versionable.

        ``items`` are the extracted meal items (``name``/``qty``/``qty_g``/``qty_text``);
        ``context`` is the event's summary, for disambiguation only.

        Returns **raw, untrusted component dicts** — one per input item, in any order. The
        provider does no validation: ``engine.nutrition.build_nutrition`` bounds-checks them,
        computes every total, and classifies confidence. The contract asks the model for three
        things the engine cannot supply for itself:

        * ``resolved: false`` when it does not recognize the food. **A decline is a valid,
          expected answer** — the same posture as extraction's ``no_loggable_content``, and what
          keeps the system from ever inventing nutrition it has no basis for.
        * ``qty_basis`` — ``stated`` when the user gave the quantity, ``ai_estimated`` when the
          model assumed a portion.
        * ``assumptions`` and ``range`` — what it took for granted, and how wide it thinks it is.

        Raises ``NutritionError`` on call failure or malformed output. A provider that cannot
        estimate at all (no such capability) should raise it too: the meal still persists, and
        the backfill fills the gap later.
        """
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
