"""BedrockProvider's M4 surfaces: plan (Converse tool-use) + narrate (Converse text).

No network, no database: the boto3 client is replaced with a stub returning canned Converse
responses (same approach as test_bedrock_provider.py). These pin the provider's half of the
plan/narrate contracts — tool-call parsing incl. parallel calls and the empty plan, the
NL-understanding→typed-calls boundary, context rendering, and failure mapping to typed
errors."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError

from agent.providers import bedrock as bedrock_module
from agent.providers.bedrock import BedrockProvider
from engine.assembly import assemble
from engine.model import NarrationError, PlanningError, ToolSpec
from engine.tests.test_assembly import _aggregate, _lookup, _snap  # reuse M3 factories

NOW = datetime(2026, 7, 24, 9, 0, tzinfo=timezone.utc)
TZ = "Asia/Kolkata"

_TOOLS = [
    ToolSpec(name="log_memory", description="record a turn", input_schema={"type": "object"}),
    ToolSpec(
        name="aggregate_memories",
        description="sum/avg a metric over time",
        input_schema={"type": "object", "properties": {"metric": {"type": "string"}}},
    ),
]


class _StubClient:
    """Records the last converse kwargs and returns canned content, or raises."""

    def __init__(self, *, content: list | None = None, raises: Exception | None = None) -> None:
        self._content = content or []
        self._raises = raises
        self.last_kwargs: dict = {}
        self.converse_calls = 0

    def converse(self, **kwargs) -> dict:
        self.converse_calls += 1
        self.last_kwargs = kwargs
        if self._raises is not None:
            raise self._raises
        return {"output": {"message": {"content": self._content}}}


@pytest.fixture()
def provider(monkeypatch):
    def _install(client: _StubClient) -> BedrockProvider:
        monkeypatch.setattr(bedrock_module.boto3, "client", lambda *a, **k: client)
        return BedrockProvider(
            region="us-east-1",
            extraction_model_id="test-model",
            embedding_model_id="test-embed",
            default_tz=TZ,
        )

    return _install


def _tooluse(name: str, inp: dict) -> dict:
    return {"toolUse": {"name": name, "input": inp}}


# ── plan ──────────────────────────────────────────────────────────────────────────────
def test_plan_parses_a_single_tool_call(provider):
    client = _StubClient(content=[_tooluse("aggregate_memories", {"metric": "protein_g"})])
    calls = provider(client).plan("how much protein?", _TOOLS, now=NOW, tz=TZ)

    assert len(calls) == 1
    assert calls[0].tool == "aggregate_memories"
    assert calls[0].arguments == {"metric": "protein_g"}


def test_plan_parses_parallel_calls_in_order(provider):
    # A 'both' turn: log_memory AND a retrieval call — mixed retrieval / ingest+query.
    client = _StubClient(
        content=[
            _tooluse("log_memory", {"text": "ran 5k"}),
            _tooluse("aggregate_memories", {"metric": "workout_distance_km"}),
        ]
    )
    calls = provider(client).plan("ran 5k — am I improving?", _TOOLS, now=NOW, tz=TZ)
    assert [c.tool for c in calls] == ["log_memory", "aggregate_memories"]


def test_plan_returns_empty_when_model_calls_no_tools(provider):
    # 'thanks!' — model replies in prose, calls nothing. Empty plan, not an error.
    client = _StubClient(content=[{"text": "you're welcome!"}])
    assert provider(client).plan("thanks!", _TOOLS, now=NOW, tz=TZ) == []


def test_plan_uses_toolchoice_auto_and_passes_the_tool_vocabulary(provider):
    client = _StubClient(content=[])
    provider(client).plan("q", _TOOLS, now=NOW, tz=TZ)

    cfg = client.last_kwargs["toolConfig"]
    assert cfg["toolChoice"] == {"auto": {}}  # auto, not forced — enables the empty plan
    assert [t["toolSpec"]["name"] for t in cfg["tools"]] == ["log_memory", "aggregate_memories"]
    # now/tz are grounded into the prompt for relative-date slots
    assert TZ in client.last_kwargs["messages"][0]["content"][0]["text"]


def test_plan_raises_on_tooluse_without_a_name(provider):
    client = _StubClient(content=[{"toolUse": {"input": {}}}])
    with pytest.raises(PlanningError):
        provider(client).plan("q", _TOOLS, now=NOW, tz=TZ)


def test_plan_maps_client_error_to_planning_error(provider):
    throttle = ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")
    with pytest.raises(PlanningError):
        provider(_StubClient(raises=throttle)).plan("q", _TOOLS, now=NOW, tz=TZ)


def test_plan_raises_on_malformed_response_shape(provider):
    class _Bad(_StubClient):
        def converse(self, **kwargs):
            return {"nope": True}

    with pytest.raises(PlanningError):
        provider(_Bad()).plan("q", _TOOLS, now=NOW, tz=TZ)


# ── narrate ─────────────────────────────────────────────────────────────────────────────
def test_narrate_returns_text(provider):
    client = _StubClient(content=[{"text": "You averaged 137g protein."}])
    context, _ = assemble("protein?", [_lookup(_snap(mem_id=uuid4()))])
    assert provider(client).narrate("protein?", context) == "You averaged 137g protein."


def test_narrate_prompt_carries_evidence_ids_for_citation(provider):
    client = _StubClient(content=[{"text": "ok"}])
    agg_ids = tuple(uuid4() for _ in range(2))
    mem = _snap(mem_id=uuid4(), summary="lunch: 200g chicken")
    context, _ = assemble("protein last month?", [_aggregate(agg_ids), _lookup(mem)])

    provider(client).narrate("protein last month?", context)

    prompt = client.last_kwargs["messages"][0]["content"][0]["text"]
    assert str(mem.id) in prompt
    for cid in agg_ids:
        assert str(cid) in prompt


def test_narrate_renders_empty_context_honestly(provider):
    client = _StubClient(content=[{"text": "No data yet."}])
    context, _ = assemble("anything?", [])
    provider(client).narrate("anything?", context)
    prompt = client.last_kwargs["messages"][0]["content"][0]["text"]
    assert "No matching memories were found" in prompt


def test_narrate_empty_output_raises(provider):
    client = _StubClient(content=[{"text": "   "}])
    context, _ = assemble("q", [_lookup(_snap(mem_id=uuid4()))])
    with pytest.raises(NarrationError):
        provider(client).narrate("q", context)


def test_narrate_maps_client_error_to_narration_error(provider):
    err = ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")
    context, _ = assemble("q", [_lookup(_snap(mem_id=uuid4()))])
    with pytest.raises(NarrationError):
        provider(_StubClient(raises=err)).narrate("q", context)
