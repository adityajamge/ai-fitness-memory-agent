"""Recall builder tests (M2 — 12-test-plan.md `engine/retrieval` recall block: vector
top-k, status='active' filter, NULL-embedding rows excluded).

Distance fixtures use handcrafted orthonormal basis vectors, NOT the conftest
_unit_vector fake: that fake seeds from a text's character ordinal sum, so distinct
texts can collide into identical vectors (review finding F-5). Basis vectors make
every expected distance exact: |e_i - e_j| = √2 for i≠j.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from engine.db import Database
from engine.memory import Memory
from engine.model import EmbeddingError
from engine.repository import insert_memories, mark_superseded
from engine.retrieval import (
    RecallSpec,
    RetrievalSpecError,
    embed_query,
    recall_memories,
)
from engine.tests.conftest import FakeModelProvider
from engine.tests.dbcleanup import new_user

UTC = timezone.utc
DIMS = 512
T0 = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


def _basis(i: int) -> list[float]:
    vec = [0.0] * DIMS
    vec[i] = 1.0
    return vec


def _mix(i: int, j: int) -> list[float]:
    """Normalized midpoint of two basis vectors — distance to e_i is √(2-√2) ≈ 0.765."""
    inv = 1.0 / math.sqrt(2.0)
    vec = [0.0] * DIMS
    vec[i] = inv
    vec[j] = inv
    return vec


def _mem(
    user_id: UUID,
    summary: str,
    embedding: list[float] | None,
    *,
    type_: str = "note",
    event_time: datetime = T0,
) -> Memory:
    return Memory(
        user_id=user_id,
        event_time=event_time,
        tz="Asia/Kolkata",
        type=type_,
        source="chat",
        provenance="live",
        confidence=0.9,
        payload={"text": summary} if type_ == "note" else {},
        summary=summary,
        embedding=embedding,
    )


def _seed(db: Database, memories: list[Memory]) -> list[UUID]:
    with db.transaction() as cur:
        return insert_memories(cur, memories)


def _run(db: Database, user_id: UUID, spec: RecallSpec, vec: list[float]):
    with db.transaction() as cur:
        return recall_memories(cur, user_id, spec, vec)


# ── K-NN semantics ────────────────────────────────────────────────────────────────────
def test_top_k_ordering_by_distance(db, user_id) -> None:
    ids = _seed(
        db,
        [
            _mem(user_id, "exact match", _basis(0)),
            _mem(user_id, "half match", _mix(0, 1)),
            _mem(user_id, "orthogonal", _basis(1)),
        ],
    )

    result, step = _run(db, user_id, RecallSpec(query="knee pain"), _basis(0))

    assert [h.id for h in result.hits] == ids
    assert result.hits[0].distance == pytest.approx(0.0, abs=1e-6)
    assert result.hits[1].distance == pytest.approx(math.sqrt(2 - math.sqrt(2)), abs=1e-6)
    assert result.hits[2].distance == pytest.approx(math.sqrt(2), abs=1e-6)
    assert step.row_count == 3


def test_top_k_truncates(db, user_id) -> None:
    _seed(db, [_mem(user_id, f"m{i}", _basis(i)) for i in range(4)])
    result, _ = _run(db, user_id, RecallSpec(query="q", top_k=2), _basis(0))
    assert len(result.hits) == 2
    assert result.hits[0].distance < result.hits[1].distance


def test_null_embedding_rows_are_invisible(db, user_id) -> None:
    # Backfill-pending rows (T15) must be silently excluded, never an error.
    _seed(
        db,
        [
            _mem(user_id, "embedded", _basis(0)),
            _mem(user_id, "backfill pending", None),
        ],
    )
    result, _ = _run(db, user_id, RecallSpec(query="q"), _basis(0))
    assert [h.summary for h in result.hits] == ["embedded"]


def test_superseded_rows_are_excluded(db, user_id) -> None:
    keep, drop = _seed(
        db,
        [
            _mem(user_id, "typed replacement", _mix(0, 1)),
            _mem(user_id, "old note", _basis(0)),  # closest to the query, but superseded
        ],
    )
    with db.transaction() as cur:
        mark_superseded(cur, user_id, drop, superseded_by=keep)

    result, _ = _run(db, user_id, RecallSpec(query="q"), _basis(0))
    assert [h.id for h in result.hits] == [keep]


def test_type_filter(db, user_id) -> None:
    _seed(
        db,
        [
            _mem(user_id, "a meal", _basis(0), type_="meal"),
            _mem(user_id, "a note", _basis(1)),
        ],
    )
    result, _ = _run(db, user_id, RecallSpec(query="q", type="meal"), _basis(1))
    # The note is nearer (distance 0) but filtered out by type.
    assert [h.type for h in result.hits] == ["meal"]


def test_date_filter(db, user_id) -> None:
    _seed(
        db,
        [
            _mem(user_id, "in range", _basis(1), event_time=T0),
            _mem(user_id, "too old", _basis(0), event_time=T0 - timedelta(days=40)),
        ],
    )
    spec = RecallSpec(query="q", start=T0 - timedelta(days=7), end=T0 + timedelta(days=7))
    result, _ = _run(db, user_id, spec, _basis(0))
    assert [h.summary for h in result.hits] == ["in range"]


def test_cross_user_isolation(db, user_id) -> None:
    other = new_user()
    _seed(db, [_mem(other, "other user's secret", _basis(0))])
    _seed(db, [_mem(user_id, "mine", _basis(1))])

    result, _ = _run(db, user_id, RecallSpec(query="q"), _basis(0))
    assert [h.summary for h in result.hits] == ["mine"]


def test_empty_result_is_defined(db, user_id) -> None:
    result, step = _run(db, user_id, RecallSpec(query="anything"), _basis(0))
    assert result.is_empty
    assert result.hits == ()
    assert step.row_count == 0


# ── the engine embeds, the agent passes text (D-4) ────────────────────────────────────
def test_embed_query_matches_stored_pipeline(db, user_id) -> None:
    # A summary embedded at ingestion and the same text embedded as a query go through
    # the same provider — identical vectors, distance ≈ 0.
    provider = FakeModelProvider()
    text = "felt a sharp pain in my left knee during squats"
    _seed(db, [_mem(user_id, text, provider.embed([text])[0])])

    qvec = embed_query(provider, text)
    result, _ = _run(db, user_id, RecallSpec(query=text), qvec)
    assert result.hits[0].distance == pytest.approx(0.0, abs=1e-6)


def test_embed_query_failure_raises_before_any_sql() -> None:
    with pytest.raises(EmbeddingError):
        embed_query(FakeModelProvider(embed_error=True), "q")


# ── slot validation (DB-free) ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": "   "},
        {"query": "q", "top_k": 0},
        {"query": "q", "top_k": 51},
        {"query": "q", "type": "diary"},  # not a registry type
        {"query": "q", "start": T0},  # start without end
        {"query": "q", "start": T0, "end": T0 - timedelta(days=1)},  # inverted
        {"query": "q", "start": datetime(2026, 7, 1), "end": datetime(2026, 7, 2)},  # naive
    ],
)
def test_invalid_slots_raise_before_any_sql(kwargs) -> None:
    with pytest.raises(RetrievalSpecError):
        RecallSpec(**kwargs)


def test_wrong_dimension_vector_rejected(db, user_id) -> None:
    with pytest.raises(RetrievalSpecError):
        _run(db, user_id, RecallSpec(query="q"), [1.0, 0.0, 0.0])


# ── the trace step ────────────────────────────────────────────────────────────────────
def test_step_elides_the_vector_and_keeps_the_query_text(db, user_id) -> None:
    _seed(db, [_mem(user_id, "m", _basis(0))])
    spec = RecallSpec(query="when did my knee hurt?", top_k=5, type="note")
    _, step = _run(db, user_id, spec, _basis(0))

    assert step.family == "recall"
    for placeholder in ("%(qvec)s", "%(user_id)s", "%(type)s", "%(top_k)s"):
        assert placeholder in step.sql
    assert str(user_id) not in step.sql
    # The 512 floats never enter the trace; the query text does.
    assert step.params["qvec"] == "<512-d unit vector of 'query'>"
    assert step.params["query"] == "when did my knee hurt?"
    assert json.loads(json.dumps(step.params))["top_k"] == 5
