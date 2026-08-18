"""The ingestion-source invariant (ADR-14.15), proved against the real graph and a real database.

**The requirement, in one line: conversation history is context, never an ingestion source.**

This module exists because adding short-term conversation history to the planner's prompt
re-opens a hole that nothing else in the system would catch. With prior turns visible, a
planner could put a fact from an earlier day into this turn's ingest call — verbatim
("3 eggs"), merged ("3 eggs and 100g paneer"), split across parallel calls, or resolved from a
reference ("same as yesterday") — and it would be extracted against *today's* clock and
committed as a brand-new memory. There is no dedupe in the write path, so the duplicate is
permanent and silently inflates every aggregate, insight and nutrition total computed over
that range, while the glass box faithfully cites it as genuine evidence.

The defence is structural, not behavioural, and that is what these tests pin:

  * ``log_memory`` is a **zero-argument signal** (``agent/tools.py``) — there is no slot in
    which historical text could be expressed.
  * ``ingest_node`` reads **``state["question"]``** — the bytes of *this* HTTP request — and
    nothing else.
  * Extraction is anchored to **``state["now"]``**, the turn's own clock, so "today" can only
    ever mean the date of the request that contained the word.

Every provider here therefore **misbehaves on purpose**: it emits `log_memory` calls stuffed
with a previous turn's text, several at once, in the shapes a real model plausibly would. The
assertion is that none of it reaches the database. A test that scripted a well-behaved planner
would pass on the broken architecture too, and prove nothing.

``RecordingProvider`` derives its events from whatever string it is handed, exactly as a real
extractor would: hand it eggs text and it returns an eggs meal. That is what makes a leak
*visible* — a re-ingested "3 eggs" would materialise as a real eggs row dated to the wrong day.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from agent.graph import run_turn
from agent.tools import COUNT_EVENTS, LOG_MEMORY
from engine.model import ExtractedEvent, ToolCall
from engine.tests.conftest import FakeModelProvider

pytestmark = pytest.mark.usefixtures("saver")

UTC = timezone.utc
IST = "Asia/Kolkata"

#: The two days of the scenario this module was written for.
DAY1 = datetime(2026, 8, 16, 9, 12, tzinfo=UTC)
DAY2 = datetime(2026, 8, 17, 8, 40, tzinfo=UTC)

DAY1_TURN = "Today at breakfast I ate 3 eggs."
DAY2_TURN = "Today at dinner I ate 100g paneer."


def _meal(name: str, qty: dict, summary: str, *, now: datetime, tz: str) -> ExtractedEvent:
    """A meal event anchored to the clock it was extracted with — which is precisely the
    behaviour under test. A real extractor resolves "today" against the ``now`` it is given;
    so does this, so a turn ingested with the wrong clock produces a visibly wrong date."""
    return ExtractedEvent(
        type="meal",
        event_time=now,
        tz=tz,
        confidence=0.9,
        summary=summary,
        payload={"items": [{"name": name, **qty}]},
    )


class RecordingProvider(FakeModelProvider):
    """A provider that extracts from whatever text it receives, and records every input.

    The recording is the point: ``extract_inputs`` is the exact boundary the invariant is
    about — what the *memory extraction* model saw. Assertions read it directly rather than
    inferring from what landed in the database, so a failure says which string leaked.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.extract_inputs: list[dict] = []

    def extract_events(self, text: str, *, now, tz) -> list[ExtractedEvent]:
        self.extract_inputs.append({"text": text, "now": now, "tz": tz})
        lowered = text.lower()
        events: list[ExtractedEvent] = []
        if "egg" in lowered:
            events.append(_meal("eggs", {"qty": 3}, "breakfast: 3 eggs", now=now, tz=tz))
        if "paneer" in lowered:
            events.append(_meal("paneer", {"qty_g": 100}, "dinner: 100g paneer", now=now, tz=tz))
        return events


def _turn(graph, user_id, question: str, *, thread: str, now: datetime):
    return run_turn(
        graph, user_id=user_id, question=question, thread_id=thread, tz=IST, now=now
    )


def _meals(db, user_id: UUID) -> list[dict]:
    """Every meal this user has, oldest first — the ground truth the whole module is about."""
    with db.transaction() as cur:
        cur.execute(
            """
            SELECT summary, event_time, payload
            FROM memories
            WHERE user_id = %s AND type = 'meal' AND status = 'active'
            ORDER BY event_time
            """,
            [user_id],
        )
        return list(cur.fetchall())


def _local_dates(rows: list[dict]) -> list[str]:
    return [r["event_time"].astimezone(UTC).date().isoformat() for r in rows]


# ── the scenario: two days, one of which tries to re-log the other ─────────────────────
def test_day2_turn_never_re_ingests_day1_facts(make_graph, graph_db, user_id, thread_id) -> None:
    """DAY 1 logs 3 eggs on Aug 16. DAY 2 logs 100g paneer on Aug 17 — while the planner
    actively tries to re-log the eggs. Only the paneer may be written.

    This is the exact scenario the architecture was designed around. The Day-2 planner emits
    ``log_memory(text="3 eggs and 100 gram paneer")`` — the *merged-text* failure mode, the
    most dangerous of the four because one tool call yields two events and it slips past any
    "was log_memory called exactly once?" check. On the pre-ADR-14.15 architecture this wrote
    a second eggs row dated Aug 17; here the argument is unreachable.
    """
    provider = RecordingProvider(plan_calls=[ToolCall(tool=LOG_MEMORY, arguments={})])
    graph, _ = make_graph(provider)

    _turn(graph, user_id, DAY1_TURN, thread=thread_id, now=DAY1)

    day1_meals = _meals(graph_db, user_id)
    assert [m["summary"] for m in day1_meals] == ["breakfast: 3 eggs"]
    assert _local_dates(day1_meals) == ["2026-08-16"]

    # Day 2: the planner has history in view and tries to carry the eggs forward.
    provider.plan_calls = [
        ToolCall(tool=LOG_MEMORY, arguments={"text": "3 eggs and 100 gram paneer"})
    ]
    _turn(graph, user_id, DAY2_TURN, thread=thread_id, now=DAY2)

    meals = _meals(graph_db, user_id)
    summaries = [m["summary"] for m in meals]

    # Exactly one new memory, and it is the paneer the user actually reported today.
    assert summaries == ["breakfast: 3 eggs", "dinner: 100g paneer"]
    assert _local_dates(meals) == ["2026-08-16", "2026-08-17"]

    # The specific bug, named: no eggs row on Aug 17.
    aug17 = [m for m in meals if m["event_time"].astimezone(UTC).date().isoformat() == "2026-08-17"]
    assert len(aug17) == 1
    assert "egg" not in aug17[0]["summary"].lower()
    assert "egg" not in str(aug17[0]["payload"]).lower()

    # And the reason it cannot happen: the extractor was handed the current turn, verbatim.
    assert [call["text"] for call in provider.extract_inputs] == [DAY1_TURN, DAY2_TURN]


def test_planner_authored_text_never_reaches_the_extractor(
    make_graph, graph_db, user_id, thread_id
) -> None:
    """The invariant in isolation: whatever the planner writes into the call, the extractor
    sees ``state["question"]``. Covers the verbatim-copy failure mode (a) — the planner logs
    *only* the previous turn's content and none of the current one."""
    provider = RecordingProvider(
        plan_calls=[ToolCall(tool=LOG_MEMORY, arguments={"text": "3 eggs"})]
    )
    graph, _ = make_graph(provider)

    _turn(graph, user_id, DAY2_TURN, thread=thread_id, now=DAY2)

    assert [call["text"] for call in provider.extract_inputs] == [DAY2_TURN]
    assert [m["summary"] for m in _meals(graph_db, user_id)] == ["dinner: 100g paneer"]


def test_many_log_memory_calls_ingest_the_turn_at_most_once(
    make_graph, graph_db, user_id, thread_id
) -> None:
    """Failure mode (c): both providers collect *every* tool_use block, so a planner can emit
    ``log_memory`` several times in one plan. However many arrive, the turn is ingested once —
    otherwise a single meal would be committed two or three times over."""
    provider = RecordingProvider(
        plan_calls=[
            ToolCall(tool=LOG_MEMORY, arguments={"text": "3 eggs"}),
            ToolCall(tool=LOG_MEMORY, arguments={}),
            ToolCall(tool=LOG_MEMORY, arguments={"text": "100 gram paneer"}),
        ]
    )
    graph, _ = make_graph(provider)

    result = _turn(graph, user_id, DAY2_TURN, thread=thread_id, now=DAY2)

    assert len(provider.extract_inputs) == 1
    assert len(result.receipts) == 1
    assert [m["summary"] for m in _meals(graph_db, user_id)] == ["dinner: 100g paneer"]


def test_relative_today_resolves_against_the_current_turns_clock(
    make_graph, graph_db, user_id, thread_id
) -> None:
    """"Today" must mean the date of the request that contained the word.

    ``ingest_node`` used to call ``ingest_text`` without ``now``, so extraction silently fell
    back to ``datetime.now()`` while the planner was grounded on ``state["now"]`` — two clocks
    in one turn. Invisible in production (they differ by milliseconds) and fatal to this
    guarantee, because it left the turn with no single authoritative date to anchor against.
    """
    provider = RecordingProvider(plan_calls=[ToolCall(tool=LOG_MEMORY, arguments={})])
    graph, _ = make_graph(provider)

    _turn(graph, user_id, DAY2_TURN, thread=thread_id, now=DAY2)

    (extracted,) = provider.extract_inputs
    assert extracted["now"] == DAY2, "extraction must use the turn's clock, not wall-clock"
    assert extracted["tz"] == IST
    assert _local_dates(_meals(graph_db, user_id)) == ["2026-08-17"]

    # Not merely "close to now": a turn replayed with an old clock must land on the old date.
    # (Ordered by event_time, so the backdated row sorts *first* — assert on the set of dates
    # rather than on insertion order, which the query deliberately does not preserve.)
    backdated = DAY2 - timedelta(days=30)
    _turn(graph, user_id, "Today I ate 100g paneer again.", thread=thread_id, now=backdated)
    assert _local_dates(_meals(graph_db, user_id)) == [
        backdated.date().isoformat(),
        "2026-08-17",
    ]


def test_a_turn_with_no_log_memory_signal_ingests_nothing(
    make_graph, graph_db, user_id, thread_id
) -> None:
    """The signal still decides *whether* to ingest — removing the text slot did not turn
    every turn into a write. A pure question must leave the memory table alone."""
    provider = RecordingProvider(
        plan_calls=[
            ToolCall(
                tool=COUNT_EVENTS,
                arguments={
                    "type": "meal",
                    "start": (DAY2 - timedelta(days=30)).isoformat(),
                    "end": (DAY2 + timedelta(days=1)).isoformat(),
                },
            )
        ]
    )
    graph, _ = make_graph(provider)

    _turn(graph, user_id, "how many meals did I log?", thread=thread_id, now=DAY2)

    assert provider.extract_inputs == []
    assert _meals(graph_db, user_id) == []
