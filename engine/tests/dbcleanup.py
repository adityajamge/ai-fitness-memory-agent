"""Test-run row registry + end-of-session purge (Phase 5 M0).

**The problem this closes.** Every database-backed test mints a fresh ``uuid4()`` user and
writes rows under it; nothing ever deleted them, and ``memories`` has no foreign key to
``users``, so orphan rows were not even cascade-deletable. One full-suite run therefore added
hundreds of permanent rows and hundreds of permanent user ids to the shared dev cluster. The
previous cluster reached ~9k memories / ~4.7k users and stopped passing the suite altogether —
persistent ``SerializationFailure``, a different random test each run — which cost a long
debugging detour before it was traced to cluster state rather than code (TODOS.md).

**Why a registry rather than a sweep.** The purge deletes **only ids this process minted**. It
never scans for "test-looking" rows, because a heuristic sweep is what made
``test_main_all_sweeps_users_with_gaps`` slow and contention-prone in the first place, and
because a pattern match is one bad regex away from deleting real data. Everything registered
here came from ``uuid4()`` inside this run, so the purge is incapable of touching a row it did
not create — a safety property that holds *even if* ``DATABASE_URL`` is misconfigured, which
matters because the root conftest's redirect is the only other thing standing between the
suite and the real cluster.

**Why session-scoped rather than per-test.** Per-test teardown would add a round trip to every
one of ~500 tests on a suite that already takes ~4 minutes, and against a cross-region cluster
that is minutes of pure latency. One purge at the end costs a handful of statements.

Registration is automatic at the two choke points every test-owned row normally passes
through — ``engine/tests/conftest.py``'s ``user_id`` fixture and ``api/tests/conftest.py``'s
``unique_email()``. A third way exists and is **not** automatic: a test that needs a *second*
user (cross-user isolation, the backfill CLI's multi-user discovery) mints one itself. Those
call sites use ``new_user()`` below rather than a bare ``uuid4()`` — the first full run with
cleanup enabled left 11 rows behind precisely because they did not.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

__all__ = [
    "new_user",
    "register_user",
    "register_email",
    "registered",
    "reset",
    "purge",
    "residue",
    "cleanup_disabled",
]

# Ids minted by this process. Sets, so re-registration is free and order never matters.
_users: set[UUID] = set()
_emails: set[str] = set()

#: Tables purged for a registered user, in FK-safe order (``sessions``/``user_profile``
#: reference ``users``). ``memories`` has no FK at all, which is exactly why it needs this.
_USER_SCOPED = ("memories", "sessions", "user_profile")

#: Users deleted per statement. Small on purpose: one ``ANY(...)`` over every id the run
#: minted produced a **21½-minute** DELETE against the cross-region cluster on 2026-08-05, which
#: was almost certainly the source of the ``SerializationFailure`` flakes — a write transaction
#: open that long gets its timestamp pushed and cannot refresh its read set at COMMIT. Teardown
#: must never be the longest transaction in the run. Full write-up:
#: ``docs/engineering/cockroachdb-lessons-learned.md``.
PURGE_BATCH = 50

#: LangGraph checkpoint tables. Their ``thread_id`` is namespaced ``"<user_id>:<client id>"``
#: by ``api/routers/chat.py::thread_key`` (ADR-14.13), so user-scoped threads are purgeable by
#: prefix. Threads created by agent tests that drive the graph directly use raw ids
#: (``m5-…``/``guard-…``/``canary-…``) and are **not** covered — see the module note in
#: ``purge`` below.
_CHECKPOINT = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")


def cleanup_disabled() -> bool:
    """Escape hatch: ``KEEP_TEST_ROWS=1`` leaves everything in place for post-mortem
    inspection of a failing run."""
    return (os.environ.get("KEEP_TEST_ROWS") or "").strip().lower() in {"1", "true", "yes"}


def register_user(user_id: UUID) -> UUID:
    """Record a user id whose rows this run owns. Returns it, so call sites can inline."""
    _users.add(user_id)
    return user_id


def new_user() -> UUID:
    """Mint **and register** a user id outside the ``user_id`` fixture.

    Use this wherever a test needs a *second* user — cross-user isolation checks, and the
    backfill CLI's multi-user discovery. A bare ``uuid4()`` at those call sites was the third
    way rows entered the database, unregistered and therefore unpurged; the first full run
    with cleanup enabled left exactly 11 such rows behind. The named helper exists so the
    pattern is discoverable rather than re-invented."""
    return register_user(uuid4())


def register_email(email: str) -> str:
    """Record an email whose account this run creates through the API. The id is unknown at
    registration time (the app mints it), so it is resolved during ``purge``."""
    _emails.add(email)
    return email


def registered() -> tuple[frozenset[UUID], frozenset[str]]:
    """Everything registered so far — (user ids, emails)."""
    return frozenset(_users), frozenset(_emails)


def reset() -> None:
    """Forget every registration. For tests of this module itself; never call from a fixture."""
    _users.clear()
    _emails.clear()


def _table_exists(cur: psycopg.Cursor, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS t", [table])
    return cur.fetchone()["t"] is not None


def _resolve(cur: psycopg.Cursor, emails: list[str]) -> set[UUID]:
    """Emails → user ids. Absent emails simply yield nothing (a signup test that asserted a
    rejection created no row)."""
    if not emails:
        return set()
    cur.execute("SELECT id FROM users WHERE email = ANY(%s)", [emails])
    return {row["id"] for row in cur.fetchall()}


def _all_user_ids(cur: psycopg.Cursor) -> list[UUID]:
    return sorted(_users | _resolve(cur, sorted(_emails)), key=str)


def purge(database_url: str) -> dict[str, int]:
    """Delete every row this run created, returning per-table deleted counts.

    Safe to call repeatedly: a second call deletes nothing. Safe to call with nothing
    registered: it opens no connection. An unreachable database is **not** an error — the
    suite skips database tests in that case, so there is nothing to clean.

    **Deletes in bounded batches** (``PURGE_BATCH``). A single statement covering every id the
    run minted grows without limit as the suite does, and a long-running teardown transaction is
    both slow and a contention source in its own right — see the constant's note.

    Checkpoint rows are purged by ``thread_id`` prefix for registered users only. Threads that
    agent tests create with raw ids (``m5-…``/``guard-…``/``canary-…``) are a **known
    remainder, measured at 281 rows per full run** (70 checkpoints + 61 blobs + 150 writes on
    2026-08-03) against the ~1,500 ``memories``/``users``/``sessions`` rows this closes, which
    now land at exactly zero. Closing it means registering raw thread ids at call sites in five
    agent test modules — the same mechanical change ``new_user`` applied to users, deliberately
    left outside M0's scope and recorded rather than silently ignored.
    """
    if cleanup_disabled():
        return {}
    if not (_users or _emails):
        return {}

    deleted: dict[str, int] = {}
    try:
        with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=10) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                user_ids = _all_user_ids(cur)
                if not user_ids:
                    return {}

                for table in _CHECKPOINT:
                    if not _table_exists(cur, table):
                        continue
                    deleted[table] = _delete_batched(
                        cur,
                        f"DELETE FROM {table} WHERE thread_id LIKE ANY(%s)",
                        [f"{uid}:%" for uid in user_ids],
                    )

                for table in _USER_SCOPED:
                    if not _table_exists(cur, table):
                        continue
                    deleted[table] = _delete_batched(
                        cur, f"DELETE FROM {table} WHERE user_id = ANY(%s)", user_ids
                    )

                # Last: sessions/user_profile reference users.
                if _table_exists(cur, "users"):
                    deleted["users"] = _delete_batched(
                        cur, "DELETE FROM users WHERE id = ANY(%s)", user_ids
                    )
    except psycopg.OperationalError:
        return {}  # no database this run; nothing was written, nothing to clean

    return {table: n for table, n in deleted.items() if n}


def _delete_batched(cur: psycopg.Cursor, sql: str, values: list) -> int:
    """Run one parameterized DELETE per ``PURGE_BATCH`` slice, returning the total deleted.

    Each statement is its own implicit transaction (the connection is autocommit), so a slow
    slice cannot hold locks while the rest run — which is the whole point. Batching also keeps
    the statement's parameter array a fixed size regardless of how large the suite grows.
    """
    total = 0
    for start in range(0, len(values), PURGE_BATCH):
        cur.execute(sql, [values[start : start + PURGE_BATCH]])
        total += cur.rowcount
    return total


def residue(database_url: str) -> dict[str, int]:
    """Rows still present for registered ids — the verification half of ``purge``.

    Non-zero after a purge means the purge itself is broken (a table gained rows keyed by a
    column this module does not know about), which is why the session fixture reports it.
    """
    if not (_users or _emails):
        return {}

    found: dict[str, int] = {}
    try:
        with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=10) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                user_ids = _all_user_ids(cur)
                if not user_ids:
                    return {}
                for table in _USER_SCOPED:
                    if not _table_exists(cur, table):
                        continue
                    cur.execute(
                        f"SELECT count(*) AS n FROM {table} WHERE user_id = ANY(%s)", [user_ids]
                    )
                    found[table] = int(cur.fetchone()["n"])
                if _table_exists(cur, "users"):
                    cur.execute(
                        "SELECT count(*) AS n FROM users WHERE id = ANY(%s)", [user_ids]
                    )
                    found["users"] = int(cur.fetchone()["n"])
    except psycopg.OperationalError:
        return {}

    return {table: n for table, n in found.items() if n}
