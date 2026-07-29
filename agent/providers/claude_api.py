"""Claude Developer Platform implementation of engine.model.ModelProvider —
**a development adapter, not the production provider.**

Why this exists: ADR-13 fixes Bedrock as the architecture's default (Titan V2 embeddings,
Converse extraction). This adapter lets the app run end-to-end on a platform.claude.com API
key when Bedrock access isn't available, exercising the *same* engine, graph, and API with
zero edits below the provider boundary. That round-trip is the ADR-1 acceptance check —
"switching provider must be a config change, zero memory-layer edits" — actually performed.

**It cannot embed, and that is deliberate.** The Claude API has no embeddings endpoint, and
Titan V2 (512-dim, normalized — ADR-13.2) is a Bedrock model. Rather than inventing vectors,
``embed`` raises ``EmbeddingError``, which the Phase 2 write path already handles: memories
are stored with NULL embeddings and a later ``cli backfill`` against Bedrock fills them in
correctly (T15). Fabricating placeholder vectors would be worse than useless — the rows
would look embedded, never qualify for backfill, and poison semantic recall permanently.
The cost is that ``recall_memories`` cannot be exercised on this provider; every other tool
works, and the turn degrades honestly (agent/graph.py records the failure and answers with
what it *could* retrieve).

Two Claude API specifics this file depends on:
  * ``temperature`` is **removed** on current models — sending it is a 400. Determinism
    comes from the prompt and the forced tool schema, not a sampling knob.
  * Thinking is on by default and shares the ``max_tokens`` budget with the response, so
    budgets here are generous.
  * ``output_config.effort`` is **model-dependent** — the current line accepts it, the
    4.5-era small models reject it with a 400 — so it is sent conditionally, never blindly.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

import anthropic

from agent.providers._prompts import (
    EXTRACT_TOOL,
    NARRATE_SYSTEM,
    PLAN_SYSTEM,
    SYSTEM_PROMPT,
    extract_tool_schema,
    parse_extracted_events,
    render_context,
)
from engine.model import (
    EmbeddingError,
    ExtractedEvent,
    ExtractionError,
    NarrationError,
    PlanningError,
    ToolCall,
    ToolSpec,
)

if TYPE_CHECKING:
    from engine.assembly import ContextBlock

logger = logging.getLogger(__name__)

_MAX_TOKENS_STRUCTURED = 8192  # thinking + tool call
_MAX_TOKENS_PROSE = 4096

# `output_config.effort` is a newer-generation capability: the current Opus / Sonnet / Fable
# line accepts it, while the 4.5-era small models reject it outright with
# `400 invalid_request_error: This model does not support the effort parameter`.
# Kept as a narrow *denylist* rather than an allowlist so an unfamiliar model still gets
# effort (and, if it is unsupported, a clear API error) instead of silently losing it.
_EFFORT_UNSUPPORTED_PREFIXES = ("claude-haiku-4-5", "claude-sonnet-4-5", "claude-3-")

# Escape hatch: any of these as CLAUDE_API_EFFORT omits the parameter entirely.
_EFFORT_DISABLED = frozenset({"", "none", "off", "default"})


class ClaudeAPIProvider:
    """ModelProvider over the Claude Developer Platform (development use — see module doc)."""

    def __init__(
        self,
        *,
        model_id: str,
        effort: str = "low",
        default_tz: str = "UTC",
        client: Any | None = None,
    ) -> None:
        # The SDK resolves credentials itself (ANTHROPIC_API_KEY, then an `ant auth login`
        # profile); never hardcode a key here.
        self._client = client or anthropic.Anthropic()
        self._model_id = model_id
        self._effort = effort
        self._default_tz = default_tz
        if not self._effort_kwargs():
            logger.info(
                "effort not sent for model %s (unsupported or disabled); using its default",
                model_id,
            )

    def _effort_kwargs(self) -> dict:
        """``output_config`` for a request, or ``{}`` when effort must not be sent.

        Effort is model-dependent — sending it to a model that does not accept it is a hard
        400, not a silently ignored field — so it is omitted for known-unsupported models and
        whenever the operator disables it via ``CLAUDE_API_EFFORT``.
        """
        if (self._effort or "").strip().lower() in _EFFORT_DISABLED:
            return {}
        if self._model_id.startswith(_EFFORT_UNSUPPORTED_PREFIXES):
            return {}
        return {"output_config": {"effort": self._effort}}

    # ── extraction ────────────────────────────────────────────────────────────────────
    def extract_events(self, text: str, *, now: datetime, tz: str) -> list[ExtractedEvent]:
        """Same forced-tool contract as the Bedrock provider, including the
        ``no_loggable_content`` flag that keeps never-lose-input honest (D1)."""
        prompt = f"Current time: {now.isoformat()}\nCurrent timezone: {tz}\n\nUser message:\n{text}"
        try:
            response = self._client.messages.create(
                model=self._model_id,
                max_tokens=_MAX_TOKENS_STRUCTURED,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                tools=[extract_tool_schema()],
                tool_choice={"type": "tool", "name": EXTRACT_TOOL},
                **self._effort_kwargs(),
            )
        except anthropic.APIError as exc:
            raise ExtractionError(f"claude api extraction failed: {exc}") from exc

        _reject_refusal(response, ExtractionError)
        tool_input = _tool_input(response, EXTRACT_TOOL, ExtractionError)
        return parse_extracted_events(tool_input, now=now, tz=tz, default_tz=self._default_tz)

    # ── embeddings (unsupported — see module docstring) ───────────────────────────────
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Always raises: the Claude API has no embeddings endpoint, and inventing vectors
        would permanently poison semantic recall. The engine treats this as a nullable
        enrichment — rows land with NULL embeddings and ``cli backfill`` fills them once a
        real embedding provider (Bedrock/Titan) is configured (T15)."""
        raise EmbeddingError(
            "the Claude API provider cannot embed (no embeddings endpoint); memories are "
            "stored without embeddings and can be backfilled later with `python -m cli.backfill` "
            "against Bedrock"
        )

    # ── planning ──────────────────────────────────────────────────────────────────────
    def plan(
        self, question: str, tools: list[ToolSpec], *, now: datetime, tz: str
    ) -> list[ToolCall]:
        """Tool selection with ``tool_choice: auto`` so an empty plan stays expressible
        (M4-2: calling nothing is a deliberate outcome, not a failure)."""
        prompt = (
            f"Current time: {now.isoformat()}\nCurrent timezone: {tz}\n\nUser turn:\n{question}"
        )
        try:
            response = self._client.messages.create(
                model=self._model_id,
                max_tokens=_MAX_TOKENS_STRUCTURED,
                system=PLAN_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                tools=[
                    {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                    for t in tools
                ],
                tool_choice={"type": "auto"},
                **self._effort_kwargs(),
            )
        except anthropic.APIError as exc:
            raise PlanningError(f"claude api planning failed: {exc}") from exc

        _reject_refusal(response, PlanningError)
        calls: list[ToolCall] = []
        for block in _content(response, PlanningError):
            if getattr(block, "type", None) != "tool_use":
                continue
            if not getattr(block, "name", None):
                raise PlanningError(f"tool_use block without a name: {block!r}")
            calls.append(ToolCall(tool=str(block.name), arguments=dict(block.input or {})))
        return calls

    # ── narration ─────────────────────────────────────────────────────────────────────
    def narrate(self, question: str, context: ContextBlock) -> str:
        try:
            response = self._client.messages.create(
                model=self._model_id,
                max_tokens=_MAX_TOKENS_PROSE,
                system=NARRATE_SYSTEM,
                messages=[{"role": "user", "content": render_context(question, context)}],
                **self._effort_kwargs(),
            )
        except anthropic.APIError as exc:
            raise NarrationError(f"claude api narration failed: {exc}") from exc

        _reject_refusal(response, NarrationError)
        text = "".join(
            block.text
            for block in _content(response, NarrationError)
            if getattr(block, "type", None) == "text"
        )
        if not text.strip():
            raise NarrationError("narration returned empty text")
        return text


# ── response plumbing ──────────────────────────────────────────────────────────────────
def _content(response: Any, error: type[Exception]) -> list:
    content = getattr(response, "content", None)
    if content is None:
        raise error(f"unexpected response shape: {response!r}")
    return list(content)


def _reject_refusal(response: Any, error: type[Exception]) -> None:
    """Current models can decline a request with HTTP 200 + ``stop_reason='refusal'``.
    Surface it as this call's typed error so the pipeline degrades the way it does for any
    other model failure (a refused ingest becomes a note; a refused turn is retriable)."""
    if getattr(response, "stop_reason", None) == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise error(f"model refused the request (category={category!r})")


def _tool_input(response: Any, tool_name: str, error: type[Exception]) -> dict:
    for block in _content(response, error):
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return dict(block.input or {})
    raise error(f"model did not return a {tool_name} tool call")
