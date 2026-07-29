"""The Claude API development adapter (agent/providers/claude_api.py).

No network: the SDK client is replaced with a stub returning canned Messages responses, the
same approach as the Bedrock provider tests. These pin the adapter's half of the provider
contract — the shared extraction/planning/narration rules, the deliberate no-embeddings
behavior, refusal handling, and the API specifics that would otherwise 400 (no `temperature`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import anthropic
import httpx
import pytest

from agent.providers import BEDROCK, CLAUDE_API, build_default_provider
from agent.providers.claude_api import ClaudeAPIProvider
from engine.assembly import assemble
from engine.config import Settings
from engine.model import (
    EmbeddingError,
    ExtractionError,
    ModelProvider,
    NarrationError,
    PlanningError,
    ToolSpec,
)
from engine.tests.test_assembly import _lookup, _snap  # reuse M3 factories

NOW = datetime(2026, 7, 24, 9, 0, tzinfo=timezone.utc)
TZ = "Asia/Kolkata"
_TOOLS = [ToolSpec(name="count_events", description="count events", input_schema={})]

_MEAL_EVENT = {
    "type": "meal",
    "event_time": "2026-07-24T13:00:00+00:00",
    "tz": TZ,
    "confidence": 0.9,
    "summary": "lunch: 250g curd",
    "payload": {"meal_type": "lunch"},
}


def _text(text: str):
    return SimpleNamespace(type="text", text=text)


def _tool_use(name: str, payload: dict):
    return SimpleNamespace(type="tool_use", name=name, input=payload, id="toolu_1")


class _StubMessages:
    def __init__(self, content, *, raises=None, stop_reason="end_turn", stop_details=None):
        self._content = content
        self._raises = raises
        self._stop_reason = stop_reason
        self._stop_details = stop_details
        self.last_kwargs: dict = {}
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self._raises is not None:
            raise self._raises
        return SimpleNamespace(
            content=self._content,
            stop_reason=self._stop_reason,
            stop_details=self._stop_details,
        )


def _provider(messages: _StubMessages) -> ClaudeAPIProvider:
    return ClaudeAPIProvider(
        model_id="claude-opus-5",
        effort="low",
        default_tz=TZ,
        client=SimpleNamespace(messages=messages),
    )


def _api_error() -> anthropic.APIError:
    """A real SDK exception — the adapter catches `anthropic.APIError`, the base every
    status/connection error inherits from."""
    return anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )


# ── the adapter satisfies the Protocol and is config-selected ─────────────────────────
def test_adapter_satisfies_the_model_provider_protocol() -> None:
    assert isinstance(_provider(_StubMessages([])), ModelProvider)


def test_provider_is_selected_by_configuration_only() -> None:
    # ADR-1's acceptance check: swapping providers is a config change, no engine edits.
    built = build_default_provider(Settings(model_provider=CLAUDE_API))
    assert isinstance(built, ClaudeAPIProvider)
    assert Settings().model_provider == BEDROCK  # the architecture's default is unchanged


def test_unknown_provider_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unknown MODEL_PROVIDER"):
        build_default_provider(Settings(model_provider="gpt"))


# ── extraction ────────────────────────────────────────────────────────────────────────
def test_extraction_parses_events() -> None:
    messages = _StubMessages(
        [_tool_use("record_events", {"events": [_MEAL_EVENT], "no_loggable_content": False})]
    )
    events = _provider(messages).extract_events("250g curd", now=NOW, tz=TZ)

    assert len(events) == 1 and events[0].type == "meal"
    # Forced tool use, and NO temperature — sending it is a 400 on current models.
    assert messages.last_kwargs["tool_choice"] == {"type": "tool", "name": "record_events"}
    assert "temperature" not in messages.last_kwargs
    assert "top_p" not in messages.last_kwargs and "top_k" not in messages.last_kwargs


def test_contentless_turn_returns_empty_list() -> None:
    messages = _StubMessages(
        [_tool_use("record_events", {"events": [], "no_loggable_content": True})]
    )
    assert _provider(messages).extract_events("thanks!", now=NOW, tz=TZ) == []


def test_empty_events_without_the_flag_raises() -> None:
    # The D1 contract, enforced identically for every provider (shared _prompts.py).
    messages = _StubMessages(
        [_tool_use("record_events", {"events": [], "no_loggable_content": False})]
    )
    with pytest.raises(ExtractionError):
        _provider(messages).extract_events("ergh half a plate", now=NOW, tz=TZ)


def test_missing_tool_call_raises() -> None:
    with pytest.raises(ExtractionError):
        _provider(_StubMessages([_text("sure, noted!")])).extract_events("x", now=NOW, tz=TZ)


def test_api_errors_map_to_extraction_error() -> None:
    with pytest.raises(ExtractionError):
        _provider(_StubMessages([], raises=_api_error())).extract_events("x", now=NOW, tz=TZ)


# ── effort is model-dependent (regression: caught in release validation) ──────────────
def _effort_provider(messages: _StubMessages, *, model: str, effort: str) -> ClaudeAPIProvider:
    return ClaudeAPIProvider(
        model_id=model, effort=effort, default_tz=TZ, client=SimpleNamespace(messages=messages)
    )


@pytest.mark.parametrize(
    ("model", "effort", "sends_effort"),
    [
        ("claude-opus-5", "low", True),  # current line accepts effort
        ("claude-haiku-4-5-20251001", "low", False),  # 400s if sent — the bug we hit
        ("claude-sonnet-4-5", "low", False),
        ("claude-3-5-haiku-20241022", "low", False),
        ("claude-opus-5", "none", False),  # operator opt-out
        ("claude-opus-5", "", False),
    ],
)
def test_effort_is_sent_only_when_the_model_supports_it(model, effort, sends_effort) -> None:
    """`output_config.effort` is rejected outright by 4.5-era small models — a hard 400, not
    an ignored field — so it must not be sent unconditionally."""
    messages = _StubMessages(
        [_tool_use("record_events", {"events": [_MEAL_EVENT], "no_loggable_content": False})]
    )
    _effort_provider(messages, model=model, effort=effort).extract_events("x", now=NOW, tz=TZ)
    assert ("output_config" in messages.last_kwargs) is sends_effort


def test_effort_decision_applies_to_every_surface() -> None:
    """extract / plan / narrate must agree — one of them still sending effort would 400."""
    for call in ("extract", "plan", "narrate"):
        messages = _StubMessages(
            [
                _tool_use("record_events", {"events": [_MEAL_EVENT], "no_loggable_content": False})
                if call == "extract"
                else _text("ok")
            ]
        )
        provider = _effort_provider(messages, model="claude-haiku-4-5", effort="low")
        if call == "extract":
            provider.extract_events("x", now=NOW, tz=TZ)
        elif call == "plan":
            provider.plan("q", _TOOLS, now=NOW, tz=TZ)
        else:
            context, _ = assemble("q", [])
            provider.narrate("q", context)
        assert "output_config" not in messages.last_kwargs, call


# ── embeddings: unsupported by design ─────────────────────────────────────────────────
def test_embed_raises_rather_than_inventing_vectors() -> None:
    """Fabricated vectors would look embedded, never qualify for backfill, and poison
    recall permanently. Raising keeps the rows NULL and backfill-eligible (T15)."""
    with pytest.raises(EmbeddingError, match="backfill"):
        _provider(_StubMessages([])).embed(["some summary"])


# ── planning ──────────────────────────────────────────────────────────────────────────
def test_plan_returns_typed_calls_in_order() -> None:
    messages = _StubMessages(
        [
            _text("let me look"),
            _tool_use("log_memory", {"text": "ran 5k"}),
            _tool_use("count_events", {"type": "workout"}),
        ]
    )
    calls = _provider(messages).plan("ran 5k — how many?", _TOOLS, now=NOW, tz=TZ)

    assert [c.tool for c in calls] == ["log_memory", "count_events"]
    assert calls[0].arguments == {"text": "ran 5k"}
    # auto, not forced — this is what makes the empty plan expressible (M4-2).
    assert messages.last_kwargs["tool_choice"] == {"type": "auto"}
    assert TZ in messages.last_kwargs["messages"][0]["content"]


def test_empty_plan_is_not_an_error() -> None:
    messages = _StubMessages([_text("you're welcome!")])
    assert _provider(messages).plan("thanks!", _TOOLS, now=NOW, tz=TZ) == []


def test_plan_api_error_maps_to_planning_error() -> None:
    with pytest.raises(PlanningError):
        _provider(_StubMessages([], raises=_api_error())).plan("q", _TOOLS, now=NOW, tz=TZ)


# ── narration ─────────────────────────────────────────────────────────────────────────
def test_narrate_returns_text_and_carries_evidence_ids() -> None:
    mem = _snap(mem_id=uuid4(), summary="lunch: 200g chicken")
    context, _ = assemble("protein?", [_lookup(mem)])
    messages = _StubMessages([_text("You logged chicken.")])

    answer = _provider(messages).narrate("protein?", context)

    assert answer == "You logged chicken."
    assert str(mem.id) in messages.last_kwargs["messages"][0]["content"]
    assert "temperature" not in messages.last_kwargs


def test_narrate_empty_output_raises() -> None:
    context, _ = assemble("q", [])
    with pytest.raises(NarrationError):
        _provider(_StubMessages([_text("  ")])).narrate("q", context)


# ── refusals (current models can decline with HTTP 200) ───────────────────────────────
@pytest.mark.parametrize(
    ("call", "error"),
    [
        ("extract", ExtractionError),
        ("plan", PlanningError),
        ("narrate", NarrationError),
    ],
)
def test_refusal_maps_to_the_calls_typed_error(call, error) -> None:
    messages = _StubMessages(
        [],
        stop_reason="refusal",
        stop_details=SimpleNamespace(type="refusal", category="cyber", explanation=""),
    )
    provider = _provider(messages)
    context, _ = assemble("q", [])
    with pytest.raises(error, match="refused"):
        if call == "extract":
            provider.extract_events("x", now=NOW, tz=TZ)
        elif call == "plan":
            provider.plan("q", _TOOLS, now=NOW, tz=TZ)
        else:
            provider.narrate("q", context)
