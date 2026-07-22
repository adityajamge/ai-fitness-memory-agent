"""BedrockProvider's half of the empty-result contract (D1, engine/model.py).

The provider is the only component that can tell "nothing to log" from "I could not parse
this" — the engine sees an empty list either way. These tests pin that decision down. No
network and no database: the boto3 client is replaced with a stub returning canned Converse
responses, so they run everywhere, including a machine with no AWS credentials.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from botocore.exceptions import ClientError

from agent.providers import bedrock as bedrock_module
from agent.providers.bedrock import BedrockProvider
from engine.model import ExtractionError

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
TZ = "Asia/Kolkata"

_MEAL_EVENT = {
    "type": "meal",
    "event_time": "2026-07-21T13:00:00+00:00",
    "tz": TZ,
    "confidence": 0.9,
    "summary": "lunch: 250g curd, 3 eggs",
    "payload": {"meal_type": "lunch"},
}


class _StubClient:
    """Minimal stand-in for the bedrock-runtime client: returns a canned tool_input, or
    raises whatever it was given."""

    def __init__(self, tool_input: dict | None = None, *, raises: Exception | None = None,
                 content: list | None = None) -> None:
        self._tool_input = tool_input
        self._raises = raises
        self._content = content
        self.converse_calls = 0

    def converse(self, **kwargs) -> dict:
        self.converse_calls += 1
        if self._raises is not None:
            raise self._raises
        content = (
            self._content
            if self._content is not None
            else [{"toolUse": {"name": "record_events", "input": self._tool_input}}]
        )
        return {"output": {"message": {"content": content}}}


@pytest.fixture()
def provider(monkeypatch):
    """Build a provider whose client is whatever the test installs via `install`."""
    holder: dict = {}

    def _install(client: _StubClient) -> BedrockProvider:
        monkeypatch.setattr(bedrock_module.boto3, "client", lambda *a, **k: client)
        built = BedrockProvider(
            region="us-east-1",
            extraction_model_id="test-extract",
            embedding_model_id="test-embed",
            default_tz=TZ,
        )
        holder["client"] = client
        return built

    return _install


def test_events_are_parsed(provider):
    svc = provider(_StubClient({"events": [_MEAL_EVENT], "no_loggable_content": False}))
    events = svc.extract_events("250g curd, 3 eggs", now=NOW, tz=TZ)

    assert len(events) == 1
    assert events[0].type == "meal"
    assert events[0].confidence == pytest.approx(0.9)
    assert events[0].payload == {"meal_type": "lunch"}


def test_contentless_turn_returns_empty_list(provider):
    """'thanks!' — the model affirms there is nothing to log, so the engine no-ops.
    This is the case that must NOT become a note."""
    svc = provider(_StubClient({"events": [], "no_loggable_content": True}))
    assert svc.extract_events("thanks!", now=NOW, tz=TZ) == []


def test_empty_events_with_flag_false_raises(provider):
    """The message had loggable content the model could not type -> ExtractionError, which
    the ingestion pipeline turns into a note. This is the D1 hole, closed."""
    svc = provider(_StubClient({"events": [], "no_loggable_content": False}))
    with pytest.raises(ExtractionError):
        svc.extract_events("ergh half a plate of something", now=NOW, tz=TZ)


def test_empty_events_with_missing_flag_raises(provider):
    """A non-compliant model that omits the flag must fail safe (note), not silently drop."""
    svc = provider(_StubClient({"events": []}))
    with pytest.raises(ExtractionError):
        svc.extract_events("had some curd", now=NOW, tz=TZ)


def test_empty_events_with_non_boolean_flag_raises(provider):
    """Only a literal True is an affirmation — a truthy string is a malformed response."""
    svc = provider(_StubClient({"events": [], "no_loggable_content": "yes"}))
    with pytest.raises(ExtractionError):
        svc.extract_events("had some curd", now=NOW, tz=TZ)


def test_events_win_over_a_contradictory_flag(provider):
    """If the model returns events AND claims contentlessness, the events are the truth —
    never discard extracted content over a stray flag."""
    svc = provider(_StubClient({"events": [_MEAL_EVENT], "no_loggable_content": True}))
    assert len(svc.extract_events("250g curd", now=NOW, tz=TZ)) == 1


def test_missing_tool_call_raises(provider):
    """Model answered in prose instead of calling the forced tool (pre-existing behavior)."""
    svc = provider(_StubClient(content=[{"text": "sure, noted!"}]))
    with pytest.raises(ExtractionError):
        svc.extract_events("250g curd", now=NOW, tz=TZ)


def test_bedrock_client_error_raises_extraction_error(provider):
    """Throttling and friends map to ExtractionError -> note fallback (failure-modes table)."""
    throttle = ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")
    svc = provider(_StubClient(raises=throttle))
    with pytest.raises(ExtractionError):
        svc.extract_events("250g curd", now=NOW, tz=TZ)


def test_tool_schema_requires_the_flag():
    """The contract is enforced at the schema level: the model cannot omit its decision."""
    schema = bedrock_module._tool_config()["tools"][0]["toolSpec"]["inputSchema"]["json"]
    assert "no_loggable_content" in schema["properties"]
    assert set(schema["required"]) == {"events", "no_loggable_content"}
