"""The LangGraph spine — orchestration only (05: "LangGraph is the wiring").

One turn flows: ``plan`` → (``ingest``) → (``retrieve``) → ``assemble`` → ``narrate``.
Routing is **tool selection**, not a separate classification step (M4-1): ``log_memory``
among the planner's calls makes it an ingest turn, retrieval calls make it a query turn,
both make it both, and an empty plan (M4-2) is a conversational turn that skips straight to
narration. The graph is therefore a *pure interpreter* of ``plan()`` output — it contains no
natural-language understanding of its own.

Ingest runs **before** retrieve on a "both" turn, so a memory logged this turn is already
committed when the aggregate scans for it ("logged my run — am I improving?").

**State durability boundary (decision M5-1, ADR-13.14).** ``GraphState`` declares only small,
serde-safe channels; the heavyweight turn-local objects (``RetrievalOutcome``,
``ContextBlock``, ``EvidenceTrace``, ``Receipt``) live on a per-invocation ``TurnCarrier``
passed through ``RunnableConfig["configurable"]`` and never touch the checkpoint. The
enforcement is not this docstring: ``agent/checkpointer.py``'s guarded serde raises if a
banned object ever reaches the persist path (M5-1 L2), so the boundary holds even if this
module changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from agent.tools import (
    ANALYZE_SERIES,
    LOG_MEMORY,
    RETRIEVAL_TOOLS,
    ToolCallError,
    analyze_series_metric,
    build_tool_specs,
    execute,
    is_analyze_series,
    is_log_memory,
    log_memory_text,
    prepare_call,
)
from engine.assembly import ContextBlock, RetrievalOutcome, assemble
from engine.citations import CitationReport, validate_citations
from engine.consolidation import ConsolidationService, SeriesOutcome
from engine.db import Database
from engine.ingestion import IngestionService, Receipt
from engine.insights import SeriesKey, UnknownSeries
from engine.model import EmbeddingError, ModelProvider, ToolCall
from engine.trace import EvidenceTrace
from engine.turns import TurnRecord, persist_turn

logger = logging.getLogger(__name__)

CARRIER_KEY = "turn_carrier"

# ── state: the checkpointed channels, deliberately small (M5-1 L1) ─────────────────────
class GraphState(TypedDict, total=False):
    """The **only** channels LangGraph checkpoints for a thread.

    Every field here is small and serde-safe. Heavy runtime objects belong on
    ``TurnCarrier`` — see the module docstring and decision M5-1. Adding a channel is a
    deliberate act: ``agent/tests/test_graph_routing.py`` asserts this set against an
    explicit allowlist (L3a), and the checkpointer guard (L2) refuses banned types outright.
    """

    messages: Annotated[list, add_messages]  # conversation continuity (ADR-13.14)
    user_id: str
    question: str
    now: str  # ISO-8601; the turn's clock, for grounding relative dates
    tz: str
    tool_calls: list[dict]  # [{"tool": ..., "arguments": {...}}] — plain, tiny, JSON-safe
    answer: str
    citations: list[str]


@dataclass
class TurnCarrier:
    """Per-invocation scratch space for the objects that must never be checkpointed.

    Created by the caller (the chat endpoint in M6), passed via
    ``config["configurable"]["turn_carrier"]``, and read back after ``invoke()`` — this is
    how the rich turn artifacts reach the API without entering graph state (M5-1).
    """

    outcomes: list[RetrievalOutcome] = field(default_factory=list)
    receipts: list[Receipt] = field(default_factory=list)
    #: What ``analyze_series`` derived this turn. Turn-local like everything else here, so it
    #: never enters checkpointed state (M5-1).
    consolidation: list[SeriesOutcome] = field(default_factory=list)
    context: ContextBlock | None = None
    trace: EvidenceTrace | None = None
    errors: list[str] = field(default_factory=list)
    #: T7b's verdict on the answer's citations. ``None`` until ``narrate`` has run.
    citations: CitationReport | None = None
    #: What stage (G) persisted, once it has run. ``None`` means the turn was not recorded —
    #: either (G) has not reached this turn yet, or it failed and said so in ``errors``.
    turn_record: TurnRecord | None = None


@dataclass(frozen=True)
class TurnResult:
    """What one turn produced: the prose plus everything the glass box needs."""

    thread_id: str
    answer: str
    citations: list[str]
    receipts: list[Receipt]
    consolidation: list[SeriesOutcome]
    context: ContextBlock | None
    trace: EvidenceTrace | None
    errors: list[str]
    #: T7b's citation verdict — what the UI flags invalid citations from.
    citation_report: CitationReport | None = None
    #: Stage (G)'s handles, or ``None`` if the turn was not persisted. The UI reads the glass
    #: box by ``turn_record.assistant_turn_id``; a ``None`` here is exactly the case where the
    #: answer stands but that turn has no glass box (glass-box-architecture.md §4.3).
    turn_record: TurnRecord | None = None


STATE_CHANNELS: frozenset[str] = frozenset(GraphState.__annotations__)


def _checked(name: str, node):
    """Wrap a node so its output is checked against the ``GraphState`` allowlist **before**
    LangGraph sees it (M5-1 L1, the developer-signal mechanism).

    Why this exists: LangGraph does **not** reject updates to undeclared channels — verified
    on 1.2.4, it *silently drops* them. The durability boundary survives that (a dropped
    value never reaches the checkpoint), but a developer who writes ``return {"context":
    ctx}`` would otherwise get no signal at all and would debug a silent disappearance. This
    turns that slip into a loud error naming the invariant.

    This is the *signal*, not the guarantee: the guarantee is the checkpointer's serde guard
    (M5-1 L2), which holds even if a channel is deliberately added to ``GraphState``.
    """

    def checked_node(state: GraphState, config: RunnableConfig) -> dict:
        update = node(state, config)
        unknown = set(update or {}) - STATE_CHANNELS
        if unknown:
            raise RuntimeError(
                f"node {name!r} returned undeclared channel(s) {sorted(unknown)}. "
                "LangGraph would silently drop them. Heavy, turn-local objects "
                "(ContextBlock/EvidenceTrace/RetrievalOutcome/Receipt) belong on the "
                "per-invocation TurnCarrier, not in GraphState (decision M5-1)."
            )
        return update

    return checked_node


def carrier_of(config: RunnableConfig) -> TurnCarrier:
    """The invocation's carrier. Missing means the caller wired the graph wrong — a loud
    failure is right: silently dropping the trace would defeat the glass box."""
    carrier = (config or {}).get("configurable", {}).get(CARRIER_KEY)
    if not isinstance(carrier, TurnCarrier):
        raise RuntimeError(
            f"no TurnCarrier in config['configurable'][{CARRIER_KEY!r}]; the caller must "
            "create one per invocation (decision M5-1)"
        )
    return carrier


# ── graph construction ─────────────────────────────────────────────────────────────────
def build_graph(
    *,
    db: Database,
    model: ModelProvider,
    ingestion: IngestionService,
    checkpointer: Any,
    default_tz: str,
    consolidation: ConsolidationService | None = None,
):
    """Compile the turn graph. Dependencies are injected (same composition-root style as
    ``api.main.create_app``), so tests drive a real database with a fake provider."""
    tool_specs = build_tool_specs()

    def plan_node(state: GraphState, config: RunnableConfig) -> dict:
        """The one NL-understanding step (05 query-planning boundary)."""
        calls = model.plan(
            state["question"],
            tool_specs,
            now=_now_of(state),
            tz=state.get("tz") or default_tz,
        )
        return {"tool_calls": [{"tool": c.tool, "arguments": dict(c.arguments)} for c in calls]}

    def ingest_node(state: GraphState, config: RunnableConfig) -> dict:
        """Run every ``log_memory`` call through the Phase 2 ingestion pipeline."""
        carrier = carrier_of(config)
        user_id = UUID(state["user_id"])
        for call in _calls_of(state):
            if not is_log_memory(call):
                continue
            try:
                text = log_memory_text(call)
            except ToolCallError:
                # Never lose the input (ADR-13.5 posture): if the planner mangled the text
                # slot, log the user's own words rather than dropping the turn's content.
                logger.info("log_memory call had no usable text; falling back to the raw turn")
                text = state["question"]
            carrier.receipts.append(
                ingestion.ingest_text(user_id, text, tz=state.get("tz") or default_tz)
            )
        return {}

    def consolidate_node(state: GraphState, config: RunnableConfig) -> dict:
        """Run every ``analyze_series`` call through the consolidation service (§4.9).

        Dispatched here rather than through ``prepare_call``/``execute`` because it **writes**:
        ``execute`` runs every retrieval tool inside one shared transaction, and a write there
        would need a read, a compare and possibly an insert+supersede inside it — putting
        several round trips, and the §4.8 budget, inside a transaction they must never enter.
        ``log_memory`` is dispatched for the same reason, and this is the same animal.

        Runs **before** retrieve (the ADR-14.3 ordering, applied to tier 2), so an insight
        derived this turn is already committed when the same turn's ``lookup_insights`` scans
        for it — otherwise the engine would derive a claim and then answer as though it had not.

        A failure costs this tool, not the turn (ADR-14.12).
        """
        carrier = carrier_of(config)
        user_id = UUID(state["user_id"])
        tz = state.get("tz") or default_tz
        for call in _calls_of(state):
            if not is_analyze_series(call):
                continue
            if consolidation is None:
                carrier.errors.append(f"{ANALYZE_SERIES}: consolidation is not configured")
                continue
            try:
                key = SeriesKey.for_metric(analyze_series_metric(call))
            except (ToolCallError, UnknownSeries) as exc:
                logger.info("dropping %s call: %s", ANALYZE_SERIES, exc)
                carrier.errors.append(f"{ANALYZE_SERIES}: {exc}")
                continue
            try:
                carrier.consolidation.append(
                    consolidation.consolidate_series(user_id, key, tz=tz)
                )
            except Exception as exc:  # noqa: BLE001 — one failed tool, not a failed turn
                logger.exception("analyze_series failed for %s", key)
                carrier.errors.append(f"{ANALYZE_SERIES}: {exc}")
        return {}

    def retrieve_node(state: GraphState, config: RunnableConfig) -> dict:
        """Prepare every retrieval call (model work off-transaction), then execute them all
        in one transaction — engine/db.py discipline."""
        carrier = carrier_of(config)
        user_id = UUID(state["user_id"])
        tz = state.get("tz") or default_tz

        prepared = []
        for call in _calls_of(state):
            if call.tool not in RETRIEVAL_TOOLS:
                continue
            try:
                prepared.append(prepare_call(call, model=model, tz=tz))
            except (ToolCallError, EmbeddingError) as exc:
                # One bad call must not sink the turn: record it so the answer can be
                # honest about what could not be retrieved, and run the rest. Embedding
                # failures land here too — recall needs a query vector, and a provider
                # that cannot embed (or a transient embed outage) should cost the user
                # that one tool, not the whole turn.
                logger.info("dropping tool call %s: %s", call.tool, exc)
                carrier.errors.append(f"{call.tool}: {exc}")

        if prepared:
            with db.transaction() as cur:
                carrier.outcomes.extend(execute(cur, user_id, p) for p in prepared)
        return {}

    def assemble_node(state: GraphState, config: RunnableConfig) -> dict:
        """Merge, rank, and emit the trace — always as a pair (ADR-12). Runs on every turn,
        so a turn that narrates always has a context and a trace, even when both are empty."""
        carrier = carrier_of(config)
        carrier.context, carrier.trace = assemble(state["question"], carrier.outcomes)
        return {}

    def narrate_node(state: GraphState, config: RunnableConfig) -> dict:
        """Prose only, with citation markers the engine can resolve (05 answer contract)."""
        carrier = carrier_of(config)
        context = carrier.context
        answer = model.narrate(state["question"], context)
        # T7b: validate against the *trace's* citable set, not the context's. They are the
        # same set (assembly copies it), but the trace is what gets persisted and what the
        # UI reads, so validating against it keeps runtime and rendered verdicts identical.
        citable = carrier.trace.citable_ids if carrier.trace else frozenset()
        carrier.citations = validate_citations(answer, citable)
        return {
            "answer": answer,
            # The checkpointed channel stays a plain list of id strings (small, serde-safe,
            # M5-1); the full report rides the carrier like every other rich turn artifact.
            "citations": [str(i) for i in carrier.citations.cited],
            "messages": [AIMessage(content=answer)],
        }

    def persist_node(state: GraphState, config: RunnableConfig) -> dict:
        """Stage (G): record the turn and its trace, atomically with each other.

        Runs after narration because that is the first moment both halves exist — the trace
        comes from ``assemble``, the answer from ``narrate``. It is deliberately *outside* the
        ingestion transaction: see glass-box-architecture.md §4.3, which amends ADR-13.14 and
        ingestion-transaction-boundaries.md §12.

        **Best-effort, like stage (F₀), and for the same reason.** The memories are already
        durably committed and the answer is already produced; a failure here costs the glass
        box for this turn and costs the user nothing. Swallowing the exception keeps a
        UI-persistence problem from failing a turn whose real work succeeded — but it is
        recorded in ``carrier.errors`` and logged, never silent (I-24).
        """
        carrier = carrier_of(config)
        if carrier.trace is None:
            return {}  # nothing was assembled; there is no glass box to record

        memory_ids = [ref.id for receipt in carrier.receipts for ref in receipt.created]
        try:
            with db.transaction() as cur:
                carrier.turn_record = persist_turn(
                    cur,
                    user_id=UUID(state["user_id"]),
                    thread_id=(config or {}).get("configurable", {}).get("thread_id"),
                    question=state["question"],
                    answer=state.get("answer") or "",
                    memory_ids=memory_ids,
                    trace=carrier.trace,
                )
        except Exception as exc:  # noqa: BLE001 — see the docstring; never fail a good turn
            logger.warning("stage (G) failed to persist turn for user %s: %s",
                           state["user_id"], exc)
            carrier.errors.append(f"turn not recorded: {exc}")
        return {}

    builder = StateGraph(GraphState)
    # Every node goes through _checked: the L1 developer signal is on by construction, so a
    # node cannot quietly grow a heavy channel (M5-1).
    for name, node in (
        ("plan", plan_node),
        ("ingest", ingest_node),
        ("consolidate", consolidate_node),
        ("retrieve", retrieve_node),
        ("assemble", assemble_node),
        ("narrate", narrate_node),
        ("persist", persist_node),
    ):
        builder.add_node(name, _checked(name, node))

    builder.add_edge(START, "plan")
    stages = {
        "ingest": "ingest",
        "consolidate": "consolidate",
        "retrieve": "retrieve",
        "assemble": "assemble",
    }
    builder.add_conditional_edges("plan", _route_after_plan, stages)
    builder.add_conditional_edges("ingest", _route_after_ingest, stages)
    builder.add_conditional_edges("consolidate", _route_after_consolidate, stages)
    builder.add_edge("retrieve", "assemble")
    builder.add_edge("assemble", "narrate")
    # Stage (G) is the last thing a turn does: both the trace and the answer exist by
    # here, and nothing downstream depends on it (glass-box-architecture.md §4.3).
    builder.add_edge("narrate", "persist")
    builder.add_edge("persist", END)
    return builder.compile(checkpointer=checkpointer)


# ── routing: derived entirely from which tools the planner selected (M4-1) ─────────────
#: The turn's stages, in the only order that is correct: write, then write, then read.
#: Ingest first so this turn's *memories* are queryable; consolidate next so this turn's
#: *insights* are too; retrieve last. Reversing either would let a turn answer as though work
#: it just did had not happened — a wrong answer that looks right, which is the worst failure
#: class for a glass box (ADR-14.3, extended to tier 2 in §4.9).
_STAGES: tuple[tuple[str, frozenset[str]], ...] = (
    ("ingest", frozenset({LOG_MEMORY})),
    ("consolidate", frozenset({ANALYZE_SERIES})),
    ("retrieve", RETRIEVAL_TOOLS),
)


def _next_stage(state: GraphState, *, after: str | None) -> str:
    """The first stage after ``after`` whose tools the planner actually selected.

    One table drives every edge, so a stage cannot be reachable from one predecessor and
    unreachable from another — the bug class a hand-written edge per pair invites. An empty
    plan (M4-2) falls through to ``assemble``: a conversational turn, answered without
    inventing a retrieval."""
    names = {c["tool"] for c in state.get("tool_calls") or []}
    reached = after is None
    for stage, tools in _STAGES:
        if not reached:
            reached = stage == after
            continue
        if names & tools:
            return stage
    return "assemble"


def _route_after_plan(state: GraphState) -> str:
    """The turn's shape, read entirely off which tools the planner selected (M4-1)."""
    return _next_stage(state, after=None)


def _route_after_ingest(state: GraphState) -> str:
    return _next_stage(state, after="ingest")


def _route_after_consolidate(state: GraphState) -> str:
    return _next_stage(state, after="consolidate")


# ── helpers ────────────────────────────────────────────────────────────────────────────
def _calls_of(state: GraphState) -> list[ToolCall]:
    return [
        ToolCall(tool=c["tool"], arguments=c.get("arguments") or {})
        for c in state.get("tool_calls") or []
    ]


def _now_of(state: GraphState) -> datetime:
    raw = state.get("now")
    return datetime.fromisoformat(raw) if raw else datetime.now(timezone.utc)


def run_turn(
    graph,
    *,
    user_id: UUID,
    question: str,
    thread_id: str,
    tz: str,
    now: datetime | None = None,
) -> TurnResult:
    """Run one turn end to end: create the carrier, invoke the graph, collect everything.

    This is the seam the chat endpoint (M6) uses — it owns the carrier lifecycle so callers
    never have to know that heavy objects travel out-of-band (M5-1).
    """
    carrier = TurnCarrier()
    config = {"configurable": {"thread_id": thread_id, CARRIER_KEY: carrier}}
    state = graph.invoke(
        {
            "messages": [HumanMessage(content=question)],
            "user_id": str(user_id),
            "question": question,
            "now": (now or datetime.now(timezone.utc)).isoformat(),
            "tz": tz,
        },
        config,
    )
    return TurnResult(
        thread_id=thread_id,
        answer=state.get("answer", ""),
        citations=list(state.get("citations") or []),
        receipts=list(carrier.receipts),
        consolidation=list(carrier.consolidation),
        context=carrier.context,
        trace=carrier.trace,
        errors=list(carrier.errors),
        citation_report=carrier.citations,
        turn_record=carrier.turn_record,
    )
