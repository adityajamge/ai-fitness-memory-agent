"""Tests for the M4 additions to cli/replay_dataset.py — the JSONL/manifest reader and the
ReplayRecord -> ExtractedEvent adapter (T8 M4, docs/engineering/replay-architecture.md §6/§4.14).

Pure and DB-free by design, same posture as M1: these are data-layer functions, so nothing
here needs CockroachDB or a model provider.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cli.replay_dataset import (
    DatasetError,
    Manifest,
    ReplayRecord,
    load_manifest,
    read_dataset,
    write_dataset,
)

IST = timezone(timedelta(hours=5, minutes=30))


def _record(**overrides: object) -> ReplayRecord:
    fields: dict[str, object] = {
        "record_id": "meal.2026-03-26",
        "type": "meal",
        "event_time": "2026-03-26T08:00:00+05:30",
        "tz": "Asia/Kolkata",
        "confidence": 0.9,
        "payload": {"items": [{"name": "egg", "qty": 4}]},
        "source_ref": "§2 Timeline :: 2026-03-26",
        "summary": "4 eggs",
    }
    fields.update(overrides)
    return ReplayRecord(**fields)  # type: ignore[arg-type]


def _manifest(**overrides: object) -> Manifest:
    fields: dict[str, object] = {
        "dataset_version": "1",
        "converter_version": "1.0.0",
        "source_document": "docs/evidence/timeline-entries.md",
        "source_document_sha256": "abc123",
        "payload_table": "docs/evidence/replay_payloads.json",
        "payload_table_sha256": "def456",
        "generated_at": "2026-07-30T00:00:00Z",
        "replay_cutover_date": "2026-07-01",
        "default_tz": "Asia/Kolkata",
    }
    fields.update(overrides)
    return Manifest(**fields)  # type: ignore[arg-type]


# ── read_dataset / load_manifest: round trip with write_dataset ────────────────────────────


def test_read_dataset_round_trips_write_dataset(tmp_path: Path) -> None:
    records = [
        _record(),
        _record(record_id="meal.2026-03-27", event_time="2026-03-27T08:00:00+05:30"),
    ]
    manifest = _manifest()
    jsonl_path = tmp_path / "dataset.jsonl"
    write_dataset(records, manifest, jsonl_path)

    read_back = read_dataset(jsonl_path)
    assert [r.to_json() for r in read_back] == [r.to_json() for r in records]

    loaded_manifest = load_manifest(jsonl_path)
    assert loaded_manifest == manifest


def test_read_dataset_skips_blank_lines(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "dataset.jsonl"
    write_dataset([_record()], _manifest(), jsonl_path)
    jsonl_path.write_text(jsonl_path.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
    assert len(read_dataset(jsonl_path)) == 1


def test_read_dataset_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(DatasetError):
        read_dataset(tmp_path / "nope.jsonl")


def test_read_dataset_malformed_json_raises(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "dataset.jsonl"
    jsonl_path.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(DatasetError):
        read_dataset(jsonl_path)


def test_read_dataset_missing_field_raises(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "dataset.jsonl"
    jsonl_path.write_text('{"record_id": "meal.2026-03-26"}\n', encoding="utf-8")
    with pytest.raises(DatasetError):
        read_dataset(jsonl_path)


def test_load_manifest_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(DatasetError):
        load_manifest(tmp_path / "dataset.jsonl")


def test_load_manifest_malformed_json_raises(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "dataset.jsonl"
    jsonl_path.with_suffix(".manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(DatasetError):
        load_manifest(jsonl_path)


def test_load_manifest_missing_field_raises(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "dataset.jsonl"
    jsonl_path.with_suffix(".manifest.json").write_text(
        '{"dataset_version": "1"}', encoding="utf-8"
    )
    with pytest.raises(DatasetError):
        load_manifest(jsonl_path)


# ── ReplayRecord.as_extracted_event ─────────────────────────────────────────────────────────


def test_as_extracted_event_converts_fields() -> None:
    record = _record()
    event = record.as_extracted_event()

    assert event.type == "meal"
    assert event.event_time == datetime(2026, 3, 26, 8, 0, tzinfo=IST)
    assert event.tz == "Asia/Kolkata"
    assert event.confidence == pytest.approx(0.9)
    assert event.summary == "4 eggs"
    assert event.payload == {"items": [{"name": "egg", "qty": 4}]}


def test_as_extracted_event_no_summary_raises() -> None:
    record = _record(summary=None)
    with pytest.raises(DatasetError):
        record.as_extracted_event()


def test_as_extracted_event_extra_payload_merges_without_mutating_original() -> None:
    record = _record()
    event = record.as_extracted_event(extra_payload={"replay_record_id": "meal.2026-03-26"})

    assert event.payload["replay_record_id"] == "meal.2026-03-26"
    assert event.payload["items"] == record.payload["items"]
    assert "replay_record_id" not in record.payload  # the original is untouched


def test_as_extracted_event_carries_expanded_from_into_the_payload() -> None:
    """§4.1's honesty mechanism is TWO signals — lowered confidence *and* this marker.

    `expanded_from` is a sibling of `payload` on the record, has no database column, and has
    no ExtractedEvent field, so it must ride inline in the payload or it vanishes silently.
    It did vanish, for all 399 expanded rows of the first production replay: the rows were
    factually correct but overstated what was observed, because nothing distinguished a
    materialized day of an asserted pattern from a logged event.
    """
    marker = {
        "period_start": "2026-03-26",
        "period_end": "2026-04-24",
        "cadence": "daily",
        "assertion": "4 eggs + 200g dahi daily",
        "composition": "meal-pattern.2026-03-26.2026-04-24",
    }
    record = _record(record_id="meal-pattern.2026-03-26.2026-04-24#2026-04-03",
                     expanded_from=marker)
    event = record.as_extracted_event()

    assert event.payload["expanded_from"] == marker
    assert event.payload["items"] == record.payload["items"]  # facts untouched
    assert "expanded_from" not in record.payload  # the record itself is not mutated


def test_as_extracted_event_omits_expanded_from_for_point_events() -> None:
    """A point event is a real observation — it must NOT be marked as pattern-derived."""
    event = _record().as_extracted_event()
    assert "expanded_from" not in event.payload


def test_expanded_from_coexists_with_the_replay_record_id_stamp() -> None:
    """Both markers ride in the same payload; neither may clobber the other."""
    marker = {"cadence": "daily", "composition": "meal-pattern.2026-03-26.2026-04-24"}
    record = _record(record_id="meal-pattern.2026-03-26.2026-04-24#2026-04-03",
                     expanded_from=marker)
    event = record.as_extracted_event(
        extra_payload={"replay_record_id": record.record_id}
    )
    assert event.payload["expanded_from"] == marker
    assert event.payload["replay_record_id"] == record.record_id


def test_as_extracted_event_payload_is_never_the_same_object() -> None:
    """Even with no extra_payload, the returned dict must not alias self.payload -- a caller
    mutating the event's payload (e.g. to stamp a key in later) must never corrupt the record
    the ledger will hash and snapshot."""
    record = _record()
    event = record.as_extracted_event()
    event.payload["mutated"] = True
    assert "mutated" not in record.payload
