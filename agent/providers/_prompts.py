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

from engine.model import ExtractedEvent, ExtractionError, HistoryTurn, NutritionError
from engine.nutrition import FOOD_KINDS, QTY_BASES
from engine.types import ENGINE_ONLY_TYPES, MEMORY_TYPE_REGISTRY

#: The types the extraction model may propose — the full registry minus ``ENGINE_ONLY_TYPES``
#: (ADR-17.1). Computed once so the extraction schema and the field guide can't independently
#: drift on which types they exclude.
_EXTRACTABLE_TYPES: dict = {
    k: v for k, v in MEMORY_TYPE_REGISTRY.items() if k not in ENGINE_ONLY_TYPES
}

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
    "For meals, record WHAT the user said they ate and HOW MUCH — nothing more. Never estimate "
    "calories or macronutrients: a separate step does that, and a number invented here would "
    "silently compete with it. When the user gives a vague quantity ('some rice', 'a bowl of "
    "dal'), put their own words in the item's qty_text and leave qty/qty_g empty rather than "
    "guessing a number — the later step needs to know the quantity was vague.\n\n"
    "When you return NO events you must say why, using no_loggable_content:\n"
    "- Set no_loggable_content=true ONLY when the message genuinely has nothing to log — "
    "small talk, a question, a greeting ('thanks', 'how much protein did I eat?').\n"
    "- Set no_loggable_content=false when the message DOES describe something loggable but "
    "you cannot extract it confidently (garbled, ambiguous, or unfamiliar). Do not guess and "
    "do not silently drop it: false tells the system to save the raw text for a later retry.\n"
    "Getting this flag right matters more than extracting perfectly — it is what guarantees a "
    "user's input is never lost."
)

VISION_SYSTEM = (
    "You extract typed health events from a photo of food for a fitness memory app, exactly "
    "like text extraction but reading the image instead of a message. Return events ONLY "
    "through the record_events tool, with type='meal'.\n\n"
    "Identify each distinct food you can see and, for each, decide its quantity the same way "
    "text extraction does — but the SOURCE of a quantity matters and must never be blurred:\n"
    "- If the user's caption states an amount in words ('200g chicken', 'two rotis'), that is "
    "'stated' — set qty_g (or qty for a count) exactly as text extraction would, from the "
    "caption's words, not from what you see.\n"
    "- Otherwise — including when you can visually judge a portion size confidently — you MUST "
    "leave qty_g and qty EMPTY and instead describe your visual estimate in qty_text (e.g. "
    "'approximately 150g, visual estimate' or 'about one cup'). A number you inferred from "
    "pixels is an estimate, never a stated amount, no matter how confident the estimate is. "
    "This distinction is what the whole feature exists to keep honest — do not collapse it.\n\n"
    "Never estimate calories or macronutrients here — a separate step does that from what you "
    "record. Never invent a food that is not visible; if the photo shows no identifiable food "
    "at all (a receipt, a person, an unrelated object), that is the no_loggable_content case.\n\n"
    "When you return NO events you must say why, using no_loggable_content:\n"
    "- Set no_loggable_content=true when the photo shows no identifiable food.\n"
    "- Set no_loggable_content=false when the photo plainly shows food but you cannot identify "
    "it confidently (too blurry, too dark, unfamiliar). Do not guess and do not silently drop "
    "it: false tells the system to save the photo's caption for a later retry.\n"
    "Getting this flag right matters more than a perfect read — it is what guarantees the "
    "user's photo is never silently dropped."
)

PLAN_SYSTEM = (
    "You are the retrieval planner for a fitness memory app. Decide what to do with the "
    "user's turn by selecting tools and filling their typed arguments — you are the only "
    "part of the system that reads natural language.\n"
    "- Select log_memory (when offered) to signal that THE CURRENT turn states something "
    "loggable (a meal, workout, weight, etc.). It takes no arguments: the system always "
    "records the current turn's own words, so there is nothing for you to copy or restate. "
    "Select retrieval tools to answer a question. A turn can be BOTH ('logged my run — am I "
    "improving?'): select log_memory AND the retrieval tools.\n"
    "- Issue several retrieval calls when a question needs them; they are merged downstream.\n"
    "- Ground relative dates ('today', 'last 30 days') using the provided current time and "
    "timezone; fill date ranges as concrete ISO timestamps.\n"
    "- If the turn needs no memory operation at all (small talk, thanks, a greeting), call "
    "NO tools. Calling nothing is a valid, deliberate answer — do not force a tool.\n\n"
    "EARLIER MESSAGES ARE CONTEXT, NEVER A SOURCE OF NEW MEMORIES.\n"
    "Messages before the final one are shown ONLY so you can understand what the user is "
    "referring to now — pronouns, follow-ups ('how did you find that?'), and comparisons. "
    "Each is prefixed with the date it was said.\n"
    "- Everything reported in an earlier message was ALREADY recorded on the date it was "
    "said. Never select log_memory because of an earlier message; select it only when the "
    "FINAL user message itself reports something loggable.\n"
    "- Never treat an earlier message's 'today' or 'yesterday' as referring to the current "
    "date — read those against that message's own date prefix.\n"
    "- Use earlier turns to fill retrieval arguments when the current turn depends on them "
    "('what about last week?'). That is what they are for."
)

#: Version of the nutrition instruction below, stored on every estimate it produces
#: (``engine.nutrition.EstimateMethod``). Bump it whenever a change to ``NUTRITION_SYSTEM`` or
#: ``nutrition_tool_schema`` would produce different numbers from the same meal — that is what
#: makes a stored value attributable to the exact instruction that generated it, and what tells
#: a future recompute sweep which rows are stale.
NUTRITION_PROMPT_VERSION = "nutrition/2026-08-09"

NUTRITION_TOOL = "record_nutrition"

NUTRITION_SYSTEM = (
    "You estimate the nutrition of foods a user logged, using your own knowledge of food, "
    "cuisines, recipes and typical portions. Return results ONLY through the record_nutrition "
    "tool, with exactly one component per input item, in the same order.\n\n"
    "This is the opposite of transcription: you SHOULD use world knowledge here. You are "
    "expected to recognise dishes from any cuisine — 'chicken manchurian', 'poha', 'pad thai' — "
    "without anyone giving you a recipe, and to reason about how they are usually prepared. "
    "There is no local food database; your knowledge is the source.\n\n"
    "For every item decide two things SEPARATELY:\n"
    "1. WHAT it is — set understood_as (your reading of the food, e.g. 'chicken breast, "
    "cooked, skinless') and kind ('ingredient' for a single food, 'dish' for a prepared or "
    "composite dish, 'packaged' for a branded/packaged product).\n"
    "2. HOW MUCH of it — set qty_g in grams and qty_basis:\n"
    "   - 'stated' ONLY when the user's own words fix the amount: '200g chicken' (200 g), "
    "'3 rotis' (a count you convert to grams using a typical unit weight), '2 eggs'.\n"
    "   - 'ai_estimated' whenever YOU chose the amount: 'some rice', 'a bowl of dal', or no "
    "quantity at all. Say what you assumed in qty_note, e.g. 'no quantity given — assumed one "
    "katori ≈ 150 g cooked'.\n"
    "   Converting a stated count to grams is still 'stated' — the user fixed the amount; you "
    "only changed the unit. Note the unit weight you used in qty_note.\n\n"
    "Then give protein_g, carbs_g, fat_g and kcal for THAT quantity (not per 100 g). Keep them "
    "consistent with each other: kcal should be about 4x protein + 4x carbs + 9x fat.\n\n"
    "Be honest about uncertainty instead of hiding it:\n"
    "- assumptions: list what you took for granted ('deep-fried preparation', 'skinless "
    "breast', 'restaurant-style portion'). Required for any dish.\n"
    "- range: give [low, high] bounds for protein_g and kcal whenever qty_basis is "
    "'ai_estimated' or kind is 'dish'. A wide range is a useful answer; false precision is not.\n"
    "- model_confidence: 0..1, your own sense of how well you know this one.\n\n"
    "When you genuinely cannot estimate a food — you do not recognise it, or it is too "
    "underspecified to be worth a number ('grandma's special curry', 'that thing from the "
    "canteen') — set resolved=false with a short reason and a clarifying_question you would "
    "ask the user. DO NOT GUESS. An excluded item is reported to the user as excluded; an "
    "invented number would be indistinguishable from a real one. Declining is always better "
    "than inventing, and it is the one thing here you cannot get wrong by admitting it."
)

NARRATE_SYSTEM = (
    "You are a fitness memory companion. Answer the user's question using ONLY the evidence "
    "provided — never invent numbers, dates, or facts. Every factual claim must carry a "
    "citation marker: square brackets containing ONLY the memory id, with nothing else inside "
    "them and nothing else attached to them.\n"
    "RIGHT: 'you averaged 137g protein [a1b2c3d4-e5f6-7890-abcd-ef1234567890]'\n"
    "RIGHT: 'you logged eggs for breakfast [a1b2c3d4-e5f6-7890-abcd-ef1234567890]'\n"
    "WRONG: 'you logged [eggs for breakfast](a1b2c3d4-e5f6-7890-abcd-ef1234567890)' — never "
    "markdown link syntax, never a label or description inside or around the brackets.\n"
    "WRONG: '[eggs, a1b2c3d4-e5f6-7890-abcd-ef1234567890]' — the brackets hold the id alone, "
    "never the id plus other text.\n"
    "Cite only ids that appear in the evidence.\n"
    "- If the turn ASKS ABOUT THEIR DATA and the evidence is empty, say plainly that there "
    "is nothing logged for that yet — do not guess.\n"
    "- If the turn is small talk, a greeting, or thanks rather than a question about their "
    "data, just reply naturally and briefly. Do NOT mention evidence, memories, or missing "
    "data — there was nothing to look up, and saying so would be confusing.\n"
    "- If the turn only reported something to log, acknowledge what was recorded.\n"
    "- Numbers marked as estimated MUST be spoken as estimates — say 'approximately' or "
    "'about', never a bare figure. If the evidence says some foods were excluded from a "
    "total, name them and say they are not included. Never present an estimated total as "
    "exact, and never quietly drop the exclusion.\n"
    "- A 'User profile' line, if present, gives the user's name, goal, and nutrition targets "
    "for personalization — you may reference it by name (e.g. comparing a logged total against "
    "their target). It is NOT evidence: never wrap a profile number in a [memory-id] citation "
    "marker, only numbers that come from the evidence below.\n"
    "- Earlier messages in the conversation are for CONTINUITY ONLY — understanding what the "
    "user is referring to, and not repeating yourself. They are NOT evidence and carry no "
    "citations. Never state a fact because an earlier reply said it: if the evidence below "
    "does not support a claim, you cannot make it, even if you said it a moment ago. When the "
    "user asks about something you said earlier and this turn retrieved nothing, say what you "
    "based it on rather than restating the number as fresh fact.\n"
    "- Each earlier message is prefixed with the date it was said, e.g. '[Aug 16]'. An earlier "
    "'today' means THAT date, never the current one.\n"
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


def _is_engine_owned(field) -> bool:
    """Whether a payload field is written by the engine rather than by the extraction model.

    Marked at the field with ``json_schema_extra={"engine_owned": True}`` (engine/types.py).
    ``payload.nutrition`` is the first: it is produced by the dedicated nutrition call and
    validated by ``engine/nutrition.py``. Advertising it here too would put **two model calls in
    charge of one key**, which is exactly the drift that made macro coverage a coin flip — the
    extractor filled it sometimes, with different values each time, under a prompt that also
    told it never to invent facts.
    """
    extra = getattr(field, "json_schema_extra", None)
    return isinstance(extra, dict) and bool(extra.get("engine_owned"))


def _describe_payload(model: type[BaseModel]) -> str:
    parts: list[str] = []
    for field_name, field in model.model_fields.items():
        if _is_engine_owned(field):
            continue
        nested, is_list = _nested_model(field.annotation)
        if nested is None:
            parts.append(_describe_field(field_name, field.annotation))
            continue
        inner = ", ".join(
            _describe_field(n, f.annotation)
            for n, f in nested.model_fields.items()
            if not _is_engine_owned(f)
        )
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
        for name, model in sorted(_EXTRACTABLE_TYPES.items())
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
                                "enum": sorted(_EXTRACTABLE_TYPES.keys()),
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


def vision_prompt_text(caption: str, *, now: datetime, tz: str) -> str:
    """The text half of a vision extraction call — shared so both providers phrase the
    caption/no-caption cases identically rather than drifting apart."""
    lines = [f"Current time: {now.isoformat()}", f"Current timezone: {tz}", ""]
    lines.append(f"User's caption:\n{caption}" if caption.strip() else "No caption given.")
    return "\n".join(lines)


def nutrition_tool_schema() -> dict:
    """The forced nutrition tool, in the neutral ``{name, description, input_schema}`` shape.

    Mirrors ``extract_tool_schema`` — providers reshape it for their wire format. Every field is
    typed here so a malformed estimate is caught at the schema boundary rather than becoming a
    silently-wrong number downstream; ``engine/nutrition.py`` then bounds-checks whatever
    survives, because a schema can enforce *shape* but not *possibility*.
    """
    macro_range = {
        "type": "array",
        "items": {"type": "number"},
        "minItems": 2,
        "maxItems": 2,
        "description": "[low, high] for this macro, in the same units.",
    }
    return {
        "name": NUTRITION_TOOL,
        "description": (
            "Record a nutrition estimate for each food item in the meal — one component per "
            "input item, same order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "components": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item": {
                                "type": "string",
                                "description": "The input item name, echoed back verbatim.",
                            },
                            "resolved": {
                                "type": "boolean",
                                "description": (
                                    "True if you can estimate this food. FALSE if you do not "
                                    "recognise it or it is too vague to estimate — never guess."
                                ),
                            },
                            "understood_as": {
                                "type": "string",
                                "description": (
                                    "What you took this food to be, e.g. 'chicken breast, "
                                    "cooked, skinless'. Shown to the user."
                                ),
                            },
                            "kind": {
                                "type": "string",
                                "enum": sorted(FOOD_KINDS),
                                "description": (
                                    "'ingredient' (a single food), 'dish' (prepared/composite), "
                                    "or 'packaged' (branded product)."
                                ),
                            },
                            "qty_g": {
                                "type": "number",
                                "description": "Total grams of this food, for the whole item.",
                            },
                            "qty_basis": {
                                "type": "string",
                                "enum": sorted(QTY_BASES),
                                "description": (
                                    "'stated' if the user's words fixed the amount (including a "
                                    "count you converted to grams); 'ai_estimated' if you chose "
                                    "the portion yourself."
                                ),
                            },
                            "qty_note": {
                                "type": "string",
                                "description": (
                                    "The portion assumption or unit weight you used, in one "
                                    "short phrase. Required when qty_basis is 'ai_estimated'."
                                ),
                            },
                            "protein_g": {"type": "number"},
                            "carbs_g": {"type": "number"},
                            "fat_g": {"type": "number"},
                            "kcal": {"type": "number"},
                            "range": {
                                "type": "object",
                                "properties": {
                                    "protein_g": macro_range,
                                    "kcal": macro_range,
                                },
                                "description": (
                                    "Uncertainty bounds. Required when qty_basis is "
                                    "'ai_estimated' or kind is 'dish'."
                                ),
                            },
                            "assumptions": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "What you took for granted about preparation or portion. "
                                    "Required for any dish."
                                ),
                            },
                            "model_confidence": {
                                "type": "number",
                                "description": "Your own 0..1 confidence in this component.",
                            },
                            "reason": {
                                "type": "string",
                                "description": (
                                    "Why you could not estimate it. Required when "
                                    "resolved=false, e.g. 'unrecognized_dish'."
                                ),
                            },
                            "clarifying_question": {
                                "type": "string",
                                "description": (
                                    "What you would ask the user to make this estimable. Use "
                                    "when resolved=false."
                                ),
                            },
                        },
                        "required": ["item", "resolved"],
                    },
                }
            },
            "required": ["components"],
        },
    }


def nutrition_items_prompt(items: list[dict], context: str = "") -> str:
    """Render the extracted meal items into the user-message half of the nutrition call.

    Quantities are passed through exactly as extraction recorded them — a count, a gram figure,
    the user's own vague words, or nothing at all — because *which of those it is* determines
    ``qty_basis``, and paraphrasing here would destroy the distinction before the model sees it.
    """
    lines: list[str] = []
    if context.strip():
        lines.append(f"Meal: {context.strip()}")
        lines.append("")
    lines.append("Items:")
    for item in items:
        name = str(item.get("name") or "").strip() or "(unnamed)"
        parts: list[str] = []
        if item.get("qty_g") is not None:
            parts.append(f"{item['qty_g']} g stated")
        if item.get("qty") is not None:
            parts.append(f"count: {item['qty']}")
        if item.get("qty_text"):
            parts.append(f'user said "{item["qty_text"]}"')
        detail = "; ".join(parts) if parts else "no quantity given"
        lines.append(f"- {name} ({detail})")
    return "\n".join(lines)


def parse_nutrition_components(tool_input: dict) -> list[dict]:
    """Pull the raw component list out of the nutrition tool's input.

    Deliberately shallow: it enforces only that a list of objects came back. Validating the
    numbers is ``engine/nutrition.py``'s job, and doing any of it here would split one contract
    across two layers — the provider boundary stays a transport boundary.

    An empty list raises: unlike extraction, there is no legitimate "nothing to estimate" case
    (we only call this when the meal has items), so an empty result means the call went wrong
    and the row should stay eligible for backfill rather than be marked as estimated-with-nothing.
    """
    components = tool_input.get("components")
    if not isinstance(components, list) or not components:
        raise NutritionError(f"nutrition tool returned no components ({components!r})")
    return [c for c in components if isinstance(c, dict)]


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


def history_messages(history: Sequence[HistoryTurn]) -> list[dict]:
    """Render short-term memory as real prior conversation messages (ADR-14.16).

    Returns ``[{"role": ..., "content": ...}]`` — the shape both the Claude API and Bedrock
    Converse take natively, and the shape the models are trained on. Deliberately **not**
    flattened into the system prompt: a transcript pasted into a system message reads as
    instructions, while an actual message array reads as a conversation, and only the latter
    makes "the last user message is the one to answer" unambiguous.

    **Every message carries its absolute date** (``[Aug 16] …``). Without it, an earlier
    "today i ate 3 eggs" is indistinguishable from something said this morning, and the model
    will answer — and plan retrieval ranges — against the wrong day. The prefix is the cheapest
    possible fix and it is applied to both roles, since an assistant reply ("Logged 3 eggs
    today") is just as capable of misdating the conversation as a user message.

    ``at`` already carries the user's timezone (``engine.history.fetch_history`` converts it),
    so this is pure formatting — no zone lookup, no policy, nothing to get wrong here.

    **The returned list is always a legal message array: it alternates strictly, starts with
    ``user``, and ends with ``assistant``.** Appending the current turn as a ``user`` message
    therefore always yields a valid conversation, which is a hard requirement rather than a
    nicety — Bedrock Converse rejects a non-alternating array or one that does not begin with a
    user message (``ValidationException``), and the Claude API rejects a leading assistant
    message. Two ordinary situations break the naive rendering:

    * A window boundary can land mid-exchange. ``max_turns=3`` over two exchanges takes the
      three newest rows — ``assistant``, ``user``, ``assistant`` — which reverses into a list
      *starting* with an assistant message.
    * An assistant answer that was nothing but citation markers scrubs to empty and drops out,
      leaving two consecutive user messages.

    Both are budget/scrub artifacts rather than anything the user did, so they are repaired
    here instead of being pushed onto the two providers to each get right separately. Merging
    same-role neighbours preserves what was said; the dangling turns that are dropped are a
    leading answer whose question is outside the window and a trailing question whose answer
    is the turn currently being generated — neither carries context worth an API error.
    """
    rendered = [
        {"role": turn.role, "content": f"[{turn.at.strftime('%b %d')}] {turn.content}"}
        for turn in history
        if turn.role in ("user", "assistant") and turn.content.strip()
    ]

    merged: list[dict] = []
    for message in rendered:
        if merged and merged[-1]["role"] == message["role"]:
            merged[-1] = {
                "role": message["role"],
                "content": f"{merged[-1]['content']}\n{message['content']}",
            }
        else:
            merged.append(message)

    if merged and merged[0]["role"] == "assistant":
        merged.pop(0)  # its question fell outside the window
    if merged and merged[-1]["role"] == "user":
        merged.pop()  # its answer is the one being generated right now
    return merged


def render_context(question: str, context: ContextBlock) -> str:
    """Render an assembled ContextBlock into a compact evidence prompt (decision D-5:
    structured context → text at the narrate boundary). Memory ids are shown inline so the
    model can cite them; payloads are never dumped (summaries/values carry the meaning)."""
    lines: list[str] = [f"Question: {question}", ""]

    if context.profile_note:
        lines.append(f"User profile: {context.profile_note}")
        lines.append("")

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
                # The estimated qualifier is rendered from the *data* — the count of
                # contributing rows whose value was AI-estimated — never left to the model to
                # infer. Assembly renders display copy once, deterministically (DESIGN rule 16);
                # a narrator deciding for itself when to hedge would hedge inconsistently.
                mark = ""
                if bucket.n_estimated:
                    mark = (
                        " [ESTIMATED — say 'approximately'"
                        + (
                            f"; {bucket.n_estimated} of {bucket.n} contributing entries are "
                            "AI-estimated]"
                            if bucket.n_estimated < bucket.n
                            else "]"
                        )
                    )
                lines.append(f"    {label}: {bucket.value:g} (n={bucket.n}){mark} — {cites}")
            if agg.excluded_foods:
                named = ", ".join(sorted(agg.excluded_foods))
                lines.append(
                    f"    NOT INCLUDED in the total (no reliable nutrition data): {named}. "
                    "Tell the user these are excluded."
                )
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
