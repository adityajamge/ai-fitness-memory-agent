"""Tests for the replay CLI's main loop (T8 M4).

This commit covers the NEW/DONE path only (docs/engineering/replay-architecture.md §3, §4.3):
resume correctness and the P0 duplicate-prevention guard. CORRECTED-record handling, the §4.10
halt threshold, and --rebuild-ledger land in later commits and are only pinned here as "not yet
implemented" (test_corrected_record_is_not_yet_handled).

DB-backed, via cli/tests/conftest's real-CockroachDB `db`/`user_id` fixtures and
`FakeModelProvider`.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from cli.replay import ReplaySummary, _default_ledger_path, run_replay
from cli.replay_dataset import ReplayRecord
from cli.replay_ledger import REPLAY_RECORD_ID_KEY, ReplayLedger
from cli.tests.conftest import FakeModelProvider
from engine.ingestion import IngestionService

TZ = "Asia/Kolkata"


def _record(day: str, *, qty: float = 4, **kw) -> ReplayRecord:
    return ReplayRecord(
        record_id=f"meal.{day}",
        type=kw.pop("type", "meal"),
        event_time=kw.pop("event_time", f"{day}T12:00:00+05:30"),
        tz=TZ,
        confidence=kw.pop("confidence", 0.9),
        payload=kw.pop("payload", {"items": [{"name": "egg", "qty": qty}]}),
        source_ref=kw.pop("source_ref", f"§2 Timeline :: {day}"),
        summary=kw.pop("summary", "eggs"),
        **kw,
    )


def _service(db, provider) -> IngestionService:
    return IngestionService(db, provider, default_tz=TZ)


def _fetch(db, user_id, memory_id):
    from engine.repository import get_memory

    with db.transaction() as cur:
        return get_memory(cur, user_id, memory_id)


def _row_count(db, user_id) -> int:
    with db.transaction() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM memories WHERE user_id = %(user_id)s",
            {"user_id": user_id},
        )
        return cur.fetchone()["n"]


# ── run_replay: NEW/DONE path ────────────────────────────────────────────────────────────


def test_new_records_are_ingested_and_marked_done(db, user_id, tmp_path: Path) -> None:
    provider = FakeModelProvider()
    svc = _service(db, provider)
    ledger = ReplayLedger(tmp_path / "replay.ledger.jsonl")
    records = [_record("2026-03-25"), _record("2026-03-26"), _record("2026-03-27")]

    summary = run_replay(svc, ledger, records, user_id)

    assert summary == ReplaySummary(new=3, skipped=0)
    assert len(ledger) == 3
    for record in records:
        assert ledger.is_done(record.record_id)
        entry = ledger.entry(record.record_id)
        assert len(entry.memory_ids) == 1
        row = _fetch(db, user_id, UUID(entry.memory_ids[0]))
        assert row["source"] == "replay"
        assert row["provenance"] == "reconstructed"
        assert row["payload"][REPLAY_RECORD_ID_KEY] == record.record_id


def test_second_run_skips_everything_and_writes_no_new_rows(db, user_id, tmp_path: Path) -> None:
    ledger_path = tmp_path / "replay.ledger.jsonl"
    records = [_record("2026-03-25"), _record("2026-03-26")]

    run_replay(_service(db, FakeModelProvider()), ReplayLedger(ledger_path), records, user_id)
    before = _row_count(db, user_id)

    second_ledger = ReplayLedger.load(ledger_path)
    summary = run_replay(_service(db, FakeModelProvider()), second_ledger, records, user_id)

    assert summary == ReplaySummary(new=0, skipped=2)
    assert _row_count(db, user_id) == before


def test_forced_double_run_produces_no_duplicate_rows(db, user_id, tmp_path: Path) -> None:
    """The P0 guard: re-running the exact same records against the same ledger file, as if
    the CLI had simply been invoked twice, never doubles a row."""
    ledger_path = tmp_path / "replay.ledger.jsonl"
    records = [_record("2026-03-25"), _record("2026-03-26"), _record("2026-03-27")]

    run_replay(_service(db, FakeModelProvider()), ReplayLedger.load(ledger_path), records, user_id)
    after_first = _row_count(db, user_id)

    run_replay(_service(db, FakeModelProvider()), ReplayLedger.load(ledger_path), records, user_id)
    after_second = _row_count(db, user_id)

    assert after_second == after_first == 3


def test_interrupted_run_resumes_without_reprocessing(db, user_id, tmp_path: Path) -> None:
    ledger_path = tmp_path / "replay.ledger.jsonl"
    records = [_record(f"2026-03-{d:02d}") for d in range(25, 31)]  # 6 records

    run_replay(
        _service(db, FakeModelProvider()), ReplayLedger.load(ledger_path), records[:3], user_id
    )
    assert _row_count(db, user_id) == 3

    resumed = ReplayLedger.load(ledger_path)
    summary = run_replay(_service(db, FakeModelProvider()), resumed, records, user_id)

    assert summary == ReplaySummary(new=3, skipped=3)  # records 1-3 skipped, 4-6 new
    assert _row_count(db, user_id) == 6


def test_crash_window_costs_at_most_one_reprocessed_record(db, user_id, tmp_path: Path) -> None:
    """§4.3's accepted, bounded risk: a crash between the ingest commit and the ledger write
    re-processes that one record next run. This test pins the bound, not "no duplicates ever" --
    exactly one extra row, never more."""
    ledger_path = tmp_path / "replay.ledger.jsonl"
    record = _record("2026-03-25")

    svc = _service(db, FakeModelProvider())
    event = record.as_extracted_event(extra_payload={REPLAY_RECORD_ID_KEY: record.record_id})
    svc.ingest_events(user_id, [event])  # committed...
    # ...but the process "crashes" before ledger.mark_done ever runs.
    assert _row_count(db, user_id) == 1

    ledger = ReplayLedger.load(ledger_path)  # empty -- never wrote this record
    run_replay(svc, ledger, [record], user_id)

    assert _row_count(db, user_id) == 2  # exactly one re-processed record, not more


def test_extract_calls_stay_zero(db, user_id, tmp_path: Path) -> None:
    """PROPERTY (§4.11): the direct-ingest path never calls extraction, even across a
    multi-record run."""
    provider = FakeModelProvider()
    records = [_record("2026-03-25"), _record("2026-03-26")]
    run_replay(_service(db, provider), ReplayLedger(tmp_path / "l.jsonl"), records, user_id)

    assert provider.extract_calls == 0
    assert provider.embed_calls > 0


def test_corrected_record_is_not_yet_handled(db, user_id, tmp_path: Path) -> None:
    ledger_path = tmp_path / "replay.ledger.jsonl"
    original = _record("2026-03-25")
    run_replay(
        _service(db, FakeModelProvider()), ReplayLedger.load(ledger_path), [original], user_id
    )

    changed = _record("2026-03-25", payload={"items": [{"name": "egg", "qty": 6}]})
    with pytest.raises(NotImplementedError):
        run_replay(
            _service(db, FakeModelProvider()),
            ReplayLedger.load(ledger_path),
            [changed],
            user_id,
        )


# ── _default_ledger_path (pure) ─────────────────────────────────────────────────────────


def test_default_ledger_path_derivation() -> None:
    assert _default_ledger_path(Path("data/replay/dataset.jsonl")) == Path(
        "data/replay/dataset.ledger.jsonl"
    )
