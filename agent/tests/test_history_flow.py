"""Short-term memory end to end, and its separation from long-term memory (ADR-14.16).

``engine/tests/test_history.py`` pins the read. This file runs the **whole graph** across two
days against real CockroachDB and asserts the separation the architecture exists to hold:

    Agent LLM (plan + narrate)     sees  history + the current question
    Extraction LLM                 sees  the current question ONLY
    Long-term memory ingestion     source: the current question ONLY

The Day-1/Day-2 scenario is the acceptance test for the whole design, and it is written to
fail loudly rather than subtly: the Day-2 planner **deliberately misbehaves**, emitting
``log_memory`` calls stuffed with Day 1's eggs while Day 1's exchange really is in its history
window. A well-behaved scripted planner would pass on a broken architecture and prove nothing.

``ScriptedProvider`` extracts from whatever text it is handed, exactly as a real extractor
would, so a leak materialises as a real, wrongly-dated row rather than as a silent no-op.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from agent.graph import run_turn
from agent.providers._prompts import history_messages
from agent.tools import LOG_MEMORY
from engine.model import ExtractedEvent, HistoryTurn, ToolCall
from engine.tests.conftest import FakeModelProvider

pytestmark = pytest.mark.usefixtures("saver")

UTC = timezone.utc
IST = "Asia/Kolkata"

DAY1 = datetime(2026, 8, 16, 9, 12, tzinfo=UTC)
DAY2 = datetime(2026, 8, 17, 8, 40, tzinfo=UTC)

DAY1_TURN = "Today at breakfast I ate 3 eggs."
DAY2_TURN = "Today at dinner I ate 100g paneer."
DAY1_ANSWER = "Logged 3 eggs on August 16, 2026. Protein: 18g."


class ScriptedProvider(FakeModelProvider):
    """Extracts from the text it receives; records every input to every role."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.extract_inputs: list[dict] = []

    def extract_events(self, text: str, *, now, tz) -> list[ExtractedEvent]:
        self.extract_inputs.append({"text": text, "now": now, "tz": tz})
        lowered = text.lower()
        events: list[ExtractedEvent] = []
        if "egg" in lowered:
            events.append(_meal("eggs", {"qty": 3}, "breakfast: 3 eggs", now, tz))
        if "paneer" in lowered:
            events.append(_meal("paneer", {"qty_g": 100}, "dinner: 100g paneer", now, tz))
        return events


def _meal(name: str, qty: dict, summary: str, now: datetime, tz: str) -> ExtractedEvent:
    return ExtractedEvent(
        type="meal",
        event_time=now,
        tz=tz,
        confidence=0.9,
        summary=summary,
        payload={"items": [{"name": name, **qty}]},
    )


def _turn(graph, user_id, question: str, *, thread: str, now: datetime):
    return run_turn(
        graph, user_id=user_id, question=question, thread_id=thread, tz=IST, now=now
    )


def _meals(db, user_id: UUID) -> list[dict]:
    with db.transaction() as cur:
        cur.execute(
            """
            SELECT summary, event_time FROM memories
            WHERE user_id = %s AND type = 'meal' AND status = 'active'
            ORDER BY event_time
            """,
            [user_id],
        )
        return list(cur.fetchall())


def _dates(rows: list[dict]) -> list[str]:
    return [r["event_time"].astimezone(UTC).date().isoformat() for r in rows]


# ── the acceptance scenario ───────────────────────────────────────────────────────────
def test_day2_ingests_only_the_current_turn_even_with_day1_in_history(
    make_graph, graph_db, user_id, thread_id
) -> None:
    """The requirement, end to end, with history genuinely loaded and the planner misbehaving.

    Day 1 logs 3 eggs on Aug 16. Day 2 the planner can see that exchange and tries to carry it
    forward — the merged-text failure mode, which is the dangerous one because a single tool
    call yields two events and no call-count check would notice. Only the paneer may be
    written, and only on Aug 17.
    """
    provider = ScriptedProvider(
        plan_calls=[ToolCall(tool=LOG_MEMORY, arguments={})], narration=DAY1_ANSWER
    )
    graph, _ = make_graph(provider)

    _turn(graph, user_id, DAY1_TURN, thread=thread_id, now=DAY1)
    assert _dates(_meals(graph_db, user_id)) == ["2026-08-16"]

    provider.plan_calls = [
        ToolCall(tool=LOG_MEMORY, arguments={"text": "3 eggs and 100 gram paneer"})
    ]
    provider.narration = "Logged 100g paneer today."
    _turn(graph, user_id, DAY2_TURN, thread=thread_id, now=DAY2)

    # History really was in play — otherwise this test proves nothing about the risk.
    assert any("3 eggs" in turn.content for turn in provider.last_plan_history)

    meals = _meals(graph_db, user_id)
    assert [m["summary"] for m in meals] == ["breakfast: 3 eggs", "dinner: 100g paneer"]
    assert _dates(meals) == ["2026-08-16", "2026-08-17"]

    # The specific bug: no eggs row dated Aug 17.
    aug17 = [m for m in meals if m["event_time"].astimezone(UTC).date().isoformat() == "2026-08-17"]
    assert len(aug17) == 1 and "egg" not in aug17[0]["summary"].lower()


# ── the separation, asserted per boundary ─────────────────────────────────────────────
def test_history_never_reaches_long_term_memory_extraction(
    make_graph, graph_db, user_id, thread_id
) -> None:
    """The extraction model sees the current question and nothing else — no history, ever.

    This is the boundary that matters most, and it holds by *signature*:
    ``extract_events(text, now, tz)`` has no parameter through which a previous turn could
    arrive. The assertion is on the recorded call, so a future refactor that widened it would
    fail here rather than in production six weeks later.
    """
    provider = ScriptedProvider(plan_calls=[ToolCall(tool=LOG_MEMORY, arguments={})])
    graph, _ = make_graph(provider)

    _turn(graph, user_id, DAY1_TURN, thread=thread_id, now=DAY1)
    _turn(graph, user_id, DAY2_TURN, thread=thread_id, now=DAY2)

    assert [call["text"] for call in provider.extract_inputs] == [DAY1_TURN, DAY2_TURN]
    for call in provider.extract_inputs:
        assert "3 eggs" not in call["text"] or call["text"] == DAY1_TURN
        assert DAY1_ANSWER not in call["text"]


def test_history_is_available_to_the_conversational_answer(
    make_graph, graph_db, user_id, thread_id
) -> None:
    """Both agent-facing calls receive the conversation — that is the whole feature.

    "How did you find that?" is unanswerable without it, and it is what makes the second turn
    of every conversation coherent rather than amnesiac.
    """
    provider = ScriptedProvider(plan_calls=[], narration=DAY1_ANSWER)
    graph, _ = make_graph(provider)

    _turn(graph, user_id, "what is my weight?", thread=thread_id, now=DAY1)
    _turn(graph, user_id, "how did you find that?", thread=thread_id, now=DAY2)

    for seen in (provider.last_plan_history, provider.last_narrate_history):
        assert [h.role for h in seen] == ["user", "assistant"]
        assert seen[0].content == "what is my weight?"
        assert seen[1].content == DAY1_ANSWER

    # Rendered for the model, each message carries the date it was said — so an earlier
    # "today" can never be read as meaning the current date.
    #
    # That date is `turns.created_at` in the user's timezone: **when the message was actually
    # sent**, which is the only honest answer to "when was this said". It is deliberately NOT
    # the turn's injected logical clock (`state["now"]`) — in production the two are the same
    # moment, and only a test that backdates `now` can tell them apart. This asserts against
    # today's date in IST precisely to state that semantic rather than let a hardcoded literal
    # imply the prefix follows the injected clock.
    # Two separate facts, so a failure says which one broke — and so neither depends on the
    # wall-clock date at assertion time (deriving it from `datetime.now()` would flake for the
    # seconds either side of IST midnight).
    first, second = provider.last_narrate_history
    rendered = history_messages(provider.last_narrate_history)

    # (a) the prefix is rendered from the turn's own `at`, in the user's timezone
    assert rendered[0] == {
        "role": "user",
        "content": f"[{first.at.strftime('%b %d')}] what is my weight?",
    }
    assert rendered[1] == {
        "role": "assistant",
        "content": f"[{second.at.strftime('%b %d')}] {DAY1_ANSWER}",
    }
    assert first.at.utcoffset() == ZoneInfo(IST).utcoffset(first.at)

    # (b) `at` is the moment the message was SENT, not the injected logical clock. The turn
    # was backdated to DAY1 (Aug 16 2026) while the row was written just now, so anchoring to
    # the logical clock would have shown Aug 16 — this is what pins the distinction.
    assert abs((datetime.now(timezone.utc) - first.at).total_seconds()) < 3600
    assert first.at.date() != DAY1.date()


def test_the_current_turn_is_never_in_its_own_history(
    make_graph, graph_db, user_id, thread_id
) -> None:
    """Structural, not filtered: stage (G) persists at the END of the graph while history is
    read at the START, so the question being answered has no row to find yet. A turn that saw
    itself would let the model treat its own unanswered question as prior context."""
    provider = ScriptedProvider(plan_calls=[], narration="ok")
    graph, _ = make_graph(provider)

    _turn(graph, user_id, "first ever message", thread=thread_id, now=DAY1)
    assert provider.last_plan_history == []  # nothing precedes the first turn
    assert provider.last_narrate_history == []

    _turn(graph, user_id, "second message", thread=thread_id, now=DAY2)
    contents = [h.content for h in provider.last_plan_history]
    assert "second message" not in contents
    assert contents == ["first ever message", "ok"]


def test_history_does_not_leak_across_threads(make_graph, graph_db, user_id) -> None:
    """"New chat" must actually be a new chat. The sidebar switches threads constantly, and a
    window that bled across them would look exactly like the model hallucinating context."""
    provider = ScriptedProvider(plan_calls=[], narration="ok")
    graph, _ = make_graph(provider)

    _turn(graph, user_id, "message in thread A", thread="thread-A", now=DAY1)
    _turn(graph, user_id, "message in thread B", thread="thread-B", now=DAY2)

    assert provider.last_plan_history == []


def test_history_never_becomes_citable_evidence(make_graph, graph_db, user_id, thread_id) -> None:
    """Short-term and long-term memory stay separate at the citation boundary.

    History is deliberately kept out of ``ContextBlock``, so ``citable_ids`` — which is
    computed from retrieved rows alone — cannot grow from it. That is what stops an answer
    citing something that was merely *said* rather than retrieved, which would put a
    fabricated chip in the glass box.
    """
    provider = ScriptedProvider(plan_calls=[], narration="ok")
    graph, _ = make_graph(provider)

    _turn(graph, user_id, "today i ate 3 eggs", thread=thread_id, now=DAY1)
    result = _turn(graph, user_id, "what did I just say?", thread=thread_id, now=DAY2)

    assert provider.last_narrate_history, "history was loaded"
    assert result.citations == []
    assert result.trace is not None and result.trace.citable_ids == frozenset()


# ── the ingest signal, with history in play ───────────────────────────────────────────
def test_repeated_log_memory_signals_still_ingest_the_turn_once(
    make_graph, graph_db, user_id, thread_id
) -> None:
    provider = ScriptedProvider(
        plan_calls=[
            ToolCall(tool=LOG_MEMORY, arguments={"text": "3 eggs"}),
            ToolCall(tool=LOG_MEMORY, arguments={}),
            ToolCall(tool=LOG_MEMORY, arguments={"text": "3 eggs again"}),
        ]
    )
    graph, _ = make_graph(provider)

    _turn(graph, user_id, DAY1_TURN, thread=thread_id, now=DAY1)
    _turn(graph, user_id, DAY2_TURN, thread=thread_id, now=DAY2)

    assert [c["text"] for c in provider.extract_inputs] == [DAY1_TURN, DAY2_TURN]
    assert len(_meals(graph_db, user_id)) == 2


def test_a_question_only_turn_writes_nothing_even_with_history(
    make_graph, graph_db, user_id, thread_id
) -> None:
    """History must not turn a read into a write. The planner's signal still decides."""
    provider = ScriptedProvider(plan_calls=[], narration="ok")
    graph, _ = make_graph(provider)

    _turn(graph, user_id, DAY1_TURN, thread=thread_id, now=DAY1)  # no log_memory: no write
    _turn(graph, user_id, "what did I eat?", thread=thread_id, now=DAY2)

    assert provider.extract_inputs == []
    assert _meals(graph_db, user_id) == []


def test_relative_today_uses_the_current_turns_clock_with_history_loaded(
    make_graph, graph_db, user_id, thread_id
) -> None:
    """Both turns say "today" and mean different days. The clock comes from the request, so
    Day 2's word resolves to Aug 17 even though Day 1's identical word is right there in the
    window resolving to Aug 16."""
    provider = ScriptedProvider(plan_calls=[ToolCall(tool=LOG_MEMORY, arguments={})])
    graph, _ = make_graph(provider)

    _turn(graph, user_id, DAY1_TURN, thread=thread_id, now=DAY1)
    _turn(graph, user_id, DAY2_TURN, thread=thread_id, now=DAY2)

    assert [c["now"] for c in provider.extract_inputs] == [DAY1, DAY2]
    assert _dates(_meals(graph_db, user_id)) == ["2026-08-16", "2026-08-17"]


# ── the durability boundary is unchanged ──────────────────────────────────────────────
def test_history_is_never_checkpointed(make_graph, saver, user_id, thread_id) -> None:
    """History rides the per-invocation carrier, not graph state (M5-1).

    It is derived from ``turns`` on every turn, so checkpointing it would persist a second,
    staler copy of rows the database already owns and grow the checkpoint without bound. The
    channel allowlist is unchanged by this feature, and this asserts it stayed that way.
    """
    provider = ScriptedProvider(plan_calls=[], narration="ok")
    graph, _ = make_graph(provider)

    _turn(graph, user_id, "first", thread=thread_id, now=DAY1)
    _turn(graph, user_id, "second", thread=thread_id, now=DAY2)

    channels = saver.get_tuple({"configurable": {"thread_id": thread_id}}).checkpoint[
        "channel_values"
    ]
    assert "history" not in channels
    assert not any(isinstance(v, HistoryTurn) for v in channels.values())


def test_a_turn_still_answers_when_history_cannot_be_loaded(
    make_graph, graph_db, user_id, thread_id, monkeypatch
) -> None:
    """Best-effort, like stages (F₀) and (G): history is an enhancement to two model calls,
    not a precondition. A read failure costs the turn its conversational memory and nothing
    else — degrading to a stateless-but-correct answer beats a 502."""
    import agent.graph as graph_module

    provider = ScriptedProvider(plan_calls=[], narration="still answered")
    graph, _ = make_graph(provider)

    def _boom(*args, **kwargs):
        raise RuntimeError("history read exploded")

    monkeypatch.setattr(graph_module, "fetch_history", _boom)
    result = _turn(graph, user_id, "hello", thread=thread_id, now=DAY2)

    assert result.answer == "still answered"
    assert any("conversation history unavailable" in e for e in result.errors)
