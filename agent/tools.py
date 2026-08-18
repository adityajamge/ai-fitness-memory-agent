"""The agent's tool layer — the typed boundary between planner output and the engine.

This module is where the planner's raw, model-authored ``ToolCall`` arguments become
**validated typed specs** and then executed engine calls. It is the enforcement point for
05's query-planning boundary: above it the LLM chose a tool and filled slots; below it
nothing is interpreted, only executed.

Three responsibilities, in order:

1. **Vocabulary** (``build_tool_specs``) — the provider-agnostic ``ToolSpec`` list handed to
   ``ModelProvider.plan``. JSON Schema ``enum``s are generated from the engine's own closed
   vocabularies (``METRICS``, ``MEMORY_TYPE_REGISTRY``), so a planner literally cannot name a
   metric or memory type the engine doesn't have — the same schema-level posture the
   extraction tool already uses (``agent/providers/bedrock.py``).
2. **Validation** (``prepare_call``) — raw arguments → a typed spec (``AggregateSpec`` etc.),
   raising ``ToolCallError`` before any SQL is composed. A planner mistake dies here, above
   the database.
3. **Execution** (``execute``) — a prepared call → ``RetrievalOutcome`` (result + the
   ``RetrievalStep`` for the trace), ready for ``engine.assembly.assemble``.

**Transaction discipline (engine/db.py, transaction-boundaries doc):** all model work happens
in ``prepare_call`` — which is why recall's query embedding is computed there, *outside* any
transaction — and ``execute`` is pure database work. The graph (M5) calls prepare for every
call first, then opens one transaction to execute them.

``log_memory`` is deliberately **not** executed here: it is not a query builder and needs the
ingestion service rather than a cursor, so the graph dispatches it to ``IngestionService``
(M5-1 / M4-1: routing is tool selection, and the graph is the interpreter).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg

from engine.assembly import RetrievalOutcome
from engine.insights import CONSOLIDATION_SERIES
from engine.model import ModelProvider, ToolCall, ToolSpec
from engine.retrieval import (
    METRICS,
    AggregateSpec,
    CountSpec,
    InsightSpec,
    LookupSpec,
    RecallSpec,
    RetrievalSpecError,
    TimelineSpec,
    aggregate_memories,
    count_events,
    embed_query,
    get_timeline,
    lookup_events,
    lookup_insights,
    recall_memories,
)
from engine.types import INSIGHT_KINDS, MEMORY_TYPE_REGISTRY

# Tool names — the planner's vocabulary. Kept identical to the doc-canonical names in
# 03-memory-engine.md so code and design read as the same system.
LOG_MEMORY = "log_memory"
AGGREGATE_MEMORIES = "aggregate_memories"
RECALL_MEMORIES = "recall_memories"
GET_TIMELINE = "get_timeline"
LOOKUP_EVENTS = "lookup_events"
COUNT_EVENTS = "count_events"
LOOKUP_INSIGHTS = "lookup_insights"
ANALYZE_SERIES = "analyze_series"

RETRIEVAL_TOOLS = frozenset(
    {AGGREGATE_MEMORIES, RECALL_MEMORIES, GET_TIMELINE, LOOKUP_EVENTS, COUNT_EVENTS,
     LOOKUP_INSIGHTS}
)
#: The two **write** tools. Neither is in ``RETRIEVAL_TOOLS`` and neither goes through
#: ``prepare_call``/``execute``: the graph dispatches both to a service (M4-1, §4.9). Keeping
#: them out of the builder set is what makes "the closed retrieval set is read-only" (**I-17**)
#: a structural fact rather than a convention someone has to remember.
WRITE_TOOLS = frozenset({LOG_MEMORY, ANALYZE_SERIES})
TOOL_NAMES = RETRIEVAL_TOOLS | WRITE_TOOLS


class ToolCallError(ValueError):
    """A planner tool call could not be turned into a valid typed spec: unknown tool,
    missing/extra-shaped slots, unparseable timestamps, or slot values the engine rejects
    (wrapped ``RetrievalSpecError``). Raised **before** any SQL is composed."""


# ── 1. vocabulary ──────────────────────────────────────────────────────────────────────
_ISO_UTC = "ISO-8601 timestamp, e.g. 2026-07-24T00:00:00+05:30"


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def build_tool_specs() -> list[ToolSpec]:
    """The tool vocabulary handed to ``ModelProvider.plan`` (M4).

    Includes ``log_memory``: with it offered, selecting it *is* the ingest classification
    (M4-1, routing as tool selection). Timezone is deliberately **not** a slot — it is a
    property of the user, not of the question, so the engine injects it (see
    ``prepare_call``); letting a model invent timezones would put language interpretation
    where determinism belongs.
    """
    metrics = sorted(METRICS)
    types = sorted(MEMORY_TYPE_REGISTRY)
    return [
        ToolSpec(
            name=LOG_MEMORY,
            description=(
                "Signal that THE CURRENT USER TURN states something worth remembering (a "
                "meal, workout, weight, sleep, supplement, body scan...). Select this "
                "whenever the current turn reports something loggable, even if it ALSO asks "
                "a question — in that case select the retrieval tools too.\n"
                "This tool takes NO arguments and carries no text. It is a signal, not a "
                "payload: the system always ingests the current user turn's own words, so "
                "there is nothing for you to copy, summarize, or restate. Selecting it twice "
                "is the same as selecting it once.\n"
                "NEVER select it for something reported in an EARLIER turn — that was "
                "already recorded when it was said."
            ),
            # Zero-argument by construction (ADR-14.15). The text that reaches ingestion is
            # `state["question"]` — the bytes the user submitted with THIS request — and the
            # graph never reads anything off this call. A `text` slot existed until the
            # conversation-history work: with prior turns visible to the planner, a
            # model-authored string was the one channel through which a fact from an earlier
            # day could be re-ingested and re-dated to today. There is no such channel now,
            # because there is no slot to put one in.
            input_schema=_schema({}, []),
        ),
        ToolSpec(
            name=AGGREGATE_MEMORIES,
            description=(
                "Compute a number from typed health data over a date range: sums, averages, "
                "min/max, optionally bucketed by day or week. Use for quantitative questions "
                "('how much protein this month?', 'average sleep last week?'). Counts here "
                "count logged values OF THAT METRIC — to count events of a type, use "
                "count_events."
            ),
            input_schema=_schema(
                {
                    "metric": {
                        "type": "string",
                        "enum": metrics,
                        "description": "Which typed measurement to aggregate.",
                    },
                    "agg": {
                        "type": "string",
                        "enum": ["sum", "avg", "count", "min", "max"],
                        "description": "Aggregation function (default sum).",
                    },
                    "group_by": {
                        "type": "string",
                        "enum": ["none", "day", "week"],
                        "description": (
                            "Bucketing. Use 'none' for one total, 'day'/'week' for a trend."
                        ),
                    },
                    "start": {
                        "type": "string",
                        "description": f"Range start, inclusive. {_ISO_UTC}",
                    },
                    "end": {"type": "string", "description": f"Range end, exclusive. {_ISO_UTC}"},
                },
                ["metric", "start", "end"],
            ),
        ),
        ToolSpec(
            name=RECALL_MEMORIES,
            description=(
                "Semantic search over what the user wrote, by meaning rather than exact "
                "words. Use for narrative questions ('when did I complain about my knee?') "
                "and as the fuzzy companion to lookup_events, whose item filter only ignores "
                "case/whitespace/punctuation, not wording: for 'when did I last eat "
                "chicken?', issue BOTH so wordings like 'grilled chicken' are still found."
            ),
            input_schema=_schema(
                {
                    "query": {
                        "type": "string",
                        "description": "Natural-language description of what to find.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "How many memories to return (1-50, default 8).",
                    },
                    "type": {
                        "type": "string",
                        "enum": types,
                        "description": "Optional: restrict to one memory type.",
                    },
                    "start": {"type": "string", "description": f"Optional range start. {_ISO_UTC}"},
                    "end": {"type": "string", "description": f"Optional range end. {_ISO_UTC}"},
                },
                ["query"],
            ),
        ),
        ToolSpec(
            name=GET_TIMELINE,
            description=(
                "List the user's logged events in a date range, oldest first. Use for "
                "'what was I doing in May?' or to give an answer chronological context."
            ),
            input_schema=_schema(
                {
                    "start": {
                        "type": "string",
                        "description": f"Range start, inclusive. {_ISO_UTC}",
                    },
                    "end": {"type": "string", "description": f"Range end, exclusive. {_ISO_UTC}"},
                    "types": {
                        "type": "array",
                        "items": {"type": "string", "enum": types},
                        "description": "Optional: restrict to these memory types.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max events to return (1-1000, default 200).",
                    },
                },
                ["start", "end"],
            ),
        ),
        ToolSpec(
            name=LOOKUP_EVENTS,
            description=(
                "Find the most recent (or earliest) event of a type, optionally filtered to "
                "an item by name match on the extracted items (case/whitespace/punctuation "
                "insensitive, but not fuzzy). Use for 'when did I last...?'. Because the "
                "match is not fuzzy, pair it with recall_memories when the item could be "
                "worded differently."
            ),
            input_schema=_schema(
                {
                    "type": {
                        "type": "string",
                        "enum": types,
                        "description": "Which memory type to look up.",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["last", "first"],
                        "description": "Newest ('last', default) or oldest ('first').",
                    },
                    "item": {
                        "type": "string",
                        "description": (
                            "Optional exact item name to filter on, e.g. 'chicken'. Use the "
                            "plain base name as the user would say it."
                        ),
                    },
                    "n": {"type": "integer", "description": "How many events (1-20, default 1)."},
                },
                ["type"],
            ),
        ),
        ToolSpec(
            name=ANALYZE_SERIES,
            description=(
                "Analyse one series NOW and derive a fresh pattern from it — use only when the "
                "user asks for new analysis ('analyse my protein', 'what does my latest scan "
                "show?'). This does work and may record a new insight. To read patterns that "
                "already exist, use lookup_insights instead, which is cheaper and does not "
                "re-derive anything."
            ),
            input_schema=_schema(
                {
                    "metric": {
                        "type": "string",
                        "enum": sorted(CONSOLIDATION_SERIES),
                        "description": "Which series to analyse.",
                    }
                },
                ["metric"],
            ),
        ),
        ToolSpec(
            name=LOOKUP_INSIGHTS,
            description=(
                "Look up patterns the engine has ALREADY derived from this user's data — "
                "'have you noticed anything?', 'has this been flagged before?', 'what have you "
                "learned about my protein?'. Returns existing hypotheses with their evidence "
                "and pattern strength. It only reads what has been derived; it never derives "
                "anything new."
            ),
            input_schema=_schema(
                {
                    "metric": {
                        "type": "string",
                        "enum": sorted(CONSOLIDATION_SERIES),
                        "description": "Optional: only insights about this series.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": sorted(INSIGHT_KINDS),
                        "description": (
                            "Optional: 'level_shift' (a metric's level moved) or "
                            "'intervention_outcome' (a measured marker changed, with the "
                            "logged changes in between)."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "How many insights to return (1-20, default 10).",
                    },
                },
                [],
            ),
        ),
        ToolSpec(
            name=COUNT_EVENTS,
            description=(
                "Count how many events of a type the user logged in a date range ('how many "
                "workouts in June?'). Counts events regardless of which measurements they "
                "carry."
            ),
            input_schema=_schema(
                {
                    "type": {
                        "type": "string",
                        "enum": types,
                        "description": "Which memory type to count.",
                    },
                    "start": {
                        "type": "string",
                        "description": f"Range start, inclusive. {_ISO_UTC}",
                    },
                    "end": {"type": "string", "description": f"Range end, exclusive. {_ISO_UTC}"},
                },
                ["type", "start", "end"],
            ),
        ),
    ]


# ── 2. validation ──────────────────────────────────────────────────────────────────────
RetrievalSpec = (
    AggregateSpec | RecallSpec | TimelineSpec | LookupSpec | CountSpec | InsightSpec
)


@dataclass(frozen=True, slots=True)
class PreparedCall:
    """A validated tool call, with any model work already done (recall's query vector).
    ``execute`` needs nothing but a cursor after this."""

    tool: str
    spec: RetrievalSpec
    query_vec: list[float] | None = None


def is_log_memory(call: ToolCall) -> bool:
    """True for the ingestion tool, which the graph dispatches to IngestionService rather
    than through ``prepare_call``/``execute``."""
    return call.tool == LOG_MEMORY


def is_analyze_series(call: ToolCall) -> bool:
    """True for the consolidation tool, which the graph dispatches to ConsolidationService.

    It is a *verb*, not a query: it may write an insight, and a write cannot ride the shared
    read transaction ``execute`` runs inside (§4.9). Same treatment as ``log_memory`` for the
    same reason."""
    return call.tool == ANALYZE_SERIES


def analyze_series_metric(call: ToolCall) -> str:
    """Extract and validate the ``metric`` slot of an analyze_series call.

    Strict, like every other slot (ADR-14.11): an unknown or missing series is rejected rather
    than defaulted to "analyse everything", which would be a different — and far more
    expensive — request than the one the planner made."""
    metric = _args(call).get("metric")
    if not isinstance(metric, str) or metric not in CONSOLIDATION_SERIES:
        raise ToolCallError(
            f"analyze_series requires a known 'metric'; known: {sorted(CONSOLIDATION_SERIES)}"
        )
    return metric


#: Deliberately no ``log_memory_text``. ``log_memory`` is a zero-argument signal (ADR-14.15):
#: the only text that may reach ``IngestionService`` is the current turn's own ``question``,
#: read off graph state by ``ingest_node``. Any arguments a model attaches to the call are
#: ignored rather than rejected — a stray slot is a model quirk, not a reason to lose the
#: user's input (never-lose-input, ADR-13.5). If you are looking for the function that used to
#: turn a planner-authored string into a memory: it was removed on purpose, and re-adding one
#: would reopen the re-ingestion hole that decision closes.


def _args(call: ToolCall) -> dict:
    """A tool call's arguments as a dict. A provider that returns a non-dict payload is a
    malformed call, not a crash: it degrades to empty so required-slot validation reports
    the real problem."""
    return call.arguments if isinstance(call.arguments, dict) else {}


def prepare_call(call: ToolCall, *, model: ModelProvider, tz: str) -> PreparedCall:
    """Validate one retrieval tool call into a typed spec, doing any model work off-transaction.

    ``tz`` is the user's timezone, injected by the caller — never a planner slot. Raises
    ``ToolCallError`` for anything malformed; the underlying ``RetrievalSpecError`` is
    chained so the real reason survives.
    """
    if call.tool in WRITE_TOOLS:
        raise ToolCallError(f"{call.tool} is dispatched by the graph, not prepare_call")
    if call.tool not in RETRIEVAL_TOOLS:
        raise ToolCallError(f"unknown tool {call.tool!r}; known: {sorted(TOOL_NAMES)}")

    try:
        spec = _build_spec(call.tool, _args(call), tz=tz)
    except RetrievalSpecError as exc:
        raise ToolCallError(f"{call.tool}: {exc}") from exc

    query_vec = None
    if call.tool == RECALL_MEMORIES:
        # Model work belongs OUTSIDE the transaction (engine/db.py discipline); the engine
        # embeds the query, not the agent (decision D-4).
        query_vec = embed_query(model, spec.query)

    return PreparedCall(tool=call.tool, spec=spec, query_vec=query_vec)


def _omit_none(**kwargs) -> dict:
    """Drop ``None`` values so each spec's own default applies.

    Always this, never ``x or default``: a legitimate ``0`` is falsy and must reach the spec
    to be *rejected*, not silently replaced by a default (the bug this helper exists to make
    unrepeatable)."""
    return {key: value for key, value in kwargs.items() if value is not None}


def _build_spec(tool: str, args: dict, *, tz: str) -> RetrievalSpec:
    if tool == AGGREGATE_MEMORIES:
        return AggregateSpec(
            metric=_req_str(args, "metric", tool),
            start=_req_dt(args, "start", tool, tz),
            end=_req_dt(args, "end", tool, tz),
            tz=tz,
            **_omit_none(agg=_opt_str(args, "agg"), group_by=_opt_str(args, "group_by")),
        )
    if tool == RECALL_MEMORIES:
        return RecallSpec(
            query=_req_str(args, "query", tool),
            type=_opt_str(args, "type"),
            start=_opt_dt(args, "start", tool, tz),
            end=_opt_dt(args, "end", tool, tz),
            **_omit_none(top_k=_opt_int(args, "top_k")),
        )
    if tool == GET_TIMELINE:
        return TimelineSpec(
            start=_req_dt(args, "start", tool, tz),
            end=_req_dt(args, "end", tool, tz),
            types=_opt_types(args, tool),
            **_omit_none(limit=_opt_int(args, "limit")),
        )
    if tool == LOOKUP_EVENTS:
        return LookupSpec(
            type=_req_str(args, "type", tool),
            item=_opt_str(args, "item"),
            **_omit_none(direction=_opt_str(args, "direction"), n=_opt_int(args, "n")),
        )
    if tool == LOOKUP_INSIGHTS:
        # Deliberately no date range: an insight already carries the window it is about, and a
        # planner-chosen range would filter claims by *when they were derived* rather than what
        # they are about — a subtly wrong answer that looks right.
        return InsightSpec(
            metric=_opt_str(args, "metric"),
            kind=_opt_str(args, "kind"),
            **_omit_none(limit=_opt_int(args, "limit")),
        )
    if tool == COUNT_EVENTS:
        return CountSpec(
            type=_req_str(args, "type", tool),
            start=_req_dt(args, "start", tool, tz),
            end=_req_dt(args, "end", tool, tz),
        )
    raise ToolCallError(f"unhandled tool {tool!r}")  # pragma: no cover — guarded above


# ── slot coercion helpers (strict: no invented defaults for required slots) ─────────────
def _req_str(args: dict, key: str, tool: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolCallError(f"{tool}: missing required '{key}'")
    return value


def _opt_str(args: dict, key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolCallError(f"'{key}' must be a non-empty string when provided")
    return value


def _opt_types(args: dict, tool: str) -> tuple[str, ...] | None:
    """The optional ``types`` filter. An explicitly empty list is passed through as an empty
    tuple so ``TimelineSpec`` **rejects** it — silently reinterpreting "filter to nothing" as
    "no filter at all" would answer a different question than the planner asked for."""
    types = args.get("types")
    if types is None:
        return None
    if not isinstance(types, (list, tuple)):
        raise ToolCallError(f"{tool}: 'types' must be a list of memory types")
    return tuple(str(t) for t in types)


def _opt_int(args: dict, key: str) -> int | None:
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ToolCallError(f"'{key}' must be an integer when provided")
    return int(value)


def _req_dt(args: dict, key: str, tool: str, tz: str) -> datetime:
    value = args.get(key)
    if value is None:
        # Deliberately strict: a missing date range is NOT defaulted to a guessed window —
        # inventing a range would answer a question the user did not ask.
        raise ToolCallError(f"{tool}: missing required '{key}' (ISO-8601 timestamp)")
    return _parse_dt(value, key, tz)


def _opt_dt(args: dict, key: str, tool: str, tz: str) -> datetime | None:
    value = args.get(key)
    return None if value is None else _parse_dt(value, key, tz)


def _parse_dt(value: object, key: str, tz: str) -> datetime:
    """Parse a planner-supplied ISO-8601 timestamp. A naive value is localized to the
    user's timezone — the specs require aware datetimes, and the user's zone is the only
    honest interpretation of an unqualified local time."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ToolCallError(f"'{key}' is not an ISO-8601 timestamp: {value!r}") from exc
    else:
        raise ToolCallError(
            f"'{key}' must be an ISO-8601 timestamp string, got {type(value).__name__}"
        )

    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(tz))
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ToolCallError(f"cannot localize '{key}': unknown timezone {tz!r}") from exc
    return parsed


# ── 3. execution (pure database work — call inside one transaction) ────────────────────
def execute(cur: psycopg.Cursor, user_id: UUID, prepared: PreparedCall) -> RetrievalOutcome:
    """Run a prepared call against the engine's builders and wrap the ``(result, step)``
    pair as a ``RetrievalOutcome`` for assembly. No model calls, no language, no raw SQL."""
    spec = prepared.spec
    if prepared.tool == AGGREGATE_MEMORIES:
        result, step = aggregate_memories(cur, user_id, spec)
    elif prepared.tool == RECALL_MEMORIES:
        if prepared.query_vec is None:
            # prepare_call guarantees this; an explicit raise (not `assert`, which `-O`
            # strips) keeps a hand-built PreparedCall from reaching SQL with no vector.
            raise ToolCallError("recall_memories requires a prepared query vector")
        result, step = recall_memories(cur, user_id, spec, prepared.query_vec)
    elif prepared.tool == GET_TIMELINE:
        result, step = get_timeline(cur, user_id, spec)
    elif prepared.tool == LOOKUP_EVENTS:
        result, step = lookup_events(cur, user_id, spec)
    elif prepared.tool == COUNT_EVENTS:
        result, step = count_events(cur, user_id, spec)
    elif prepared.tool == LOOKUP_INSIGHTS:
        result, step = lookup_insights(cur, user_id, spec)
    else:  # pragma: no cover — prepare_call already rejected unknown tools
        raise ToolCallError(f"unexecutable tool {prepared.tool!r}")
    return RetrievalOutcome(result=result, step=step)
