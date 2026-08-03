"""Tests for the idempotent replay resume ledger (T8 M2).

Covers the M2 block of docs/engineering/replay-architecture.md §7. The ledger is the **P0
defense**: the write path deliberately has no deduplication, so everything standing between a
re-run and a duplicated 424-record history lives in this one class.

The load-bearing tests, in order of what they protect:

* **drift detection** — distinguishing "already done" from "changed since" is what makes
  §4.12's correction path possible at all; getting it wrong either re-ingests or mass-supersedes.
* **corrupt-ledger handling** — silently starting fresh on a populated-but-damaged ledger would
  re-ingest an already-committed history. That is the P0 outcome, reached through what looks
  like graceful error handling.
* **rebuild fails safe** — a rebuilt entry has no content to compare, and must report DONE
  rather than CORRECTED, or the first run after a recovery supersedes everything.
* **the record_id guard** — a hand-invented id makes a committed record look new on the next
  regeneration, which is the other road to duplicates.

DB-backed tests use the real single-node CockroachDB fixture (ADR-13.8) via cli/tests/conftest.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cli.replay_dataset import ReplayRecord, dumps_canonical
from cli.replay_ledger import (
    REPLAY_RECORD_ID_KEY,
    LedgerEntry,
    LedgerError,
    LedgerState,
    ReplayLedger,
    content_hash_of,
    verify_record_id,
)
from engine.memory import Memory
from engine.repository import insert_memory
from engine.tests.dbcleanup import new_user

TZ = "Asia/Kolkata"
NOW = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)


# ── record factories (ids and dates must agree — the guard enforces it) ────────────────────


def _point(record_id: str = "blood-report.2026-03-25", **kw) -> ReplayRecord:
    day = record_id.rsplit(".", 1)[-1]
    return ReplayRecord(
        record_id=record_id,
        type=kw.pop("type", "blood_report"),
        event_time=kw.pop("event_time", f"{day}T09:00:00+05:30"),
        tz=TZ,
        confidence=kw.pop("confidence", 0.95),
        payload=kw.pop("payload", {"panel": "baseline", "markers": {"vitamin_d_ng_ml": 6.2}}),
        source_ref=kw.pop("source_ref", "§2 Timeline :: 2026-03-25"),
        summary=kw.pop("summary", "Baseline blood report"),
        **kw,
    )


def _expanded(
    composition: str = "meal-pattern.2026-03-26.2026-04-24",
    day: str = "2026-04-03",
    **kw,
) -> ReplayRecord:
    return ReplayRecord(
        record_id=f"{composition}#{day}",
        type=kw.pop("type", "meal"),
        event_time=kw.pop("event_time", f"{day}T12:00:00+05:30"),
        tz=TZ,
        confidence=kw.pop("confidence", 0.67),
        payload=kw.pop("payload", {"items": [{"name": "egg", "qty": 4}]}),
        source_ref="§3 Diet phases :: 2026-03-26 → 2026-04-24",
        summary=kw.pop("summary", "4 eggs and 200g dahi"),
        expanded_from=kw.pop(
            "expanded_from",
            {
                "period_start": "2026-03-26",
                "period_end": "2026-04-24",
                "cadence": "daily",
                "assertion": "4 eggs + 200g dahi daily",
                "composition": composition,
            },
        ),
        **kw,
    )


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "replay.ledger.jsonl"


# ── the round trip ────────────────────────────────────────────────────────────────────────


def test_mark_done_then_is_done_roundtrip(ledger_path: Path):
    ledger = ReplayLedger.load(ledger_path)
    record = _point()
    assert ledger.state(record) is LedgerState.NEW
    assert not ledger.is_done(record.record_id)

    ledger.mark_done(record, [uuid.uuid4()], now=NOW)

    assert ledger.is_done(record.record_id)
    assert ledger.state(record) is LedgerState.DONE
    assert len(ledger) == 1


def test_state_survives_a_reload(ledger_path: Path):
    """The whole point: a fresh process must see what the previous one committed."""
    record = _point()
    ReplayLedger.load(ledger_path).mark_done(record, [uuid.uuid4()], now=NOW)

    reopened = ReplayLedger.load(ledger_path)
    assert reopened.state(record) is LedgerState.DONE
    assert len(reopened) == 1


def test_entry_retains_memory_ids_and_record_snapshot(ledger_path: Path):
    """`memory_ids` are §4.12's supersession targets; the snapshot is what lets a correction
    diff be rendered with no database read and no model call."""
    ledger = ReplayLedger.load(ledger_path)
    record = _expanded()
    mem_ids = [uuid.uuid4(), uuid.uuid4()]

    ledger.mark_done(record, mem_ids, now=NOW)
    entry = ledger.entry(record.record_id)

    assert entry is not None
    assert entry.memory_ids == [str(m) for m in mem_ids]
    assert entry.record == record.to_json()
    assert entry.ingested_at == "2026-07-31T09:00:00Z"
    assert entry.rebuilt is False


def test_each_mark_is_flushed_immediately(ledger_path: Path):
    """§4.9 flushes per record, not per batch, so a crash costs at most the in-flight record.
    Buffering would silently widen that window to N."""
    ledger = ReplayLedger.load(ledger_path)
    for i, day in enumerate(("2026-04-01", "2026-04-02", "2026-04-03"), start=1):
        ledger.mark_done(_expanded(day=day), [uuid.uuid4()], now=NOW)
        # a separate reader — i.e. what a post-crash process would see — sees it already
        assert len(ReplayLedger.load(ledger_path)) == i


# ── drift detection (§4.12) ───────────────────────────────────────────────────────────────


def test_changed_content_reports_corrected(ledger_path: Path):
    """Same record_id, different content — the case that must be neither skipped (the fix
    never lands) nor re-ingested (a duplicate alongside the stale row)."""
    ledger = ReplayLedger.load(ledger_path)
    ledger.mark_done(_expanded(), [uuid.uuid4()], now=NOW)

    corrected = _expanded(payload={"items": [{"name": "egg", "qty": 3}]})  # 4 eggs → 3
    assert corrected.record_id == _expanded().record_id
    assert ledger.state(corrected) is LedgerState.CORRECTED


@pytest.mark.parametrize(
    "field,value",
    [
        ("confidence", 0.42),
        ("summary", "reworded summary"),
        ("event_time", "2026-04-03T18:30:00+05:30"),
    ],
)
def test_drift_is_detected_on_any_field(ledger_path: Path, field: str, value):
    ledger = ReplayLedger.load(ledger_path)
    ledger.mark_done(_expanded(), [uuid.uuid4()], now=NOW)
    assert ledger.state(_expanded(**{field: value})) is LedgerState.CORRECTED


def test_identical_content_is_not_drift(ledger_path: Path):
    """Regeneration is byte-deterministic (M1), so an untouched record must re-hash equal —
    otherwise every rerun would report the whole dataset as corrected."""
    ledger = ReplayLedger.load(ledger_path)
    ledger.mark_done(_expanded(), [uuid.uuid4()], now=NOW)
    assert ledger.state(_expanded()) is LedgerState.DONE


def test_content_hash_is_stable_and_order_independent():
    a = content_hash_of(_point())
    b = content_hash_of(_point())
    assert a == b and a.startswith("sha256:")


# ── the record_id guard (§4.3) ────────────────────────────────────────────────────────────


def test_valid_ids_pass():
    verify_record_id(_point())
    verify_record_id(_expanded())
    verify_record_id(_expanded(composition="supplement.2026-03-28.ongoing", day="2026-04-04"))


@pytest.mark.parametrize(
    "bad_id",
    [
        "my-custom-record",  # hand-invented
        "Blood-Report.2026-03-25",  # not a converter slug
        "blood-report.25-03-2026",  # wrong date format
        "blood-report",  # no date
        "blood-report.2026-03-25#2026-03-25",  # '#' without expanded_from
    ],
)
def test_non_derivable_point_ids_are_rejected(bad_id: str):
    with pytest.raises(LedgerError):
        verify_record_id(_point(record_id=bad_id, event_time="2026-03-25T09:00:00+05:30"))


def test_point_id_date_must_match_event_time():
    record = _point(record_id="blood-report.2026-03-25", event_time="2026-05-01T09:00:00+05:30")
    with pytest.raises(LedgerError, match="carries date 2026-03-25"):
        verify_record_id(record)


def test_expanded_id_must_match_its_composition():
    record = _expanded()
    wrong = "meal-pattern.2020-01-01.2020-02-01#2026-04-03"
    tampered = ReplayRecord(**{**record.__dict__, "record_id": wrong})
    with pytest.raises(LedgerError, match="disagrees with its expanded_from.composition"):
        verify_record_id(tampered)


def test_expanded_id_occurrence_must_match_event_time():
    record = _expanded(day="2026-04-03", event_time="2026-04-09T12:00:00+05:30")
    with pytest.raises(LedgerError, match="occurrence date 2026-04-03"):
        verify_record_id(record)


def test_expanded_record_needs_the_expanded_shape():
    """A record carrying expanded_from but a point-shaped id is equally non-derivable."""
    record = _expanded()
    tampered = ReplayRecord(**{**record.__dict__, "record_id": "meal-pattern.2026-04-03"})
    with pytest.raises(LedgerError, match="not a converter-derived expanded id"):
        verify_record_id(tampered)


# ── collapsed periods: the third shape (M2 amendment, 2026-08-02) ─────────────────────────
#
# §4.1's two narrowings collapse a *period* into a single dated `note` — a change marker
# covered by a §3 period, or a `*-pattern` with no cadence (the lifelong-vegetarian and
# junk-food phases). Those keep the period-shaped id but have no expanded_from, so they match
# neither original grammar. The guard rejected all six in the real dataset, which the M5 smoke
# test caught. These records are the narrative Story A's causal chain cites, so losing them
# silently would have been the expensive kind of bug.


def _collapsed(record_id: str = "meal-pattern.2006-08-14.2026-03-26", **kw) -> ReplayRecord:
    """A collapsed period: period-shaped id, dated at the period START, no expanded_from."""
    start = record_id.split(".")[1]
    return ReplayRecord(
        record_id=record_id,
        type=kw.pop("type", "note"),
        event_time=kw.pop("event_time", f"{start}T12:00:00+05:30"),
        tz=TZ,
        confidence=kw.pop("confidence", 0.6),
        payload=kw.pop("payload", {"text": "strictly vegetarian from birth"}),
        source_ref=kw.pop("source_ref", "§3 Diet phases :: 2006-08-14 → 2026-03-26"),
        summary=kw.pop("summary", "strictly vegetarian from birth"),
        **kw,
    )


@pytest.mark.parametrize(
    "record_id",
    [
        "meal-pattern.2006-08-14.2026-03-26",  # closed period
        "illness.2026-03-28.2026-04-01",  # short closed period
        "note.2026-06-23.ongoing",  # open-ended
        "meal-pattern.2026-07-06.ongoing",
        "supplement.2026-03-28.ongoing",
    ],
)
def test_collapsed_period_ids_pass(record_id: str):
    """Every real-dataset shape the smoke test surfaced."""
    verify_record_id(_collapsed(record_id))


def test_all_three_shapes_pass_together():
    verify_record_id(_point())
    verify_record_id(_collapsed())
    verify_record_id(_expanded())


def test_collapsed_period_start_must_match_event_time():
    """Held to the same strictness as a point event — the date in the id must be the record's."""
    record = _collapsed(
        "meal-pattern.2006-08-14.2026-03-26", event_time="2026-05-01T12:00:00+05:30"
    )
    with pytest.raises(LedgerError, match="carries period start 2006-08-14"):
        verify_record_id(record)


def test_collapsed_period_dated_at_end_is_rejected():
    """Dating a collapsed period at its END is exactly the hand-edit the guard must catch."""
    record = _collapsed(
        "meal-pattern.2006-08-14.2026-03-26", event_time="2026-03-26T12:00:00+05:30"
    )
    with pytest.raises(LedgerError, match="collapsed periods are dated at their start"):
        verify_record_id(record)


def test_collapsed_period_end_before_start_is_rejected():
    """An extra strictness the two original grammars never had."""
    record = _collapsed("meal-pattern.2026-05-01.2026-01-01")
    with pytest.raises(LedgerError, match="ends .* before it starts"):
        verify_record_id(record)


@pytest.mark.parametrize(
    "bad_id",
    [
        "meal-pattern.2006-08-14.2026-03-26.2026-04-01",  # four parts
        "meal-pattern.2006-08-14.forever",  # 'forever' is not 'ongoing'
        "meal-pattern.2006-08-14.",  # trailing dot, empty end
        "meal-pattern..2026-03-26",  # empty start
        "Meal-Pattern.2006-08-14.2026-03-26",  # not a converter slug
        "meal-pattern.14-08-2006.2026-03-26",  # wrong date format
        "meal pattern.2006-08-14.2026-03-26",  # space in slug
        "meal-pattern.2006-08-14.ongoing#2026-03-26",  # '#' without expanded_from
    ],
)
def test_invented_period_shaped_ids_are_still_rejected(bad_id: str):
    """The guard must not have been widened into 'anything with dots passes'."""
    with pytest.raises(LedgerError):
        verify_record_id(_collapsed(bad_id, event_time="2006-08-14T12:00:00+05:30"))


def test_occurrence_marker_without_expanded_from_names_the_real_problem():
    with pytest.raises(LedgerError, match="occurrence marker"):
        verify_record_id(
            _collapsed(
                "meal-pattern.2006-08-14.ongoing#2006-08-14",
                event_time="2006-08-14T12:00:00+05:30",
            )
        )


# ── file robustness ───────────────────────────────────────────────────────────────────────


def test_missing_ledger_starts_empty(tmp_path: Path):
    ledger = ReplayLedger.load(tmp_path / "absent.jsonl")
    assert len(ledger) == 0
    assert ledger.state(_point()) is LedgerState.NEW


def test_torn_final_line_is_recovered(ledger_path: Path):
    """The expected crash artifact: the process died mid-append. Everything before the torn
    line is still trustworthy, so it stands."""
    ledger = ReplayLedger.load(ledger_path)
    ledger.mark_done(_expanded(day="2026-04-01"), [uuid.uuid4()], now=NOW)
    ledger.mark_done(_expanded(day="2026-04-02"), [uuid.uuid4()], now=NOW)
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write('{"record_id": "meal-pattern.2026-03-26.2026-04-24#2026-04-03", "content_')

    recovered = ReplayLedger.load(ledger_path)
    assert len(recovered) == 2
    assert recovered.state(_expanded(day="2026-04-03")) is LedgerState.NEW


def test_malformed_line_mid_file_raises_rather_than_starting_fresh(ledger_path: Path):
    """The dangerous case. Starting fresh here would re-ingest every already-committed record;
    the error names --rebuild-ledger, which recovers state from the database instead."""
    ledger = ReplayLedger.load(ledger_path)
    ledger.mark_done(_expanded(day="2026-04-01"), [uuid.uuid4()], now=NOW)
    ledger.mark_done(_expanded(day="2026-04-02"), [uuid.uuid4()], now=NOW)
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    ledger_path.write_text("\n".join([lines[0], "{{ garbage", lines[1]]) + "\n", encoding="utf-8")

    with pytest.raises(LedgerError, match="rebuild-ledger"):
        ReplayLedger.load(ledger_path)


def test_entry_missing_a_required_field_raises(ledger_path: Path):
    ledger_path.write_text(json.dumps({"content_hash": "sha256:x"}) + "\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="missing required field"):
        ReplayLedger.load(ledger_path)


def test_blank_lines_are_tolerated(ledger_path: Path):
    ledger = ReplayLedger.load(ledger_path)
    ledger.mark_done(_point(), [uuid.uuid4()], now=NOW)
    ledger_path.write_text(
        "\n" + ledger_path.read_text(encoding="utf-8") + "\n\n", encoding="utf-8"
    )
    assert len(ReplayLedger.load(ledger_path)) == 1


def test_a_later_line_supersedes_an_earlier_one(ledger_path: Path):
    """Append-only means a re-marked record appears twice; the last write is the truth."""
    ledger = ReplayLedger.load(ledger_path)
    record = _expanded()
    ledger.mark_done(record, [uuid.uuid4()], now=NOW)
    final_ids = [uuid.uuid4()]
    ledger.mark_done(record, final_ids, now=NOW)

    reopened = ReplayLedger.load(ledger_path)
    assert len(reopened) == 1
    assert reopened.entry(record.record_id).memory_ids == [str(final_ids[0])]


def test_ledger_lines_are_canonical_json(ledger_path: Path):
    ledger = ReplayLedger.load(ledger_path)
    entry = ledger.mark_done(_point(), [uuid.uuid4()], now=NOW)
    assert ledger_path.read_text(encoding="utf-8").splitlines()[0] == dumps_canonical(
        entry.to_json()
    )


# ── rebuild from the database (§4.3) ──────────────────────────────────────────────────────


def _seed_replay_row(db, user_id, record_id: str, *, day: str = "2026-04-03", **kw) -> uuid.UUID:
    """Commit one memory the way the M4 loop will: stamped with its replay_record_id."""
    with db.transaction() as cur:
        return insert_memory(
            cur,
            Memory(
                user_id=user_id,
                event_time=datetime.fromisoformat(f"{day}T12:00:00+05:30"),
                tz=TZ,
                type=kw.pop("type", "meal"),
                source=kw.pop("source", "replay"),
                provenance="reconstructed",
                confidence=0.67,
                summary=f"seeded {record_id}",
                payload={REPLAY_RECORD_ID_KEY: record_id, "items": []},
                **kw,
            ),
        )


def test_rebuild_reconstructs_state_from_committed_rows(db, user_id, ledger_path: Path):
    ids = {
        "meal-pattern.2026-03-26.2026-04-24#2026-04-01": [],
        "meal-pattern.2026-03-26.2026-04-24#2026-04-02": [],
    }
    for rid in ids:
        ids[rid].append(_seed_replay_row(db, user_id, rid, day=rid.rsplit("#", 1)[1]))

    with db.transaction() as cur:
        rebuilt = ReplayLedger.rebuild_from_db(ledger_path, cur, user_id, now=NOW)

    assert len(rebuilt) == 2
    for rid, mem_ids in ids.items():
        assert rebuilt.entry(rid).memory_ids == [str(m) for m in mem_ids]
    assert ReplayLedger.load(ledger_path).is_done(next(iter(ids)))  # persisted, not in-memory


def test_rebuilt_entries_report_done_not_corrected(db, user_id, ledger_path: Path):
    """Rebuilding recovers *what* committed, not the bytes it committed with. Reporting
    CORRECTED on unknown content would mass-supersede the dataset on the first run after a
    recovery, so it fails to DONE instead."""
    rid = "meal-pattern.2026-03-26.2026-04-24#2026-04-03"
    _seed_replay_row(db, user_id, rid)
    with db.transaction() as cur:
        rebuilt = ReplayLedger.rebuild_from_db(ledger_path, cur, user_id, now=NOW)

    entry = rebuilt.entry(rid)
    assert entry.rebuilt is True and entry.content_hash is None and entry.record is None
    assert rebuilt.state(_expanded(day="2026-04-03")) is LedgerState.DONE
    # …including a record whose content has since changed — unknowable, so still DONE
    assert rebuilt.state(_expanded(day="2026-04-03", confidence=0.1)) is LedgerState.DONE


def test_rebuild_groups_multiple_rows_per_record(db, user_id, ledger_path: Path):
    """One record may commit several memories; all of them are supersession targets."""
    rid = "blood-report.2026-03-25"
    first = _seed_replay_row(db, user_id, rid, day="2026-03-25", type="blood_report")
    second = _seed_replay_row(db, user_id, rid, day="2026-03-25", type="blood_report")

    with db.transaction() as cur:
        rebuilt = ReplayLedger.rebuild_from_db(ledger_path, cur, user_id, now=NOW)
    assert set(rebuilt.entry(rid).memory_ids) == {str(first), str(second)}


def test_rebuild_ignores_live_rows_and_other_users(db, user_id, ledger_path: Path):
    """Only `source='replay'` rows belonging to this user describe replay progress."""
    _seed_replay_row(db, user_id, "meal-pattern.2026-03-26.2026-04-24#2026-04-01")
    _seed_replay_row(db, user_id, "chat-should-be-ignored", source="chat")

    _seed_replay_row(db, new_user(), "meal-pattern.2026-03-26.2026-04-24#2026-04-09")

    with db.transaction() as cur:
        rebuilt = ReplayLedger.rebuild_from_db(ledger_path, cur, user_id, now=NOW)

    assert len(rebuilt) == 1
    assert "chat-should-be-ignored" not in rebuilt
    assert "meal-pattern.2026-03-26.2026-04-24#2026-04-09" not in rebuilt


def test_rebuild_replaces_rather_than_merges(db, user_id, ledger_path: Path):
    """The file is being replaced by a database-derived truth; merging would produce a ledger
    that is neither, retaining entries the database has no record of."""
    stale = ReplayLedger.load(ledger_path)
    stale.mark_done(_point(record_id="blood-report.2026-03-25"), [uuid.uuid4()], now=NOW)

    _seed_replay_row(db, user_id, "meal-pattern.2026-03-26.2026-04-24#2026-04-03")
    with db.transaction() as cur:
        rebuilt = ReplayLedger.rebuild_from_db(ledger_path, cur, user_id, now=NOW)

    assert "blood-report.2026-03-25" not in rebuilt
    assert len(ReplayLedger.load(ledger_path)) == 1


def test_rebuild_on_an_empty_account_is_empty(db, user_id, ledger_path: Path):
    with db.transaction() as cur:
        assert len(ReplayLedger.rebuild_from_db(ledger_path, cur, user_id, now=NOW)) == 0


def test_rebuild_scan_is_quick_at_dataset_scale(db, user_id, ledger_path: Path):
    """[→PERF] The real dataset is ~424 records; 2000 rows is comfortable headroom. This is a
    smoke check that rebuild is a single indexed scan, not an N+1.

    **Cleans up in `finally`, unlike the small-fixture tests around it.** The rows it seeds have
    NULL embeddings, and `cli/backfill.py --all` sweeps *every* user with a NULL-embedding gap —
    so leaving 2000 behind on the shared dev cluster measurably slows and destabilises
    `test_backfill.py::test_main_all_sweeps_users_with_gaps` (already flagged as flaky for
    exactly this reason in TODOS.md). Volume, not principle, is what makes this test different.
    """
    rows = 2000
    try:
        with db.transaction() as cur:
            cur.executemany(
                """
                INSERT INTO memories
                    (user_id, event_time, tz, type, source, provenance, confidence, status,
                     summary, payload)
                VALUES (%s, %s, %s, 'meal', 'replay', 'reconstructed', 0.6, 'active', %s, %s)
                """,
                [
                    (
                        user_id,
                        datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                        TZ,
                        f"perf {i}",
                        json.dumps(
                            {REPLAY_RECORD_ID_KEY: f"meal-pattern.2026-01-01.ongoing#p{i}"}
                        ),
                    )
                    for i in range(rows)
                ],
            )

        started = time.perf_counter()
        with db.transaction() as cur:
            rebuilt = ReplayLedger.rebuild_from_db(ledger_path, cur, user_id, now=NOW)
        elapsed = time.perf_counter() - started

        assert len(rebuilt) == rows
        assert elapsed < 30, f"rebuild of {rows} rows took {elapsed:.1f}s"
        print(f"\n[PERF] rebuild_from_db: {rows} rows in {elapsed:.2f}s")
    finally:
        with db.transaction() as cur:
            cur.execute("DELETE FROM memories WHERE user_id = %s", [user_id])


# ── entry serialization ───────────────────────────────────────────────────────────────────


def test_entry_json_roundtrip():
    entry = LedgerEntry(
        record_id="blood-report.2026-03-25",
        content_hash="sha256:abc",
        memory_ids=["0f9c"],
        ingested_at="2026-07-31T09:00:00Z",
        record={"record_id": "blood-report.2026-03-25"},
    )
    assert LedgerEntry.from_json(entry.to_json()) == entry


# ── the guard is wired in, not decorative ─────────────────────────────────────────────────


def test_state_rejects_a_tampered_id_before_it_can_be_reingested(ledger_path: Path):
    """The dangerous hand-edit: changing an already-committed record's id makes it look NEW,
    so the next run re-ingests it as a duplicate. state() runs for every record on every run,
    which is why the guard lives there rather than only at ingest."""
    ledger = ReplayLedger.load(ledger_path)
    record = _expanded()
    ledger.mark_done(record, [uuid.uuid4()], now=NOW)

    tampered = ReplayRecord(**{**record.__dict__, "record_id": "hand-edited-id"})
    with pytest.raises(LedgerError):
        ledger.state(tampered)


def test_mark_done_also_refuses_a_non_derivable_id(ledger_path: Path):
    ledger = ReplayLedger.load(ledger_path)
    bad = ReplayRecord(**{**_point().__dict__, "record_id": "nope"})
    with pytest.raises(LedgerError):
        ledger.mark_done(bad, [uuid.uuid4()], now=NOW)
    assert len(ReplayLedger.load(ledger_path)) == 0  # nothing was written
