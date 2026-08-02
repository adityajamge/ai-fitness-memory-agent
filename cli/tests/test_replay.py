"""Tests for the replay CLI's main loop (T8 M4).

Covers the full replay flow: NEW/DONE resume, the §4.10 halt threshold + failure artifact,
the §4.12 correction workflow, the §4.15 exit codes and advisory freshness check, and
--rebuild-ledger CLI wiring (ReplayLedger.rebuild_from_db itself is already fully tested at
the M2 level — cli/tests/test_replay_ledger.py — this file only proves the CLI calls it
correctly).

DB-backed, via cli/tests/conftest's real-CockroachDB `db`/`user_id` fixtures and
`FakeModelProvider`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import UUID

import pytest

import cli.replay as replay_cli
from cli.replay import (
    EXIT_FAILURES,
    EXIT_FATAL,
    EXIT_HALTED,
    EXIT_OK,
    HALT_THRESHOLD,
    ReplaySummary,
    _check_freshness,
    _default_ledger_path,
    run_replay,
)
from cli.replay_dataset import Manifest, ReplayRecord
from cli.replay_ledger import REPLAY_RECORD_ID_KEY, ReplayLedger
from cli.tests.conftest import DATABASE_URL, FakeModelProvider
from engine.config import Settings
from engine.ingestion import IngestionService
from engine.repository import get_memory

TZ = "Asia/Kolkata"


def _record(day: str, *, qty: float = 4, **kw) -> ReplayRecord:
    return ReplayRecord(
        record_id=kw.pop("record_id", f"meal.{day}"),
        type=kw.pop("type", "meal"),
        event_time=kw.pop("event_time", f"{day}T12:00:00+05:30"),
        tz=TZ,
        confidence=kw.pop("confidence", 0.9),
        payload=kw.pop("payload", {"items": [{"name": "egg", "qty": qty}]}),
        source_ref=kw.pop("source_ref", f"§2 Timeline :: {day}"),
        summary=kw.pop("summary", "eggs"),
        **kw,
    )


def _bad_record(day: str) -> ReplayRecord:
    """A record whose payload is guaranteed to fail engine/types.py validation."""
    return ReplayRecord(
        record_id=f"body-scan.{day}",
        type="body_scan",
        event_time=f"{day}T07:00:00+05:30",
        tz=TZ,
        confidence=0.8,
        payload={"body_fat_pct": "not-a-number"},
        source_ref=f"§2 Timeline :: {day}",
        summary="scan",
    )


def _service(db, provider) -> IngestionService:
    return IngestionService(db, provider, default_tz=TZ)


def _fetch(db, user_id, memory_id):
    with db.transaction() as cur:
        return get_memory(cur, user_id, memory_id)


def _row_count(db, user_id) -> int:
    with db.transaction() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM memories WHERE user_id = %(user_id)s",
            {"user_id": user_id},
        )
        return cur.fetchone()["n"]


def _run(svc, ledger, records, user_id, tmp_path: Path, **kw):
    kw.setdefault("failure_log_path", tmp_path / "failures.jsonl")
    return run_replay(svc, ledger, records, user_id, **kw)


# ── run_replay: NEW/DONE resume ──────────────────────────────────────────────────────────


def test_new_records_are_ingested_and_marked_done(db, user_id, tmp_path: Path) -> None:
    provider = FakeModelProvider()
    svc = _service(db, provider)
    ledger = ReplayLedger(tmp_path / "replay.ledger.jsonl")
    records = [_record("2026-03-25"), _record("2026-03-26"), _record("2026-03-27")]

    summary = _run(svc, ledger, records, user_id, tmp_path)

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


def test_expanded_record_lands_with_its_expanded_from_marker(db, user_id, tmp_path: Path) -> None:
    """The regression the first production replay shipped: all 399 expanded rows committed
    without `expanded_from`, so nothing in the database distinguished a materialized day of an
    asserted pattern from an observed event (§4.1 — "without that marker, expansion would
    violate ADR-4"). Asserted against the committed ROW, not the adapter, because that is
    where it was missing."""
    marker = {
        "period_start": "2026-03-26",
        "period_end": "2026-04-24",
        "cadence": "daily",
        "assertion": "4 eggs + 200g dahi daily",
        "composition": "meal-pattern.2026-03-26.2026-04-24",
    }
    expanded = _record(
        "2026-04-03",
        record_id="meal-pattern.2026-03-26.2026-04-24#2026-04-03",
        expanded_from=marker,
    )
    point = _record("2026-03-25")

    ledger = ReplayLedger(tmp_path / "replay.ledger.jsonl")
    _run(_service(db, FakeModelProvider()), ledger, [expanded, point], user_id, tmp_path)

    exp_row = _fetch(db, user_id, UUID(ledger.entry(expanded.record_id).memory_ids[0]))
    assert exp_row["payload"]["expanded_from"] == marker
    assert exp_row["payload"][REPLAY_RECORD_ID_KEY] == expanded.record_id  # both coexist

    pt_row = _fetch(db, user_id, UUID(ledger.entry(point.record_id).memory_ids[0]))
    assert "expanded_from" not in pt_row["payload"]  # observations stay unmarked


def test_second_run_skips_everything_and_writes_no_new_rows(db, user_id, tmp_path: Path) -> None:
    ledger_path = tmp_path / "replay.ledger.jsonl"
    records = [_record("2026-03-25"), _record("2026-03-26")]

    _run(_service(db, FakeModelProvider()), ReplayLedger(ledger_path), records, user_id, tmp_path)
    before = _row_count(db, user_id)

    second_ledger = ReplayLedger.load(ledger_path)
    summary = _run(_service(db, FakeModelProvider()), second_ledger, records, user_id, tmp_path)

    assert summary == ReplaySummary(new=0, skipped=2)
    assert _row_count(db, user_id) == before


def test_forced_double_run_produces_no_duplicate_rows(db, user_id, tmp_path: Path) -> None:
    """The P0 guard: re-running the exact same records against the same ledger file, as if
    the CLI had simply been invoked twice, never doubles a row."""
    ledger_path = tmp_path / "replay.ledger.jsonl"
    records = [_record("2026-03-25"), _record("2026-03-26"), _record("2026-03-27")]

    _run(
        _service(db, FakeModelProvider()),
        ReplayLedger.load(ledger_path),
        records,
        user_id,
        tmp_path,
    )
    after_first = _row_count(db, user_id)

    _run(
        _service(db, FakeModelProvider()),
        ReplayLedger.load(ledger_path),
        records,
        user_id,
        tmp_path,
    )
    after_second = _row_count(db, user_id)

    assert after_second == after_first == 3


def test_interrupted_run_resumes_without_reprocessing(db, user_id, tmp_path: Path) -> None:
    ledger_path = tmp_path / "replay.ledger.jsonl"
    records = [_record(f"2026-03-{d:02d}") for d in range(25, 31)]  # 6 records

    _run(
        _service(db, FakeModelProvider()),
        ReplayLedger.load(ledger_path),
        records[:3],
        user_id,
        tmp_path,
    )
    assert _row_count(db, user_id) == 3

    resumed = ReplayLedger.load(ledger_path)
    summary = _run(_service(db, FakeModelProvider()), resumed, records, user_id, tmp_path)

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
    _run(svc, ledger, [record], user_id, tmp_path)

    assert _row_count(db, user_id) == 2  # exactly one re-processed record, not more


def test_extract_calls_stay_zero(db, user_id, tmp_path: Path) -> None:
    """PROPERTY (§4.11): the direct-ingest path never calls extraction, even across a
    multi-record run, including one with a correction."""
    provider = FakeModelProvider()
    records = [_record("2026-03-25"), _record("2026-03-26")]
    ledger_path = tmp_path / "l.jsonl"
    _run(_service(db, provider), ReplayLedger(ledger_path), records, user_id, tmp_path)

    changed = [_record("2026-03-25", payload={"items": [{"name": "egg", "qty": 9}]})]
    _run(
        _service(db, provider),
        ReplayLedger.load(ledger_path),
        changed,
        user_id,
        tmp_path,
        apply_corrections=True,
    )

    assert provider.extract_calls == 0
    assert provider.embed_calls > 0


# ── correction workflow (§4.12) ─────────────────────────────────────────────────────────


def test_corrected_record_is_reported_but_not_applied_without_the_flag(
    db, user_id, tmp_path: Path, capsys
) -> None:
    ledger_path = tmp_path / "replay.ledger.jsonl"
    original = _record("2026-03-25")
    _run(
        _service(db, FakeModelProvider()),
        ReplayLedger.load(ledger_path),
        [original],
        user_id,
        tmp_path,
    )
    old_id = UUID(ReplayLedger.load(ledger_path).entry(original.record_id).memory_ids[0])
    before = _row_count(db, user_id)

    changed = _record("2026-03-25", payload={"items": [{"name": "egg", "qty": 6}]})
    summary = _run(
        _service(db, FakeModelProvider()),
        ReplayLedger.load(ledger_path),
        [changed],
        user_id,
        tmp_path,
    )

    assert summary == ReplaySummary(corrected_reported=1)
    assert _row_count(db, user_id) == before  # nothing written
    assert _fetch(db, user_id, old_id)["status"] == "active"  # untouched

    out = capsys.readouterr().out
    assert "CHANGED" in out
    assert "meal.2026-03-25" in out


def test_apply_corrections_supersedes_in_one_transaction(db, user_id, tmp_path: Path) -> None:
    ledger_path = tmp_path / "replay.ledger.jsonl"
    original = _record("2026-03-25")
    _run(
        _service(db, FakeModelProvider()),
        ReplayLedger.load(ledger_path),
        [original],
        user_id,
        tmp_path,
    )
    old_id = UUID(ReplayLedger.load(ledger_path).entry(original.record_id).memory_ids[0])

    changed = _record("2026-03-25", payload={"items": [{"name": "egg", "qty": 6}]})
    summary = _run(
        _service(db, FakeModelProvider()),
        ReplayLedger.load(ledger_path),
        [changed],
        user_id,
        tmp_path,
        apply_corrections=True,
    )

    assert summary == ReplaySummary(corrected_applied=1)
    old_row = _fetch(db, user_id, old_id)
    assert old_row["status"] == "superseded"

    new_ledger = ReplayLedger.load(ledger_path)
    new_id = UUID(new_ledger.entry(changed.record_id).memory_ids[0])
    assert old_row["superseded_by"] == new_id
    new_row = _fetch(db, user_id, new_id)
    assert new_row["status"] == "active"
    assert new_row["payload"]["items"][0]["qty"] == 6


def test_resume_after_correction_is_done_not_corrected_again(db, user_id, tmp_path: Path) -> None:
    ledger_path = tmp_path / "replay.ledger.jsonl"
    original = _record("2026-03-25")
    _run(
        _service(db, FakeModelProvider()),
        ReplayLedger.load(ledger_path),
        [original],
        user_id,
        tmp_path,
    )
    changed = _record("2026-03-25", payload={"items": [{"name": "egg", "qty": 6}]})
    _run(
        _service(db, FakeModelProvider()),
        ReplayLedger.load(ledger_path),
        [changed],
        user_id,
        tmp_path,
        apply_corrections=True,
    )
    before = _row_count(db, user_id)

    summary = _run(
        _service(db, FakeModelProvider()),
        ReplayLedger.load(ledger_path),
        [changed],
        user_id,
        tmp_path,
    )

    assert summary == ReplaySummary(skipped=1)
    assert _row_count(db, user_id) == before


# ── halt threshold + failure artifact (§4.10, §4.15) ────────────────────────────────────


def test_halts_after_five_consecutive_failures(db, user_id, tmp_path: Path) -> None:
    records = [_bad_record(f"2026-04-{d:02d}") for d in range(1, 8)]  # 7 bad records
    failure_log = tmp_path / "failures.jsonl"

    summary = run_replay(
        _service(db, FakeModelProvider()),
        ReplayLedger(tmp_path / "l.jsonl"),
        records,
        user_id,
        failure_log_path=failure_log,
    )

    assert summary.halted is True
    assert summary.failed == HALT_THRESHOLD  # halted at exactly 5, not all 7 attempted
    assert _row_count(db, user_id) == 0


def test_force_continues_past_the_threshold(db, user_id, tmp_path: Path) -> None:
    records = [_bad_record(f"2026-04-{d:02d}") for d in range(1, 8)]  # 7 bad records

    summary = _run(
        _service(db, FakeModelProvider()),
        ReplayLedger(tmp_path / "l.jsonl"),
        records,
        user_id,
        tmp_path,
        force=True,
    )

    assert summary.halted is False
    assert summary.failed == 7  # every record was attempted


def test_halt_counter_resets_on_any_non_failure_outcome(db, user_id, tmp_path: Path) -> None:
    """4 failures, 1 success, 4 more failures: 8 total failures, never 5 consecutive."""
    records = (
        [_bad_record(f"2026-04-{d:02d}") for d in range(1, 5)]
        + [_record("2026-05-01")]
        + [_bad_record(f"2026-04-{d:02d}") for d in range(10, 14)]
    )

    summary = _run(
        _service(db, FakeModelProvider()),
        ReplayLedger(tmp_path / "l.jsonl"),
        records,
        user_id,
        tmp_path,
    )

    assert summary.halted is False
    assert summary.failed == 8
    assert summary.new == 1


def test_failure_artifact_contains_every_required_field(db, user_id, tmp_path: Path) -> None:
    failure_log = tmp_path / "failures.jsonl"
    bad = _bad_record("2026-04-01")

    run_replay(
        _service(db, FakeModelProvider()),
        ReplayLedger(tmp_path / "l.jsonl"),
        [bad],
        user_id,
        failure_log_path=failure_log,
        force=True,
    )

    lines = failure_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["record_id"] == bad.record_id
    assert entry["record_number"] == 1
    assert entry["jsonl_line"] == 1
    assert entry["source_record"]["record_id"] == bad.record_id
    assert entry["constructed_payload"]["body_fat_pct"] == "not-a-number"
    assert entry["validation_errors"]  # non-empty, structured
    assert entry["source_ref"] == bad.source_ref


def test_ledger_error_from_a_tampered_record_id_counts_as_a_failure(
    db, user_id, tmp_path: Path
) -> None:
    """A record whose id disagrees with its own event_time is a LedgerError, caught and
    treated the same as a validation failure (§3's decision-tree note)."""
    tampered = _record("2026-03-25", record_id="meal.2026-03-26")  # date mismatch

    summary = _run(
        _service(db, FakeModelProvider()),
        ReplayLedger(tmp_path / "l.jsonl"),
        [tampered],
        user_id,
        tmp_path,
    )
    assert summary.failed == 1
    assert _row_count(db, user_id) == 0


# ── full end-to-end integration ──────────────────────────────────────────────────────────


def test_end_to_end_new_done_corrected_and_failure_in_one_run(
    db, user_id, tmp_path: Path
) -> None:
    ledger_path = tmp_path / "replay.ledger.jsonl"
    first_pass = [_record("2026-03-25"), _record("2026-03-26"), _bad_record("2026-03-27")]

    summary1 = _run(
        _service(db, FakeModelProvider()),
        ReplayLedger.load(ledger_path),
        first_pass,
        user_id,
        tmp_path,
    )
    assert summary1 == ReplaySummary(new=2, failed=1)
    assert _row_count(db, user_id) == 2

    second_pass = [
        _record("2026-03-25"),  # DONE
        _record("2026-03-26", payload={"items": [{"name": "egg", "qty": 99}]}),  # CORRECTED
    ]
    summary2 = _run(
        _service(db, FakeModelProvider()),
        ReplayLedger.load(ledger_path),
        second_pass,
        user_id,
        tmp_path,
        apply_corrections=True,
    )
    assert summary2 == ReplaySummary(skipped=1, corrected_applied=1)
    assert _row_count(db, user_id) == 3  # 2 original + 1 replacement (old one superseded)


# ── _check_freshness (pure, advisory only, §4.15) ───────────────────────────────────────


def _manifest(**overrides) -> Manifest:
    fields = {
        "dataset_version": "1",
        "converter_version": "1.0.0",
        "source_document": "does/not/exist.md",
        "source_document_sha256": "deadbeef",
        "payload_table": "also/does/not/exist.json",
        "payload_table_sha256": "deadbeef",
        "generated_at": "2026-07-30T00:00:00Z",
        "replay_cutover_date": "2026-07-01",
        "default_tz": TZ,
    }
    fields.update(overrides)
    return Manifest(**fields)


def test_freshness_check_silent_when_local_files_absent() -> None:
    assert _check_freshness(_manifest()) == []


def test_freshness_check_warns_on_hash_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "timeline.md"
    source.write_text("original content", encoding="utf-8")
    manifest = _manifest(source_document=str(source), source_document_sha256="wrong-hash")

    warnings = _check_freshness(manifest)
    assert len(warnings) == 1
    assert "source markdown" in warnings[0]


def test_freshness_check_silent_when_hash_matches(tmp_path: Path) -> None:
    import hashlib

    source = tmp_path / "timeline.md"
    source.write_text("original content", encoding="utf-8")
    real_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = _manifest(source_document=str(source), source_document_sha256=real_hash)

    assert _check_freshness(manifest) == []


# ── _default_ledger_path (pure) ─────────────────────────────────────────────────────────


def test_default_ledger_path_derivation() -> None:
    assert _default_ledger_path(Path("data/replay/dataset.jsonl")) == Path(
        "data/replay/dataset.ledger.jsonl"
    )


# ── main(): exit codes (§4.15) ───────────────────────────────────────────────────────────


@pytest.fixture()
def cli_env(db, monkeypatch, tmp_path: Path):
    monkeypatch.setattr(replay_cli, "load_settings", lambda: Settings(database_url=DATABASE_URL))
    monkeypatch.setattr(
        replay_cli, "build_default_provider", lambda settings: FakeModelProvider()
    )

    def run(*argv: str) -> int:
        monkeypatch.setattr(sys, "argv", ["cli.replay", *argv])
        with pytest.raises(SystemExit) as exc_info:
            replay_cli.main()
        return exc_info.value.code

    return run


def _write_dataset(tmp_path: Path, records: list[ReplayRecord]) -> Path:
    from cli.replay_dataset import write_dataset

    manifest = _manifest()
    jsonl_path = tmp_path / "dataset.jsonl"
    write_dataset(records, manifest, jsonl_path)
    return jsonl_path


def test_main_exit_ok_on_a_clean_run(user_id, tmp_path: Path, cli_env) -> None:
    dataset = _write_dataset(tmp_path, [_record("2026-03-25")])
    code = cli_env("--dataset", str(dataset), "--user", str(user_id))
    assert code == EXIT_OK


def test_main_exit_failures_when_some_records_fail_without_halting(
    user_id, tmp_path: Path, cli_env
) -> None:
    dataset = _write_dataset(tmp_path, [_record("2026-03-25"), _bad_record("2026-03-26")])
    code = cli_env("--dataset", str(dataset), "--user", str(user_id))
    assert code == EXIT_FAILURES


def test_main_exit_halted_at_the_threshold(user_id, tmp_path: Path, cli_env) -> None:
    records = [_bad_record(f"2026-04-{d:02d}") for d in range(1, 6)]  # exactly 5
    dataset = _write_dataset(tmp_path, records)
    code = cli_env("--dataset", str(dataset), "--user", str(user_id))
    assert code == EXIT_HALTED


def test_main_exit_fatal_on_missing_dataset(user_id, tmp_path: Path, cli_env) -> None:
    code = cli_env("--dataset", str(tmp_path / "nope.jsonl"), "--user", str(user_id))
    assert code == EXIT_FATAL


# ── main(): --rebuild-ledger ─────────────────────────────────────────────────────────────


def test_main_rebuild_ledger_recovers_state_and_exits_without_replaying(
    user_id, tmp_path: Path, cli_env, capsys
) -> None:
    records = [_record("2026-03-25"), _record("2026-03-26")]
    dataset = _write_dataset(tmp_path, records)
    ledger_path = _default_ledger_path(dataset)

    code = cli_env("--dataset", str(dataset), "--user", str(user_id))
    assert code == EXIT_OK
    ledger_path.unlink()  # simulate losing the ledger file

    code = cli_env("--rebuild-ledger", "--dataset", str(dataset), "--user", str(user_id))
    assert code == EXIT_OK
    assert "rebuilt ledger: 2 record(s) recovered" in capsys.readouterr().out

    rebuilt = ReplayLedger.load(ledger_path)
    for record in records:
        assert rebuilt.is_done(record.record_id)

    # A follow-up plain replay sees everything as DONE -- proving rebuild alone never wrote
    # any new memory rows.
    code = cli_env("--dataset", str(dataset), "--user", str(user_id))
    assert code == EXIT_OK
    assert "0 new, 2 skipped" in capsys.readouterr().out
