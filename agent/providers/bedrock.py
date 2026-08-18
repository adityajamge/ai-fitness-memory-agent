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
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from agent.providers._prompts import (
    EXTRACT_TOOL as _EXTRACT_TOOL,
)
from agent.providers._prompts import (
    NARRATE_SYSTEM as _NARRATE_SYSTEM,
)
from agent.providers._prompts import (
    NUTRITION_PROMPT_VERSION as _NUTRITION_PROMPT_VERSION,
)
from agent.providers._prompts import (
    NUTRITION_SYSTEM as _NUTRITION_SYSTEM,
)
from agent.providers._prompts import (
    NUTRITION_TOOL as _NUTRITION_TOOL,
)
from agent.providers._prompts import (
    PLAN_SYSTEM as _PLAN_SYSTEM,
)
from agent.providers._prompts import (
    SYSTEM_PROMPT as _SYSTEM_PROMPT,
)
from agent.providers._prompts import (
    VISION_SYSTEM as _VISION_SYSTEM,
)
from agent.providers._prompts import (
    extract_tool_schema,
    history_messages,
    nutrition_items_prompt,
    nutrition_tool_schema,
    parse_extracted_events,
    parse_nutrition_components,
    render_context,
    vision_prompt_text,
)
from engine.model import (
    EmbeddingError,
    ExtractedEvent,
    ExtractionError,
    HistoryTurn,
    NarrationError,
    NutritionError,
    PlanningError,
    ToolCall,
    ToolSpec,
    VisionError,
)

if TYPE_CHECKING:
    from engine.assembly import ContextBlock

logger = logging.getLogger(__name__)


def _forced_tool_config(tool: dict) -> dict:
    """A single forced tool, in Bedrock Converse's nesting (``toolSpec`` +
    ``inputSchema.json``). The schemas themselves are provider-agnostic — see _prompts.py."""
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "inputSchema": {"json": tool["input_schema"]},
                }
            }
        ],
        "toolChoice": {"tool": {"name": tool["name"]}},
    }


def _tool_config() -> dict:
    return _forced_tool_config(extract_tool_schema())


_BEDROCK_IMAGE_FORMATS = {"image/jpeg": "jpeg", "image/png": "png", "image/webp": "webp"}


def _bedrock_image_format(mime_type: str) -> str:
    """Converse's image block format, from the upload's content-type. The API layer
    (``api/routers/chat.py``) already restricts uploads to this same allowlist before a
    request ever reaches here; this is the belt to that suspenders."""
    try:
        return _BEDROCK_IMAGE_FORMATS[mime_type]
    except KeyError:
        raise ValueError(f"unsupported image type for vision: {mime_type!r}") from None


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

    # Recorded on every nutrition estimate this provider produces, so a stored number can be
    # attributed to the exact model and instruction that generated it (engine/nutrition.py).
    @property
    def nutrition_model_id(self) -> str:
        return self._extraction_model_id

    @property
    def nutrition_prompt_version(self) -> str:
        return _NUTRITION_PROMPT_VERSION

    # ── extraction ────────────────────────────────────────────────────────────────────
    def extract_events(self, text: str, *, now: datetime, tz: str) -> list[ExtractedEvent]:
        prompt = f"Current time: {now.isoformat()}\nCurrent timezone: {tz}\n\nUser message:\n{text}"
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

        # Empty-result contract enforcement is shared with every other provider (D1).
        return parse_extracted_events(
            self._tool_input(response), now=now, tz=tz, default_tz=self._default_tz
        )

    @staticmethod
    def _tool_input(response: dict, tool: str = _EXTRACT_TOOL, error=ExtractionError) -> dict:
        """Pull a forced tool call's input object out of a Converse response."""
        try:
            content = response["output"]["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise error(f"unexpected converse response shape: {exc}") from exc
        for block in content:
            if "toolUse" in block and block["toolUse"].get("name") == tool:
                return block["toolUse"].get("input", {})
        # Model answered without calling the forced tool — treat as an unparseable turn.
        raise error(f"model did not return a {tool} tool call")

    # ── vision extraction (M7: a photo instead of text) ────────────────────────────────
    def extract_from_image(
        self, image_bytes: bytes, mime_type: str, *, now: datetime, tz: str, caption: str = ""
    ) -> list[ExtractedEvent]:
        """Same forced-tool contract as ``extract_events``, with an image content block
        alongside the caption text. See ``engine.model.ModelProvider.extract_from_image``
        for the qty_g-only-when-stated rule this relies on ``_VISION_SYSTEM`` to enforce."""
        try:
            image_format = _bedrock_image_format(mime_type)
        except ValueError as exc:
            raise VisionError(str(exc)) from exc

        prompt = vision_prompt_text(caption, now=now, tz=tz)
        try:
            response = self._client.converse(
                modelId=self._extraction_model_id,
                system=[{"text": _VISION_SYSTEM}],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"image": {"format": image_format, "source": {"bytes": image_bytes}}},
                            {"text": prompt},
                        ],
                    }
                ],
                toolConfig=_tool_config(),
                inferenceConfig={"maxTokens": 2048, "temperature": 0.0},
            )
        except (ClientError, BotoCoreError) as exc:  # throttling, timeouts, unsupported input
            raise VisionError(f"bedrock converse (vision) failed: {exc}") from exc

        return parse_extracted_events(
            self._tool_input(response, _EXTRACT_TOOL, VisionError),
            now=now,
            tz=tz,
            default_tz=self._default_tz,
        )

    # ── nutrition estimation (the second, separate model call) ────────────────────────
    def estimate_nutrition(self, items: list[dict], *, context: str = "") -> list[dict]:
        """Estimate per-item macros from the model's own food knowledge (engine/model.py).

        A distinct call from ``extract_events`` with its own system prompt, because the two
        need opposite instructions — one must never invent, the other is asked to infer. The
        output is raw and untrusted here; ``engine/nutrition.py`` bounds-checks it and computes
        every total.
        """
        if not items:
            raise NutritionError("no items to estimate")
        try:
            response = self._client.converse(
                modelId=self._extraction_model_id,
                system=[{"text": _NUTRITION_SYSTEM}],
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": nutrition_items_prompt(items, context)}],
                    }
                ],
                toolConfig=_forced_tool_config(nutrition_tool_schema()),
                inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
            )
        except (ClientError, BotoCoreError) as exc:
            raise NutritionError(f"bedrock converse (nutrition) failed: {exc}") from exc

        return parse_nutrition_components(
            self._tool_input(response, _NUTRITION_TOOL, NutritionError)
        )

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
        self,
        question: str,
        tools: list[ToolSpec],
        *,
        now: datetime,
        tz: str,
        history: Sequence[HistoryTurn] = (),
    ) -> list[ToolCall]:
        """Select tools + fill typed slots via Converse tool-use. ``toolChoice=auto`` lets
        the model return zero calls — the empty-plan 'no memory operation' assertion
        (engine/model.py). Any failure or malformed output raises PlanningError.

        Short-term history precedes the current turn as real Converse messages; the current
        turn is last and is the only one stamped with ``Current time`` (ADR-14.16)."""
        prompt = (
            f"Current time: {now.isoformat()}\nCurrent timezone: {tz}\n\nUser turn:\n{question}"
        )
        try:
            response = self._client.converse(
                modelId=self._extraction_model_id,
                system=[{"text": _PLAN_SYSTEM}],
                messages=[
                    *_converse_history(history),
                    {"role": "user", "content": [{"text": prompt}]},
                ],
                toolConfig={"tools": _plan_tools(tools), "toolChoice": {"auto": {}}},
                inferenceConfig={"maxTokens": 2048, "temperature": 0.0},
            )
        except (ClientError, BotoCoreError) as exc:
            raise PlanningError(f"bedrock converse (plan) failed: {exc}") from exc

        return _collect_tool_calls(response)

    # ── narration (prose only, cited; 05 answer contract) ───────────────────────────────
    def narrate(
        self, question: str, context: ContextBlock, *, history: Sequence[HistoryTurn] = ()
    ) -> str:
        """Turn assembled evidence into cited prose. Renders the ContextBlock to a compact
        evidence prompt (provider-owned, mirroring extract_events); the model produces
        natural language only. Any failure or empty output raises NarrationError.

        History travels as prior messages, never inside ``render_context`` — evidence and
        conversation stay separate channels so only the former can be cited."""
        prompt = render_context(question, context)
        try:
            response = self._client.converse(
                modelId=self._extraction_model_id,
                system=[{"text": _NARRATE_SYSTEM}],
                messages=[
                    *_converse_history(history),
                    {"role": "user", "content": [{"text": prompt}]},
                ],
                inferenceConfig={"maxTokens": 1024, "temperature": 0.2},
            )
        except (ClientError, BotoCoreError) as exc:
            raise NarrationError(f"bedrock converse (narrate) failed: {exc}") from exc

        text = _text_of(response)
        if not text.strip():
            raise NarrationError("narration returned empty text")
        return text


# ── Converse plumbing for plan/narrate (module-level, provider-agnostic shapes) ─────────
def _converse_history(history: Sequence[HistoryTurn]) -> list[dict]:
    """Short-term memory in Converse's message shape.

    The dated rendering itself lives in ``_prompts.history_messages`` — shared with the Claude
    API provider, because *what* the model is told is the contract and only *how* it is shipped
    is this file's business (the same split every other prompt here follows). This wraps each
    rendered message in Converse's ``content: [{"text": ...}]`` envelope and nothing else.
    """
    return [
        {"role": message["role"], "content": [{"text": message["content"]}]}
        for message in history_messages(history)
    ]


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
