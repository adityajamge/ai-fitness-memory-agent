"""The end-of-session row purge (Phase 5 M0) — engine/tests/dbcleanup.py.

This module carries the *hard* assertion behind the cleanup mechanism: the root conftest's
session finalizer only warns (see its docstring for why), so correctness is pinned here where
a failure is precise and attributable.

The load-bearing property is not "it deletes rows" but **"it deletes only rows this run
minted"**. An unregistered user's rows surviving a purge is what separates this from the
unbounded sweep that made the old cluster-cleanup path slow and dangerous.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from engine.memory import Memory
from engine.repository import insert_memories
from engine.tests import dbcleanup
from engine.tests.conftest import DATABASE_URL


@pytest.fixture()
def registry():
    """Run each test against a clean registry, and hand the run's real registrations back
    afterwards — clobbering them would disarm the session purge for every earlier test."""
    saved_users, saved_emails = dbcleanup.registered()
    dbcleanup.reset()
    yield dbcleanup
    dbcleanup.reset()
    for user in saved_users:
        dbcleanup.register_user(user)
    for email in saved_emails:
        dbcleanup.register_email(email)


def _memory(user: uuid.UUID, summary: str) -> Memory:
    return Memory(
        user_id=user,
        event_time=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        tz="Asia/Kolkata",
        type="note",
        source="chat",
        provenance="live",
        confidence=1.0,
        summary=summary,
        payload={"text": summary},
    )


def _count(db, user: uuid.UUID) -> int:
    with db.transaction() as cur:
        cur.execute("SELECT count(*) AS n FROM memories WHERE user_id = %s", [user])
        return int(cur.fetchone()["n"])


# ── registry bookkeeping (pure) ────────────────────────────────────────────────────────
def test_register_is_idempotent_and_reports_what_it_holds(registry):
    user = uuid.uuid4()
    registry.register_user(user)
    registry.register_user(user)
    registry.register_email("a@example.com")

    users, emails = registry.registered()
    assert users == {user}
    assert emails == {"a@example.com"}


def test_register_returns_its_argument_so_call_sites_can_inline(registry):
    user = uuid.uuid4()
    assert registry.register_user(user) is user
    assert registry.register_email("b@example.com") == "b@example.com"


def test_purge_without_registrations_is_a_no_op(registry):
    # No connection is opened at all — this must hold even with no database reachable.
    assert registry.purge("postgresql://nonexistent-host-for-tests:1/x") == {}


# ── the purge, against a real database ─────────────────────────────────────────────────
def test_purge_deletes_registered_rows(db, registry):
    user = registry.register_user(uuid.uuid4())
    with db.transaction() as cur:
        insert_memories(cur, [_memory(user, "purge me"), _memory(user, "and me")])
    assert _count(db, user) == 2

    deleted = registry.purge(DATABASE_URL)

    assert deleted.get("memories") == 2
    assert _count(db, user) == 0


def test_purge_leaves_unregistered_rows_untouched(db, registry):
    """The safety property. A purge that reached rows it did not create would be an unbounded
    sweep wearing a registry's clothes."""
    registered_user = registry.register_user(uuid.uuid4())
    stranger = uuid.uuid4()  # deliberately NOT registered
    with db.transaction() as cur:
        insert_memories(cur, [_memory(registered_user, "mine"), _memory(stranger, "not mine")])

    registry.purge(DATABASE_URL)

    assert _count(db, registered_user) == 0
    assert _count(db, stranger) == 1

    # Clean up the row this test deliberately withheld from the registry.
    with db.transaction() as cur:
        cur.execute("DELETE FROM memories WHERE user_id = %s", [stranger])


def test_purge_is_idempotent(db, registry):
    user = registry.register_user(uuid.uuid4())
    with db.transaction() as cur:
        insert_memories(cur, [_memory(user, "once")])

    assert registry.purge(DATABASE_URL).get("memories") == 1
    assert registry.purge(DATABASE_URL) == {}


def test_purge_resolves_registered_emails_to_their_rows(db, registry):
    """api tests can only register the email — the app mints the id — so resolution is the
    path that keeps signup-created accounts from accumulating."""
    user = uuid.uuid4()
    email = f"cleanup-{uuid.uuid4().hex}@example.com"
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO users (id, email, password_hash, salt) VALUES (%s, %s, %s, %s)",
            [user, email, b"x", b"y"],
        )
        insert_memories(cur, [_memory(user, "owned via email registration")])
    registry.register_email(email)  # note: the id itself is never registered

    deleted = registry.purge(DATABASE_URL)

    assert deleted.get("memories") == 1
    assert deleted.get("users") == 1
    assert _count(db, user) == 0


def test_residue_reports_zero_after_a_purge(db, registry):
    """The property the session finalizer checks: a full run leaves no residue for the ids it
    minted."""
    user = registry.register_user(uuid.uuid4())
    with db.transaction() as cur:
        insert_memories(cur, [_memory(user, "residue check")])

    assert registry.residue(DATABASE_URL) == {"memories": 1}
    registry.purge(DATABASE_URL)
    assert registry.residue(DATABASE_URL) == {}


def test_unreachable_database_is_not_an_error(registry):
    """No database means database tests were skipped, so there is nothing to clean — the
    finalizer must not turn that into a failure."""
    registry.register_user(uuid.uuid4())
    assert registry.purge("postgresql://127.0.0.1:1/nope?connect_timeout=1") == {}
    assert registry.residue("postgresql://127.0.0.1:1/nope?connect_timeout=1") == {}


def test_keep_test_rows_disables_the_purge(db, registry, monkeypatch):
    """The post-mortem escape hatch must actually withhold deletion, not just log."""
    user = registry.register_user(uuid.uuid4())
    with db.transaction() as cur:
        insert_memories(cur, [_memory(user, "kept for inspection")])

    monkeypatch.setenv("KEEP_TEST_ROWS", "1")
    assert registry.cleanup_disabled() is True
    assert registry.purge(DATABASE_URL) == {}
    assert _count(db, user) == 1

    monkeypatch.delenv("KEEP_TEST_ROWS")
    registry.purge(DATABASE_URL)
    assert _count(db, user) == 0
