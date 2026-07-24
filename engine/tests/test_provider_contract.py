"""ModelProvider contract tests for the M4 agent surfaces (plan + narrate), exercised
through the FakeModelProvider.

DB-free and provider-agnostic: these pin the *contract* every provider must honor —
Protocol conformance, the empty-plan positive assertion, typed failures, and citation
pass-through from context to narration. The Bedrock-specific wiring is tested separately
(agent/tests/test_bedrock_planner.py)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from engine.assembly import assemble
from engine.model import (
    ModelProvider,
    NarrationError,
    PlanningError,
    ToolCall,
    ToolSpec,
)
from engine.tests.conftest import FakeModelProvider
from engine.tests.test_assembly import _aggregate, _lookup, _snap  # reuse M3 factories

NOW = datetime(2026, 7, 24, 9, 0, tzinfo=timezone.utc)
TZ = "Asia/Kolkata"
_TOOLS = [ToolSpec(name="aggregate_memories", description="sum/avg over metrics", input_schema={})]


def test_fake_provider_satisfies_the_extended_protocol() -> None:
    # runtime_checkable Protocol now includes plan + narrate — a provider missing either
    # would fail this isinstance check.
    assert isinstance(FakeModelProvider(), ModelProvider)


def test_plan_returns_scripted_tool_calls() -> None:
    calls = [ToolCall(tool="aggregate_memories", arguments={"metric": "protein_g"})]
    provider = FakeModelProvider(plan_calls=calls)
    result = provider.plan("how much protein?", _TOOLS, now=NOW, tz=TZ)
    assert result == calls
    assert provider.plan_invocations == 1
    assert provider.last_tools == _TOOLS  # the tool vocabulary is passed in, not hardcoded


def test_empty_plan_is_a_positive_assertion_not_an_error() -> None:
    provider = FakeModelProvider(plan_calls=[])
    assert provider.plan("thanks!", _TOOLS, now=NOW, tz=TZ) == []  # no memory operation


def test_plan_failure_raises_planning_error() -> None:
    with pytest.raises(PlanningError):
        FakeModelProvider(plan_error=True).plan("q", _TOOLS, now=NOW, tz=TZ)


def test_narrate_cites_every_citable_id_by_default() -> None:
    ids = tuple(uuid4() for _ in range(2))
    mem = _snap(mem_id=uuid4())
    context, _ = assemble("q", [_aggregate(ids), _lookup(mem)])

    answer = FakeModelProvider().narrate("q", context)

    for cid in context.citable_ids():
        assert f"[{cid}]" in answer


def test_narrate_on_empty_context_is_honest() -> None:
    context, _ = assemble("anything?", [])
    answer = FakeModelProvider().narrate("anything?", context)
    assert "no logged data" in answer.lower()


def test_narrate_can_be_scripted_and_can_fail() -> None:
    context, _ = assemble("q", [])
    assert FakeModelProvider(narration="canned").narrate("q", context) == "canned"
    with pytest.raises(NarrationError):
        FakeModelProvider(narrate_error=True).narrate("q", context)
