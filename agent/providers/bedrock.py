"""Amazon Bedrock implementation of engine.model.ModelProvider (Phase 2: extract + embed).

This is the only place boto3 is imported. Extraction uses the Bedrock **Converse** API with
a *forced tool call*, so the model's entire output is a structured events object (no prose to
parse, no JSON-in-markdown fragility). Embeddings use **Titan Text Embeddings V2**, 512-dim,
``normalize=true`` (ADR-13.2 — unit vectors make the Euclidean vector index equivalent to
cosine).

Failure mapping is what gives never-lose-input its teeth (transaction-boundaries doc §2, and
the failure-modes table's "Bedrock throttles mid-turn" row): any Bedrock/parse failure becomes
``ExtractionError``/``EmbeddingError``, which the ingestion pipeline turns into a note or a
NULL-embedding row — never a lost turn.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from engine.model import EmbeddingError, ExtractedEvent, ExtractionError
from engine.types import MEMORY_TYPE_REGISTRY

logger = logging.getLogger(__name__)

_EXTRACT_TOOL = "record_events"

_SYSTEM_PROMPT = (
    "You extract typed health events from a user's message for a fitness memory app. "
    "Return events ONLY through the record_events tool. Infer event_time and timezone from "
    "relative expressions ('yesterday', 'this morning') using the provided current time and "
    "timezone. NEVER invent facts: if a quantity or time is uncertain, lower confidence and, "
    "for time, leave event_time null (the system will estimate it). If the message contains no "
    "loggable health event, return an empty events list."
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
                                }
                            },
                            "required": ["events"],
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

        raw_events = self._tool_input(response).get("events", [])
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
