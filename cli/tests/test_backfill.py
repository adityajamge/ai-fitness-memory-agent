"""Tests for the manual embedding-backfill CLI (T15, `python -m cli.backfill`).

Closes the coverage gap flagged in the 2026-07-21 audit: the opportunistic half of T15 was
tested in engine/tests/test_ingestion.py, but the CLI entry point — user discovery, page
draining, argument handling, output — was not. The implementation is exercised as-is; the
only things patched are the composition seams `main()` already exposes (settings + provider),
so no Bedrock call and no env mutation is needed.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone

import pytest

import cli.backfill as backfill_cli
from cli.tests.conftest import DATABASE_URL, FakeModelProvider
from engine.config import Settings
from engine.ingestion import IngestionService
from engine.memory import Memory
from engine.model import ExtractedEvent
from engine.repository import insert_memory


def _meal_event(summary: str) -> ExtractedEvent:
    return ExtractedEvent(
        type="meal",
        event_time=datetime(2026, 7, 21, 13, 0, tzinfo=timezone.utc),
        tz="Asia/Kolkata",
        confidence=0.9,
        summary=summary,
        payload={"meal_type": "lunch"},
    )


def _service(db, provider, **kw) -> IngestionService:
    return IngestionService(db, provider, default_tz="Asia/Kolkata", **kw)


def _seed_null_rows(db, user_id, n: int) -> None:
    """Create n NULL-embedding rows the realistic way: ingest turns whose embed call fails
    (the opportunistic backfill inside ingest_text also fails, so the rows stay NULL)."""
    for i in range(n):
        provider = FakeModelProvider([_meal_event(f"meal {i}")], embed_error=True)
        receipt = _service(db, provider).ingest_text(user_id, f"meal {i}")
        assert receipt.created[0].embedding_pending is True


def _null_count(db, user_id) -> int:
    with db.transaction() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM memories "
            "WHERE user_id = %s AND embedding IS NULL AND status = 'active'",
            [user_id],
        )
        return cur.fetchone()["n"]


# ── page draining (_backfill_user) ─────────────────────────────────────────────────────
def test_backfill_user_drains_across_pages(db, user_id):
    """5 NULL rows with a batch of 2 forces 3 pages + the terminating empty page — the
    while-loop actually pages rather than stopping after one batch."""
    _seed_null_rows(db, user_id, 5)
    svc = _service(db, FakeModelProvider(), backfill_batch=2)

    total = backfill_cli._backfill_user(svc, user_id)

    assert total == 5
    assert _null_count(db, user_id) == 0


def test_backfill_user_is_idempotent(db, user_id):
    """Second sweep finds nothing — already-embedded rows are never revisited (docstring
    promise of cli/backfill.py)."""
    _seed_null_rows(db, user_id, 2)
    svc = _service(db, FakeModelProvider(), backfill_batch=8)
    assert backfill_cli._backfill_user(svc, user_id) == 2
    assert backfill_cli._backfill_user(svc, user_id) == 0


def test_backfill_user_terminates_when_embedding_keeps_failing(db, user_id):
    """If Bedrock is still down, backfill_embeddings returns 0 on the first page — the
    drain loop must exit (not spin) and leave the rows NULL for the next pass."""
    _seed_null_rows(db, user_id, 3)
    svc = _service(db, FakeModelProvider(embed_error=True), backfill_batch=2)

    assert backfill_cli._backfill_user(svc, user_id) == 0
    assert _null_count(db, user_id) == 3  # nothing lost, nothing half-done


# ── user discovery (_users_with_gaps) ──────────────────────────────────────────────────
def test_users_with_gaps_finds_only_users_with_active_null_rows(db):
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    _seed_null_rows(db, user_a, 1)  # A: active NULL row -> a gap
    provider = FakeModelProvider([_meal_event("embedded fine")])
    _service(db, provider).ingest_text(user_b, "embedded fine")  # B: fully embedded

    gaps = backfill_cli._users_with_gaps(db)
    assert user_a in gaps
    assert user_b not in gaps


def test_users_with_gaps_ignores_inactive_rows(db):
    """A superseded/retracted NULL row is not a gap — the sweep only chases active rows,
    matching fetch_unembedded's own filter."""
    user_c = uuid.uuid4()
    superseded = Memory(
        user_id=user_c,
        event_time=datetime(2026, 7, 21, 9, 0, tzinfo=timezone.utc),
        tz="Asia/Kolkata",
        type="note",
        source="chat",
        provenance="live",
        confidence=1.0,
        summary="old note",
        payload={"text": "old note"},
        status="superseded",
    )
    with db.transaction() as cur:
        insert_memory(cur, superseded)

    assert user_c not in backfill_cli._users_with_gaps(db)


# ── the CLI entry point (main) ─────────────────────────────────────────────────────────
@pytest.fixture()
def cli_env(db, monkeypatch):
    """Patch main()'s two composition seams: settings point at the test DB, the provider
    is the deterministic fake. Everything else runs the real code path."""
    monkeypatch.setattr(
        backfill_cli, "load_settings", lambda: Settings(database_url=DATABASE_URL)
    )
    monkeypatch.setattr(
        backfill_cli, "build_default_provider", lambda settings: FakeModelProvider()
    )

    def run(*argv: str) -> None:
        monkeypatch.setattr(sys, "argv", ["cli.backfill", *argv])
        backfill_cli.main()

    return run


def test_main_single_user_backfills_and_reports(db, user_id, cli_env, capsys):
    _seed_null_rows(db, user_id, 3)

    cli_env("--user", str(user_id))

    assert _null_count(db, user_id) == 0
    out = capsys.readouterr().out
    assert f"user {user_id}: embedded 3 row(s)" in out


def test_main_single_user_without_gaps_reports_zero(db, user_id, cli_env, capsys):
    """--user targets one user explicitly, so it reports that user's (zero) count rather
    than the global 'no rows need backfilling' message."""
    cli_env("--user", str(user_id))
    assert f"user {user_id}: embedded 0 row(s)" in capsys.readouterr().out


def test_main_all_sweeps_users_with_gaps(db, cli_env, capsys):
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    _seed_null_rows(db, user_a, 2)
    _seed_null_rows(db, user_b, 1)

    cli_env("--all")

    assert _null_count(db, user_a) == 0
    assert _null_count(db, user_b) == 0
    out = capsys.readouterr().out
    assert f"user {user_a}: embedded 2 row(s)" in out
    assert f"user {user_b}: embedded 1 row(s)" in out


# ── argument handling (no database needed: argparse exits before load_settings) ───────
def test_main_rejects_user_and_all_together(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cli.backfill", "--user", str(uuid.uuid4()), "--all"])
    with pytest.raises(SystemExit):
        backfill_cli.main()


def test_main_requires_a_target(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cli.backfill"])
    with pytest.raises(SystemExit):
        backfill_cli.main()


def test_main_rejects_malformed_uuid(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cli.backfill", "--user", "not-a-uuid"])
    with pytest.raises(SystemExit):
        backfill_cli.main()
