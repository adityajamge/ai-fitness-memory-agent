"""Stage (G): turn + trace persistence (M1, glass-box-architecture.md §4.3).

Runs the real graph against real CockroachDB, so the assertions land at the **committed-row
layer** (ADR-15.6) rather than only at the unit that owns the logic — the point of (G) is
what ends up in the database, not what a function returned.

Three properties carry this module:

* **I-24** — a turn that assembled context has a persisted trace. The phase's property test.
* **I-25** — turn and trace are atomic *with each other*; never an orphan of either.
* **§4.3** — (G) failing costs the glass box, never the turn. The memories and the answer
  stand, and the failure is recorded rather than swallowed.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from agent.checkpointer import CockroachDBSaver
from agent.graph import build_graph, run_turn
from agent.tools import AGGREGATE_MEMORIES, LOG_MEMORY
from engine.db import Database
from engine.ingestion import IngestionService
from engine.model import ExtractedEvent, ToolCall
from engine.tests.conftest import FakeModelProvider
from engine.tests.dbcleanup import register_user

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://root@127.0.0.1:26257/defaultdb?sslmode=disable"
)
_DB_REQUIRED = bool(os.environ.get("CI") or os.environ.get("REQUIRE_DB"))
UTC = timezone.utc
IST = "Asia/Kolkata"
NOW = datetime(2026, 7, 24, 6, 40, tzinfo=UTC)
RANGE = {
    "start": (NOW - timedelta(days=30)).isoformat(),
    "end": (NOW + timedelta(days=1)).isoformat(),
}


@pytest.fixture(scope="module")
def saver():
    try:
        psycopg.connect(DATABASE_URL, connect_timeout=5).close()
    except psycopg.OperationalError as exc:
        if _DB_REQUIRED:
            raise AssertionError(
                f"CI/REQUIRE_DB set but CockroachDB unreachable at {DATABASE_URL}: {exc}"
            ) from exc
        pytest.skip(f"no CockroachDB reachable at {DATABASE_URL} ({exc}); set REQUIRE_DB=1 to fail")
    database = Database(DATABASE_URL)
    database.setup_schema()
    with CockroachDBSaver.from_conn_string(DATABASE_URL) as built:
        built.setup()
        yield built


@pytest.fixture()
def make_graph(saver):
    database = Database(DATABASE_URL)

    def _build(provider: FakeModelProvider, *, db: Database | None = None):
        used = db or database
        ingestion = IngestionService(used, provider, default_tz=IST)
        return build_graph(
            db=used,
            model=provider,
            ingestion=ingestion,
            checkpointer=saver,
            default_tz=IST,
        )

    return _build


def _rows(sql: str, params: dict) -> list[dict]:
    with Database(DATABASE_URL).transaction() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _query_provider() -> FakeModelProvider:
    """A turn that retrieves and narrates — assembles context, so (G) must record it."""
    return FakeModelProvider(
        plan_calls=[ToolCall(tool=AGGREGATE_MEMORIES, arguments={"metric": "protein_g", **RANGE})],
        narration="You averaged 46g.",
    )


def _ingest_provider() -> FakeModelProvider:
    return FakeModelProvider(
        plan_calls=[ToolCall(tool=LOG_MEMORY, arguments={"text": "lunch: 46g protein"})],
        events=[
            ExtractedEvent(
                type="meal",
                event_time=NOW,
                tz=IST,
                confidence=0.9,
                summary="lunch",
                payload={"items": [], "protein_g": 46},
            )
        ],
        narration="Logged.",
    )


def test_query_turn_persists_turn_and_trace(make_graph) -> None:
    """I-24: context was assembled, so a trace exists in the database."""
    user_id = register_user(uuid.uuid4())
    graph = make_graph(_query_provider())

    result = run_turn(
        graph,
        user_id=user_id,
        question="how much protein?",
        thread_id=str(uuid.uuid4()),
        now=NOW,
        tz=IST,
    )

    assert result.turn_record is not None, "a turn that assembled context must be recorded"
    traces = _rows(
        "SELECT trace FROM evidence_traces WHERE turn_id = %(t)s AND user_id = %(u)s",
        {"t": result.turn_record.assistant_turn_id, "u": user_id},
    )
    assert len(traces) == 1
    assert traces[0]["trace"]["question"] == "how much protein?"
    assert "citable_ids" in traces[0]["trace"], "ADR-14.8: the stored trace is self-contained"


def test_both_turn_rows_are_written_with_the_right_roles(make_graph) -> None:
    """The conversation is replayable: the user's message and the answer are both stored."""
    user_id = register_user(uuid.uuid4())
    graph = make_graph(_query_provider())

    result = run_turn(
        graph,
        user_id=user_id,
        question="how much protein?",
        thread_id=str(uuid.uuid4()),
        now=NOW,
        tz=IST,
    )

    rows = _rows(
        "SELECT id, role, content FROM turns WHERE user_id = %(u)s ORDER BY created_at, role",
        {"u": user_id},
    )
    by_role = {r["role"]: r for r in rows}
    assert set(by_role) == {"user", "assistant"}
    assert by_role["user"]["content"] == "how much protein?"
    assert by_role["assistant"]["content"] == "You averaged 46g."
    assert by_role["assistant"]["id"] == result.turn_record.assistant_turn_id


def test_ingest_turn_records_the_memories_it_created(make_graph) -> None:
    """``memory_ids`` links the turn to rows stage (D) already committed."""
    user_id = register_user(uuid.uuid4())
    graph = make_graph(_ingest_provider())

    result = run_turn(
        graph,
        user_id=user_id,
        question="lunch: 46g protein",
        thread_id=str(uuid.uuid4()),
        now=NOW,
        tz=IST,
    )

    created = [ref.id for receipt in result.receipts for ref in receipt.created]
    assert created, "precondition: the ingest turn created a memory"

    rows = _rows(
        "SELECT memory_ids FROM turns WHERE user_id = %(u)s AND role = 'assistant'",
        {"u": user_id},
    )
    assert rows[0]["memory_ids"] == created


def test_no_orphan_trace_and_no_traceless_turn(make_graph) -> None:
    """I-25: turn and trace are atomic with each other, in both directions."""
    user_id = register_user(uuid.uuid4())
    graph = make_graph(_query_provider())
    run_turn(
        graph,
        user_id=user_id,
        question="how much protein?",
        thread_id=str(uuid.uuid4()),
        now=NOW,
        tz=IST,
    )

    orphans = _rows(
        """
        SELECT e.id FROM evidence_traces e
        LEFT JOIN turns t ON t.id = e.turn_id
        WHERE e.user_id = %(u)s AND t.id IS NULL
        """,
        {"u": user_id},
    )
    assert orphans == [], "a trace must never point at a turn that does not exist"

    traceless = _rows(
        """
        SELECT t.id FROM turns t
        LEFT JOIN evidence_traces e ON e.turn_id = t.id
        WHERE t.user_id = %(u)s AND t.role = 'assistant' AND e.id IS NULL
        """,
        {"u": user_id},
    )
    assert traceless == [], "an assistant turn that assembled context must have its trace"


def test_persist_failure_costs_the_glass_box_not_the_turn(make_graph, monkeypatch) -> None:
    """§4.3: the memories are committed and the answer is produced before (G) runs, so a
    failure there must not fail the turn — but it must be *recorded*, never silent."""
    user_id = register_user(uuid.uuid4())
    graph = make_graph(_ingest_provider())

    import agent.graph as graph_module

    def _boom(*args, **kwargs):
        raise psycopg.OperationalError("simulated (G) failure")

    monkeypatch.setattr(graph_module, "persist_turn", _boom)

    result = run_turn(
        graph,
        user_id=user_id,
        question="lunch: 46g protein",
        thread_id=str(uuid.uuid4()),
        now=NOW,
        tz=IST,
    )

    assert result.answer == "Logged.", "the turn's real work stands"
    assert result.turn_record is None, "nothing was recorded"
    assert any("turn not recorded" in e for e in result.errors), "the failure must be visible"

    memories = _rows("SELECT id FROM memories WHERE user_id = %(u)s", {"u": user_id})
    assert len(memories) == 1, "stage (D) committed before (G) ran and is unaffected"
