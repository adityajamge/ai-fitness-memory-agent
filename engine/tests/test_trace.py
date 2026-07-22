"""EvidenceTrace contract tests (M1/Task 1, ADR-12).

Pure value-object tests — no database, no model. What they pin down: the 8-field trace
contract from 03-memory-engine.md §6, JSON-serializability (the dict Phase 6 persists
verbatim), immutability, and the payload-free snapshot rule.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from engine.trace import (
    EvidenceSnapshot,
    EvidenceTrace,
    InsightRef,
    RankingEntry,
    RetrievalStep,
)

_NOW = datetime(2026, 7, 22, 10, 30, tzinfo=timezone.utc)

# The ADR-12 contract, field-for-field (03-memory-engine.md §6).
_CONTRACT_FIELDS = {
    "trace_id",
    "question",
    "retrieval_steps",
    "evidence",
    "insights",
    "timeline",
    "ranking",
    "assembled_at",
}


def _snapshot(mem_id: UUID | None = None) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        id=mem_id or uuid4(),
        type="meal",
        event_time=_NOW,
        confidence=0.9,
        provenance="live",
        summary="lunch: 200g chicken",
    )


def _full_trace() -> EvidenceTrace:
    mem_id = uuid4()
    return EvidenceTrace(
        trace_id=uuid4(),
        question="how much protein in the last 30 days?",
        retrieval_steps=(
            RetrievalStep(
                family="aggregate",
                sql="SELECT ... WHERE user_id = %(user_id)s",
                params={"user_id": str(uuid4()), "start": _NOW.isoformat()},
                row_count=3,
            ),
        ),
        evidence=(_snapshot(mem_id),),
        insights=(InsightRef(id=uuid4(), hypothesis="protein up", evidence_ids=(mem_id,)),),
        timeline=(_snapshot(),),
        ranking=(
            RankingEntry(
                memory_id=mem_id, relevance=1.0, confidence=0.9, recency=0.8, tier=0.5, score=0.86
            ),
        ),
        assembled_at=_NOW,
    )


def test_trace_carries_the_full_adr12_contract() -> None:
    trace = _full_trace()
    assert {f.name for f in dataclasses.fields(trace)} == _CONTRACT_FIELDS
    assert set(trace.to_json().keys()) == _CONTRACT_FIELDS


def test_trace_json_is_serializable_end_to_end() -> None:
    # The exact property Phase 6 relies on: to_json() goes into a JSONB column verbatim.
    payload = _full_trace().to_json()
    round_tripped = json.loads(json.dumps(payload))
    assert round_tripped == payload


def test_ids_and_datetimes_serialize_as_strings() -> None:
    trace = _full_trace()
    data = trace.to_json()
    assert data["trace_id"] == str(trace.trace_id)
    assert data["assembled_at"] == _NOW.isoformat()
    assert data["evidence"][0]["id"] == str(trace.evidence[0].id)
    assert data["evidence"][0]["event_time"] == _NOW.isoformat()
    assert data["insights"][0]["evidence_ids"] == [str(trace.evidence[0].id)]
    assert data["ranking"][0]["memory_id"] == str(trace.evidence[0].id)


def test_snapshot_is_payload_free_by_construction() -> None:
    # The schema comment's rule (evidence_traces: "references memory IDs, never copies
    # payloads") is a type-level property: a snapshot cannot even be built with a payload.
    with pytest.raises(TypeError):
        EvidenceSnapshot(  # type: ignore[call-arg]
            id=uuid4(),
            type="meal",
            event_time=_NOW,
            confidence=0.9,
            provenance="live",
            summary="s",
            payload={"protein_g": 40},
        )
    assert "payload" not in _snapshot().to_json()


def test_snapshot_from_row_ignores_extra_row_keys() -> None:
    # Repository rows carry payload/status/etc.; the snapshot takes identity + display
    # metadata only.
    row = {
        "id": uuid4(),
        "type": "meal",
        "event_time": _NOW,
        "confidence": 0.75,
        "provenance": "reconstructed",
        "summary": "est. breakfast",
        "payload": {"nutrition": {"protein_g": 30}},
        "status": "active",
        "has_embedding": True,
    }
    snap = EvidenceSnapshot.from_row(row)
    assert snap.id == row["id"]
    assert snap.provenance == "reconstructed"
    assert "payload" not in snap.to_json()
    assert "status" not in snap.to_json()


def test_trace_objects_are_immutable() -> None:
    trace = _full_trace()
    with pytest.raises(dataclasses.FrozenInstanceError):
        trace.question = "rewritten"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        trace.evidence[0].confidence = 1.0  # type: ignore[misc]


def test_empty_collections_serialize_honestly() -> None:
    # Phase 3 has no insights engine yet — empty tuples must render as [], not error.
    trace = EvidenceTrace(
        trace_id=uuid4(),
        question="anything logged?",
        retrieval_steps=(),
        evidence=(),
        insights=(),
        timeline=(),
        ranking=(),
        assembled_at=_NOW,
    )
    data = trace.to_json()
    assert data["retrieval_steps"] == []
    assert data["evidence"] == []
    assert data["insights"] == []
    assert data["timeline"] == []
    assert data["ranking"] == []
