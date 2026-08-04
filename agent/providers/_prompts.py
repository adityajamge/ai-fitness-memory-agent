"""Provider-agnostic model contracts: prompts, the extraction tool schema, event parsing,
and context rendering.

These are **not** Bedrock or Claude-API specifics — they are the behavioral contract every
ModelProvider must honor: the ``no_loggable_content`` empty-result rule (D1), the
bi-temporal time resolution (04), the planner's empty-plan posture (M4-2), and the narrator's
citation rules (05). Keeping them here means a second provider is a thin wire adapter rather
than a second copy of the contract — and that the two cannot silently drift apart.

Each provider owns only its transport: how it ships this schema and reads the response back.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from types import UnionType
from typing import TYPE_CHECKING, Union, get_args, get_origin

from pydantic import BaseModel

from engine.model import ExtractedEvent, ExtractionError
from engine.types import MEMORY_TYPE_REGISTRY

if TYPE_CHECKING:
    from engine.assembly import ContextBlock

logger = logging.getLogger(__name__)

EXTRACT_TOOL = "record_events"

SYSTEM_PROMPT = (
    "You extract typed health events from a user's message for a fitness memory app. "
    "Return events ONLY through the record_events tool. Infer event_time and timezone from "
    "relative expressions ('yesterday', 'this morning') using the provided current time and "
    "timezone. NEVER invent facts: if a quantity or time is uncertain, lower confidence and, "
    "for time, leave event_time null (the system will estimate it).\n\n"
    "When you return NO events you must say why, using no_loggable_content:\n"
    "- Set no_loggable_content=true ONLY when the message genuinely has nothing to log — "
    "small talk, a question, a greeting ('thanks', 'how much protein did I eat?').\n"
    "- Set no_loggable_content=false when the message DOES describe something loggable but "
    "you cannot extract it confidently (garbled, ambiguous, or unfamiliar). Do not guess and "
    "do not silently drop it: false tells the system to save the raw text for a later retry.\n"
    "Getting this flag right matters more than extracting perfectly — it is what guarantees a "
    "user's input is never lost."
)

PLAN_SYSTEM = (
    "You are the retrieval planner for a fitness memory app. Decide what to do with the "
    "user's turn by selecting tools and filling their typed arguments — you are the only "
    "part of the system that reads natural language.\n"
    "- Select log_memory (when offered) to record a turn that states something loggable "
    "(a meal, workout, weight, etc.). Select retrieval tools to answer a question. A turn "
    "can be BOTH ('logged my run — am I improving?'): select log_memory AND the retrieval "
    "tools.\n"
    "- Issue several retrieval calls when a question needs them; they are merged downstream.\n"
    "- Ground relative dates ('today', 'last 30 days') using the provided current time and "
    "timezone; fill date ranges as concrete ISO timestamps.\n"
    "- If the turn needs no memory operation at all (small talk, thanks, a greeting), call "
    "NO tools. Calling nothing is a valid, deliberate answer — do not force a tool."
)

NARRATE_SYSTEM = (
    "You are a fitness memory companion. Answer the user's question using ONLY the evidence "
    "provided — never invent numbers, dates, or facts. Every factual claim must carry a "
    "citation marker in square brackets containing the exact memory id it rests on, e.g. "
    "'you averaged 137g protein [a1b2c3d4-...]'. Cite only ids that appear in the evidence.\n"
    "- If the turn ASKS ABOUT THEIR DATA and the evidence is empty, say plainly that there "
    "is nothing logged for that yet — do not guess.\n"
    "- If the turn is small talk, a greeting, or thanks rather than a question about their "
    "data, just reply naturally and briefly. Do NOT mention evidence, memories, or missing "
    "data — there was nothing to look up, and saying so would be confusing.\n"
    "- If the turn only reported something to log, acknowledge what was recorded.\n"
    "Be concise and factual."
)


def _scalar_kind(annotation) -> str | None:
    """JSON-ish type hint for a scalar field (``number``/``boolean``), or ``None`` for a
    plain string (no hint needed — string is already the schema's implicit default)."""
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, UnionType):
        annotation = next((a for a in args if a is not type(None)), annotation)
    if annotation is bool:
        return "boolean"
    if annotation in (int, float):
        return "number"
    return None


def _nested_model(annotation) -> tuple[type[BaseModel] | None, bool]:
    """Unwrap ``X | None`` / ``list[X]`` to a nested payload model, plus whether it's a list."""
    is_list = False
    for _ in range(3):  # Optional[list[Model]] is the deepest shape in the registry
        origin, args = get_origin(annotation), get_args(annotation)
        if origin in (Union, UnionType):
            annotation = next((a for a in args if a is not type(None)), None)
        elif origin in (list, Sequence):
            is_list, annotation = True, args[0] if args else None
        else:
            break
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation, is_list
    return None, is_list


def _describe_field(field_name: str, annotation) -> str:
    kind = _scalar_kind(annotation)
    return f"{field_name}:{kind}" if kind else field_name


def _describe_payload(model: type[BaseModel]) -> str:
    parts: list[str] = []
    for field_name, field in model.model_fields.items():
        nested, is_list = _nested_model(field.annotation)
        if nested is None:
            parts.append(_describe_field(field_name, field.annotation))
            continue
        inner = ", ".join(_describe_field(n, f.annotation) for n, f in nested.model_fields.items())
        parts.append(f"{field_name}: [{{{inner}}}]" if is_list else f"{field_name}: {{{inner}}}")
    return ", ".join(parts)


def payload_field_guide() -> str:
    """A compact per-type field guide, **generated from the payload registry**.

    Without it the extraction tool advertises ``payload`` as a bare object and the model has
    to guess field names — a meal item comes back as ``{"food": "curd"}`` instead of
    ``{"name": "curd"}``, validation fails, and the whole turn degrades to a note. Generating
    the guide rather than hand-writing it means a new hot field in ``engine/types.py`` shows
    up in the prompt automatically and the two cannot drift apart.

    Scalar fields also carry a ``:number``/``:boolean`` kind hint (e.g. ``qty_g:number``) —
    live validation showed the same guessing failure one level deeper: without a type hint,
    "250g" arrives as a string in a float field (``qty_g`` / ``distance_km`` / etc. all bake
    their unit into the name) and validation rejects it the same way a misnamed key does.
    """
    return "\n".join(
        f"  {name}: {{{_describe_payload(model)}}}"
        for name, model in sorted(MEMORY_TYPE_REGISTRY.items())
    )


def extract_tool_schema() -> dict:
    """The forced extraction tool, in the neutral ``{name, description, input_schema}`` shape.
    Providers reshape it for their wire format (Bedrock nests it under ``toolSpec``)."""
    return {
        "name": EXTRACT_TOOL,
        "description": "Record the typed health events extracted from the message.",
        "input_schema": {
            "type": "object",
            "properties": {
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": sorted(MEMORY_TYPE_REGISTRY.keys()),
                            },
                            # ISO-8601 timestamp; null if not inferable.
                            "event_time": {"type": ["string", "null"]},
                            "tz": {"type": ["string", "null"]},
                            "confidence": {"type": "number"},
                            # Short NL rendering, embedded for semantic recall AND the only
                            # text a later question's evidence ever shows (EvidenceSnapshot
                            # is deliberately payload-free, ADR-12 — engine/trace.py) — so a
                            # vague summary makes a fact permanently unanswerable, not just
                            # unindexed.
                            "summary": {
                                "type": "string",
                                "description": (
                                    "A short natural-language rendering of this event. "
                                    "Include the concrete details a later question might "
                                    "need — item names AND their quantities, e.g. '250g "
                                    "curd, 3 eggs, 200g grilled chicken' — not a vague "
                                    "description like 'had lunch with curd, eggs, and "
                                    "chicken'. This is the only place that quantity is "
                                    "ever shown to a future question about this event."
                                ),
                            },
                            # Per-type fields. The description carries the registry-derived
                            # field guide — without it the model invents key names
                            # ({"food": ...} instead of {"name": ...}) and validation
                            # rejects the whole turn into a note.
                            "payload": {
                                "type": "object",
                                "description": (
                                    "Fields for this memory type. Use EXACTLY these key "
                                    "names — different names are rejected and the entry is "
                                    "downgraded to raw text. Omit anything you don't know "
                                    "rather than renaming or inventing a key; extra keys "
                                    "beyond those listed are allowed. A field marked "
                                    "`:number` must be a bare JSON number, never a string "
                                    "with a unit suffix — the unit is already encoded in "
                                    "the field name (qty_g is grams, distance_km is "
                                    "kilometers, duration_min is minutes, body_fat_pct is "
                                    'percent): write 200, not "200g".\n'
                                    + payload_field_guide()
                                ),
                            },
                        },
                        "required": ["type", "confidence", "summary", "payload"],
                    },
                },
                # The empty-result contract (engine/model.py): when `events` is empty this
                # flag is the difference between "nothing to log" and "I could not parse
                # this" — required so the model must decide rather than default.
                "no_loggable_content": {
                    "type": "boolean",
                    "description": (
                        "True only if the message contains nothing loggable (small talk, a "
                        "question). False if it does describe something loggable that you "
                        "could not extract."
                    ),
                },
            },
            "required": ["events", "no_loggable_content"],
        },
    }


def parse_extracted_events(
    tool_input: dict, *, now: datetime, tz: str, default_tz: str
) -> list[ExtractedEvent]:
    """Turn the extraction tool's input into typed events, enforcing the empty-result
    contract (engine/model.py). Shared so every provider fails the same way."""
    raw_events = tool_input.get("events") or []
    if not raw_events:
        # Empty is only a legitimate no-op when the model affirms the turn was contentless.
        # Anything else — flag false, missing, or non-boolean — is an unparsed turn, and
        # raising is what routes it to the note fallback instead of dropping it.
        if tool_input.get("no_loggable_content") is True:
            return []
        raise ExtractionError(
            "model returned no events without affirming the turn was contentless "
            f"(no_loggable_content={tool_input.get('no_loggable_content')!r})"
        )
    return [_to_event(event, now=now, tz=tz, default_tz=default_tz) for event in raw_events]


def _to_event(event: dict, *, now: datetime, tz: str, default_tz: str) -> ExtractedEvent:
    event_time, resolved_tz, confidence = _resolve_time(
        event.get("event_time"),
        event.get("tz"),
        float(event.get("confidence", 0.5)),
        now=now,
        tz=tz,
        default_tz=default_tz,
    )
    return ExtractedEvent(
        type=str(event["type"]),
        event_time=event_time,
        tz=resolved_tz,
        confidence=confidence,
        summary=str(event.get("summary", "")),
        payload=event.get("payload") or {},
    )


def _resolve_time(
    raw_time, raw_tz, confidence: float, *, now: datetime, tz: str, default_tz: str
) -> tuple[datetime, str, float]:
    """Parse the model's event_time/tz; fall back to now/default tz with lowered confidence
    when the model couldn't infer them (bi-temporal design, 04)."""
    resolved_tz = raw_tz or tz or default_tz
    if not raw_time:
        return now, resolved_tz, min(confidence, 0.5)
    try:
        parsed = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed, resolved_tz, confidence
    except ValueError:
        logger.info("unparseable event_time %r; estimating as now", raw_time)
        return now, resolved_tz, min(confidence, 0.5)


def render_context(question: str, context: ContextBlock) -> str:
    """Render an assembled ContextBlock into a compact evidence prompt (decision D-5:
    structured context → text at the narrate boundary). Memory ids are shown inline so the
    model can cite them; payloads are never dumped (summaries/values carry the meaning)."""
    lines: list[str] = [f"Question: {question}", ""]

    if context.aggregates:
        lines.append("Computed aggregates:")
        for agg in context.aggregates:
            spec = agg.spec
            header = f"- {spec.agg}({spec.metric})"
            header += f" by {spec.group_by}" if spec.group_by != "none" else ""
            header += f" from {spec.start.date()} to {spec.end.date()}:"
            lines.append(header)
            if not agg.buckets:
                lines.append("    (no matching data in range)")
            for bucket in agg.buckets:
                label = bucket.bucket or "total"
                cites = " ".join(f"[{i}]" for i in bucket.evidence_ids)
                lines.append(f"    {label}: {bucket.value:g} (n={bucket.n}) — {cites}")
        lines.append("")

    if context.counts:
        lines.append("Event counts:")
        for count in context.counts:
            cites = " ".join(f"[{i}]" for i in count.evidence_ids)
            lines.append(
                f"- {count.spec.type} from {count.spec.start.date()} to "
                f"{count.spec.end.date()}: {count.n} — {cites}"
            )
        lines.append("")

    if context.insights:
        lines.append("Patterns the engine has already derived (cite by id):")
        for insight in context.insights:
            lines.append(
                f"- [{insight.id}] {insight.hypothesis} "
                f"(pattern strength {insight.pattern_strength:.2f} = effect "
                f"{insight.effect:.2f} x coverage {insight.coverage:.2f} x specificity "
                f"{insight.specificity:.2f}; {insight.evidence_count} supporting memories)"
            )
        lines.append(
            "  These are labeled heuristic observations, never proof of cause. Present them"
            " as patterns, and do not upgrade a low pattern strength into a confident claim."
        )
        lines.append("")

    if context.memories:
        lines.append("Relevant memories (most relevant first):")
        for memory in context.memories:
            lines.append(
                f"- [{memory.id}] {memory.type} @ {memory.event_time.isoformat()} "
                f"(confidence {memory.confidence:g}, {memory.provenance}): "
                f"{memory.summary or ''}"
            )
        if context.omitted_count:
            lines.append(f"  (+{context.omitted_count} lower-ranked memories omitted)")
        lines.append("")

    if context.is_empty:
        lines.append("No matching memories were found for this question.")

    return "\n".join(lines).rstrip()
