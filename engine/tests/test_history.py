"""Short-term conversation memory: the read, its budgets, and its scoping (ADR-14.16).

Unit-level counterpart to ``agent/tests/test_history_flow.py``, which proves the same thing
through the whole graph. This file pins the query itself — ordering, windowing, scoping, and
the citation scrub — against real rows in real CockroachDB, because every one of those is a
property of the SQL rather than of anything a mock could stand in for.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from engine.history import (
    DEFAULT_MAX_TURNS,
    fetch_history,
    scrub_citations,
)
from engine.trace import EvidenceTrace
from engine.turns import persist_turn

IST = "Asia/Kolkata"
UTC = timezone.utc


def _trace(question: str) -> EvidenceTrace:
    return EvidenceTrace(
        trace_id=uuid.uuid4(),
        question=question,
        retrieval_steps=(),
        evidence=(),
        insights=(),
        timeline=(),
        ranking=(),
        assembled_at=datetime.now(UTC),
        citable_ids=frozenset(),
    )


def _exchange(db, user_id, thread: str, question: str, answer: str) -> None:
    """Write one turn-pair exactly the way stage (G) does — same function, same transaction."""
    with db.transaction() as cur:
        persist_turn(
            cur,
            user_id=user_id,
            thread_id=thread,
            question=question,
            answer=answer,
            trace=_trace(question),
        )


@pytest.fixture()
def thread(user_id) -> str:
    return f"{user_id}:hist-{uuid.uuid4().hex[:8]}"


def _read(db, user_id, thread: str, **kwargs):
    with db.transaction() as cur:
        return fetch_history(cur, user_id, thread, tz=IST, **kwargs)


# ── ordering and shape ────────────────────────────────────────────────────────────────
def test_history_reads_back_oldest_first_with_roles_intact(db, user_id, thread) -> None:
    """A conversation must arrive in the order it happened, user before assistant.

    Both rows of a turn are written in one transaction and therefore share a ``created_at``
    (CockroachDB's ``now()`` is the transaction timestamp), so ordering by time alone is
    ambiguous within a turn — the ``role`` tiebreak is what makes it deterministic. Getting
    this wrong would hand the model an inverted conversation, which reads as the assistant
    speaking first and answering questions not yet asked.
    """
    _exchange(db, user_id, thread, "today i ate 3 eggs", "Logged 3 eggs.")
    _exchange(db, user_id, thread, "and how much protein?", "About 18g.")

    history = _read(db, user_id, thread)

    assert [(h.role, h.content) for h in history] == [
        ("user", "today i ate 3 eggs"),
        ("assistant", "Logged 3 eggs."),
        ("user", "and how much protein?"),
        ("assistant", "About 18g."),
    ]


def test_timestamps_come_back_in_the_users_timezone(db, user_id, thread) -> None:
    """Rendering a date prefix must not require the caller to know the user's zone — the
    conversion happens here, once, so ``history_messages`` stays pure formatting (D-5)."""
    _exchange(db, user_id, thread, "hello", "hi")

    (first, _) = _read(db, user_id, thread)

    assert first.at.tzinfo is not None
    assert first.at.utcoffset() == ZoneInfo(IST).utcoffset(first.at)


def test_an_unknown_timezone_degrades_to_utc_rather_than_raising(db, user_id, thread) -> None:
    """A bad tz on a profile must cost a nicer date rendering, never the whole turn."""
    _exchange(db, user_id, thread, "hello", "hi")

    with db.transaction() as cur:
        history = fetch_history(cur, user_id, thread, tz="Not/AZone")

    assert len(history) == 2
    assert all(h.at.tzinfo is not None for h in history)


# ── scoping ───────────────────────────────────────────────────────────────────────────
def test_history_is_scoped_to_one_thread(db, user_id, thread) -> None:
    """Switching threads in the sidebar must not leak the previous conversation into the
    next one — they are different conversations, and mixing them is what makes an assistant
    look like it is hallucinating context."""
    other = f"{user_id}:hist-{uuid.uuid4().hex[:8]}"
    _exchange(db, user_id, thread, "in thread one", "ack one")
    _exchange(db, user_id, other, "in thread two", "ack two")

    contents = [h.content for h in _read(db, user_id, thread)]

    assert contents == ["in thread one", "ack one"]


def test_history_is_scoped_to_one_user(db, thread) -> None:
    """I-28, applied to the conversation. Two users presenting the same thread key must not
    see each other's messages — the namespacing in ``thread_key`` already prevents the
    collision, and the ``user_id`` filter here means the guarantee does not rest on a string
    prefix alone."""
    from engine.tests.dbcleanup import register_user

    alice, bob = register_user(uuid.uuid4()), register_user(uuid.uuid4())
    shared = f"shared-{uuid.uuid4().hex[:8]}"
    _exchange(db, alice, shared, "alice's private message", "ack")

    assert _read(db, bob, shared) == []
    assert [h.content for h in _read(db, alice, shared)] == ["alice's private message", "ack"]


def test_an_empty_thread_is_an_empty_window_not_an_error(db, user_id, thread) -> None:
    """The first message of a conversation has no history, and that is the normal case for
    every new chat — it must return cleanly rather than needing a caller-side guard."""
    assert _read(db, user_id, thread) == []


# ── budgets ───────────────────────────────────────────────────────────────────────────
def test_the_window_keeps_the_most_recent_turns(db, user_id, thread) -> None:
    """When the turn budget binds, the *newest* messages survive. Recency is what resolves a
    reference; the older material is long-term memory's job, which is the whole product."""
    for n in range(10):
        _exchange(db, user_id, thread, f"question {n}", f"answer {n}")

    history = _read(db, user_id, thread, max_turns=4)

    assert [h.content for h in history] == [
        "question 8",
        "answer 8",
        "question 9",
        "answer 9",
    ]


def test_the_character_budget_drops_oldest_first(db, user_id, thread) -> None:
    _exchange(db, user_id, thread, "x" * 400, "y" * 400)
    _exchange(db, user_id, thread, "recent question", "recent answer")

    history = _read(db, user_id, thread, max_chars=100)

    assert [h.content for h in history] == ["recent question", "recent answer"]


def test_one_huge_message_is_clipped_not_allowed_to_eat_the_window(db, user_id, thread) -> None:
    """A pasted wall of text must not push every other turn out. The head is kept: the start
    of a message carries the topic, and the topic is what history is for."""
    _exchange(db, user_id, thread, "earlier question", "earlier answer")
    _exchange(db, user_id, thread, "z" * 5000, "ok")

    history = _read(db, user_id, thread, max_turn_chars=200, max_chars=2000)

    assert [h.content for h in history][:2] == ["earlier question", "earlier answer"]
    clipped = next(h for h in history if h.content.startswith("z"))
    assert len(clipped.content) < 260
    assert clipped.content.endswith("[truncated]")


def test_a_zero_turn_window_disables_history_entirely(db, user_id, thread) -> None:
    """The kill switch: ``HISTORY_MAX_TURNS=0`` restores exactly the previous stateless
    behaviour, without a code change or a redeploy of logic."""
    _exchange(db, user_id, thread, "hello", "hi")
    assert _read(db, user_id, thread, max_turns=0) == []


def test_the_default_window_is_the_documented_one() -> None:
    assert DEFAULT_MAX_TURNS == 12  # ≈6 exchanges (ADR-14.16)


# ── the citation scrub ────────────────────────────────────────────────────────────────
def test_citation_markers_are_stripped_from_recalled_answers() -> None:
    """Historical answers are full of ``[memory-id]`` markers. Replayed verbatim they invite
    the model to reuse an id for a claim *this* turn's evidence does not support —
    ``validate_citations`` then flags the answer invalid and the user sees a broken glass box
    caused by our own prompt. The markers are machine annotations with no conversational
    meaning, so dropping them costs the model nothing."""
    mid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    assert scrub_citations(f"you averaged 137g protein [{mid}].") == "you averaged 137g protein."
    assert mid not in scrub_citations(f"one [{mid}] and two [{uuid.uuid4()}] here")


def test_the_scrub_leaves_ordinary_bracketed_prose_alone() -> None:
    """Only well-formed memory ids are removed — the pattern is a UUID, not 'any brackets',
    so a user's own square brackets survive."""
    assert scrub_citations("I ate eggs [the good ones]") == "I ate eggs [the good ones]"


def test_stored_answers_are_scrubbed_on_the_way_out(db, user_id, thread) -> None:
    mid = uuid.uuid4()
    _exchange(db, user_id, thread, "how much protein?", f"About 18g [{mid}].")

    history = _read(db, user_id, thread)

    assert str(mid) not in history[1].content
    assert history[1].content == "About 18g."
    # The user's own turn is never scrubbed — it is their words, not our annotations.
    assert history[0].content == "how much protein?"


def test_empty_turns_are_skipped_rather_than_spending_the_window(db, user_id, thread) -> None:
    """An answer that was nothing but a citation scrubs to nothing. A blank message teaches
    the model nothing, so it should not consume one of twelve slots."""
    _exchange(db, user_id, thread, "real question", f"[{uuid.uuid4()}]")

    assert [h.content for h in _read(db, user_id, thread)] == ["real question"]


def test_the_window_spans_only_the_recent_past(db, user_id, thread) -> None:
    """Sanity check on the ordering key: a thread whose rows were written across a span still
    comes back newest-window-first, not insertion-ordered."""
    for n in range(3):
        _exchange(db, user_id, thread, f"q{n}", f"a{n}")
    with db.transaction() as cur:
        cur.execute(
            "UPDATE turns SET created_at = created_at - %s WHERE user_id = %s AND content = 'q0'",
            [timedelta(days=2), user_id],
        )

    history = _read(db, user_id, thread, max_turns=2)

    assert [h.content for h in history] == ["q2", "a2"]
