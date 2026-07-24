"""Amazon Bedrock implementation of engine.model.ModelProvider (Phase 2: extract + embed).

This is the only place boto3 is imported. Extraction uses the Bedrock **Converse** API with
a *forced tool call*, so the model's entire output is a structured events object (no prose to
parse, no JSON-in-markdown fragility). Embeddings use **Titan Text Embeddings V2**, 512-dim,
``normalize=true`` (ADR-13.2 — unit vectors make the Euclidean vector index equivalent to
cosine).

Failure mapping is what gives never-lose-input its teeth (transaction-boundaries doc §2, and
the failure-modes table's "Bedrock throttles mid-turn" row): any Bedrock/parse failure becomes
``ExtractionError``/``EmbeddingError``, which the ingestion pipeline turns into a note or a
NULL-embedding row — never a lost turn. That includes the *silent* failure mode: a model
returning zero events. The forced tool carries a required ``no_loggable_content`` flag so an
empty result is an assertion the turn was contentless; without it we raise, honoring the
empty-result contract in ``engine/model.py``.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from engine.model import (
    EmbeddingError,
    ExtractedEvent,
    ExtractionError,
    NarrationError,
    PlanningError,
    ToolCall,
    ToolSpec,
)
from engine.types import MEMORY_TYPE_REGISTRY

if TYPE_CHECKING:
    from engine.assembly import ContextBlock

logger = logging.getLogger(__name__)

_EXTRACT_TOOL = "record_events"

_PLAN_SYSTEM = (
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

_NARRATE_SYSTEM = (
    "You are a fitness memory companion. Answer the user's question using ONLY the evidence "
    "provided — never invent numbers, dates, or facts. Every factual claim must carry a "
    "citation marker in square brackets containing the exact memory id it rests on, e.g. "
    "'you averaged 137g protein [a1b2c3d4-...]'. Cite only ids that appear in the evidence. "
    "If the evidence is empty, say plainly that there is no logged data for that question "
    "yet — do not guess. Be concise and factual."
)

_SYSTEM_PROMPT = (
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


def _tool_config() -> dict:
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": _EXTRACT_TOOL,
                    "description": "Record the typed health events extracted from the message.",
                    "inputSchema": {
                        "json": {
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
                                            # Short NL rendering, embedded for semantic recall.
                                            "summary": {"type": "string"},
                                            # Per-type fields (e.g. meal: items, nutrition).
                                            "payload": {"type": "object"},
                                        },
                                        "required": ["type", "confidence", "summary", "payload"],
                                    },
                                },
                                # The empty-result contract (engine/model.py): when `events`
                                # is empty this flag is the difference between "nothing to
                                # log" and "I could not parse this" — required so the model
                                # must decide rather than default.
                                "no_loggable_content": {
                                    "type": "boolean",
                                    "description": (
                                        "True only if the message contains nothing loggable "
                                        "(small talk, a question). False if it does describe "
                                        "something loggable that you could not extract."
                                    ),
                                },
                            },
                            "required": ["events", "no_loggable_content"],
                        }
                    },
                }
            }
        ],
        "toolChoice": {"tool": {"name": _EXTRACT_TOOL}},
    }


class BedrockProvider:
    def __init__(
        self,
        *,
        region: str,
        extraction_model_id: str,
        embedding_model_id: str,
        embed_dims: int = 512,
        default_tz: str = "UTC",
    ) -> None:
        self._client = boto3.client("bedrock-runtime", region_name=region)
        self._extraction_model_id = extraction_model_id
        self._embedding_model_id = embedding_model_id
        self._embed_dims = embed_dims
        self._default_tz = default_tz

    # ── extraction ────────────────────────────────────────────────────────────────────
    def extract_events(self, text: str, *, now: datetime, tz: str) -> list[ExtractedEvent]:
        prompt = (
            f"Current time: {now.isoformat()}\nCurrent timezone: {tz}\n\n"
            f"User message:\n{text}"
        )
        try:
            response = self._client.converse(
                modelId=self._extraction_model_id,
                system=[{"text": _SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                toolConfig=_tool_config(),
                inferenceConfig={"maxTokens": 2048, "temperature": 0.0},
            )
        except (ClientError, BotoCoreError) as exc:  # throttling, timeouts, etc.
            raise ExtractionError(f"bedrock converse failed: {exc}") from exc

        tool_input = self._tool_input(response)
        raw_events = tool_input.get("events") or []
        if not raw_events:
            # Empty is only a legitimate no-op when the model affirms the turn was
            # contentless. Anything else — flag false, flag missing, flag non-boolean —
            # is an unparsed turn, and raising is what routes it to the note fallback
            # instead of dropping it (engine/model.py empty-result contract).
            if tool_input.get("no_loggable_content") is True:
                return []
            raise ExtractionError(
                "model returned no events without affirming the turn was contentless "
                f"(no_loggable_content={tool_input.get('no_loggable_content')!r})"
            )
        return [self._to_event(e, now=now, tz=tz) for e in raw_events]

    @staticmethod
    def _tool_input(response: dict) -> dict:
        """Pull the forced tool call's input object out of a Converse response."""
        try:
            content = response["output"]["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ExtractionError(f"unexpected converse response shape: {exc}") from exc
        for block in content:
            if "toolUse" in block and block["toolUse"].get("name") == _EXTRACT_TOOL:
                return block["toolUse"].get("input", {})
        # Model answered without calling the forced tool — treat as an unparseable turn.
        raise ExtractionError("model did not return a record_events tool call")

    def _to_event(self, e: dict, *, now: datetime, tz: str) -> ExtractedEvent:
        event_time, resolved_tz, confidence = self._resolve_time(
            e.get("event_time"), e.get("tz"), float(e.get("confidence", 0.5)), now=now, tz=tz
        )
        return ExtractedEvent(
            type=str(e["type"]),
            event_time=event_time,
            tz=resolved_tz,
            confidence=confidence,
            summary=str(e.get("summary", "")),
            payload=e.get("payload") or {},
        )

    def _resolve_time(
        self, raw_time, raw_tz, confidence: float, *, now: datetime, tz: str
    ) -> tuple[datetime, str, float]:
        """Parse the model's event_time/tz; fall back to now/default tz with lowered
        confidence when the model couldn't infer them (bi-temporal design, 04)."""
        resolved_tz = raw_tz or tz or self._default_tz
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

    # ── embeddings ────────────────────────────────────────────────────────────────────
    def embed(self, texts: list[str]) -> list[list[float]]:
        """One normalized 512-dim vector per text. All-or-nothing: any failure raises
        EmbeddingError (transaction-boundaries doc §6). Titan V2 is single-input, so we
        loop — batches here are small (a turn's summaries, or a bounded backfill page)."""
        vectors: list[list[float]] = []
        for text in texts:
            vectors.append(self._embed_one(text))
        return vectors

    def _embed_one(self, text: str) -> list[float]:
        try:
            response = self._client.invoke_model(
                modelId=self._embedding_model_id,
                accept="application/json",
                contentType="application/json",
                body=json.dumps(
                    {"inputText": text, "dimensions": self._embed_dims, "normalize": True}
                ),
            )
            body = json.loads(response["body"].read())
            vector = body["embedding"]
        except (ClientError, BotoCoreError, KeyError, ValueError) as exc:
            raise EmbeddingError(f"titan embedding failed: {exc}") from exc

        norm = math.sqrt(sum(x * x for x in vector))
        if abs(norm - 1.0) > 0.05:  # defensive: the L2≡cosine invariant assumes unit vectors
            logger.warning("titan returned non-unit vector (norm=%.4f); check normalize flag", norm)
        return vector

    # ── planning (the only NL-understanding step, 05 query-planning boundary) ────────────
    def plan(
        self, question: str, tools: list[ToolSpec], *, now: datetime, tz: str
    ) -> list[ToolCall]:
        """Select tools + fill typed slots via Converse tool-use. ``toolChoice=auto`` lets
        the model return zero calls — the empty-plan 'no memory operation' assertion
        (engine/model.py). Any failure or malformed output raises PlanningError."""
        prompt = (
            f"Current time: {now.isoformat()}\nCurrent timezone: {tz}\n\n"
            f"User turn:\n{question}"
        )
        try:
            response = self._client.converse(
                modelId=self._extraction_model_id,
                system=[{"text": _PLAN_SYSTEM}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                toolConfig={"tools": _plan_tools(tools), "toolChoice": {"auto": {}}},
                inferenceConfig={"maxTokens": 2048, "temperature": 0.0},
            )
        except (ClientError, BotoCoreError) as exc:
            raise PlanningError(f"bedrock converse (plan) failed: {exc}") from exc

        return _collect_tool_calls(response)

    # ── narration (prose only, cited; 05 answer contract) ───────────────────────────────
    def narrate(self, question: str, context: ContextBlock) -> str:
        """Turn assembled evidence into cited prose. Renders the ContextBlock to a compact
        evidence prompt (provider-owned, mirroring extract_events); the model produces
        natural language only. Any failure or empty output raises NarrationError."""
        prompt = _render_context(question, context)
        try:
            response = self._client.converse(
                modelId=self._extraction_model_id,
                system=[{"text": _NARRATE_SYSTEM}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 1024, "temperature": 0.2},
            )
        except (ClientError, BotoCoreError) as exc:
            raise NarrationError(f"bedrock converse (narrate) failed: {exc}") from exc

        text = _text_of(response)
        if not text.strip():
            raise NarrationError("narration returned empty text")
        return text


# ── Converse plumbing for plan/narrate (module-level, provider-agnostic shapes) ─────────
def _plan_tools(tools: list[ToolSpec]) -> list[dict]:
    """Render provider-agnostic ToolSpecs into Converse toolConfig entries."""
    return [
        {
            "toolSpec": {
                "name": t.name,
                "description": t.description,
                "inputSchema": {"json": t.input_schema},
            }
        }
        for t in tools
    ]


def _collect_tool_calls(response: dict) -> list[ToolCall]:
    """Pull every toolUse block out of a Converse response, in order (parallel tool use →
    several calls = mixed retrieval). No toolUse blocks → empty plan (the model chose to
    call nothing). Text blocks are ignored — planning wants tools, not prose."""
    try:
        content = response["output"]["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise PlanningError(f"unexpected converse response shape: {exc}") from exc
    calls: list[ToolCall] = []
    for block in content:
        use = block.get("toolUse") if isinstance(block, dict) else None
        if use is None:
            continue
        name = use.get("name")
        if not name:
            raise PlanningError(f"toolUse block without a name: {use!r}")
        calls.append(ToolCall(tool=str(name), arguments=use.get("input") or {}))
    return calls


def _text_of(response: dict) -> str:
    """Concatenate the text blocks of a Converse response (narration output)."""
    try:
        content = response["output"]["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise NarrationError(f"unexpected converse response shape: {exc}") from exc
    return "".join(b["text"] for b in content if isinstance(b, dict) and "text" in b)


def _render_context(question: str, context: ContextBlock) -> str:
    """Render an assembled ContextBlock into a compact evidence prompt (decision D-5:
    structured context → text at the narrate boundary). Memory ids are shown inline so the
    model can cite them; payloads are never dumped (the summaries/values carry the meaning).
    """
    lines: list[str] = [f"Question: {question}", ""]

    if context.aggregates:
        lines.append("Computed aggregates:")
        for agg in context.aggregates:
            s = agg.spec
            header = f"- {s.agg}({s.metric})"
            header += f" by {s.group_by}" if s.group_by != "none" else ""
            header += f" from {s.start.date()} to {s.end.date()}:"
            lines.append(header)
            if not agg.buckets:
                lines.append("    (no matching data in range)")
            for b in agg.buckets:
                label = b.bucket or "total"
                cites = " ".join(f"[{i}]" for i in b.evidence_ids)
                lines.append(f"    {label}: {b.value:g} (n={b.n}) — {cites}")
        lines.append("")

    if context.counts:
        lines.append("Event counts:")
        for c in context.counts:
            cites = " ".join(f"[{i}]" for i in c.evidence_ids)
            lines.append(
                f"- {c.spec.type} from {c.spec.start.date()} to {c.spec.end.date()}: "
                f"{c.n} — {cites}"
            )
        lines.append("")

    if context.memories:
        lines.append("Relevant memories (most relevant first):")
        for m in context.memories:
            lines.append(
                f"- [{m.id}] {m.type} @ {m.event_time.isoformat()} "
                f"(confidence {m.confidence:g}, {m.provenance}): {m.summary or ''}"
            )
        if context.omitted_count:
            lines.append(f"  (+{context.omitted_count} lower-ranked memories omitted)")
        lines.append("")

    if context.is_empty:
        lines.append("No matching memories were found for this question.")

    return "\n".join(lines).rstrip()
