"""Tests for the reconstruction → JSONL converter (T8 M1, `python -m cli.convert`).

Covers the M1 block of docs/engineering/replay-architecture.md §7. Every fixture here is
**synthetic**: the real reconstruction (`docs/evidence/timeline-entries.md`) is gitignored
under ADR-7, so CI must be able to exercise the converter without it.

The load-bearing tests, in rough order of what they protect:

* **byte-determinism** — if regeneration isn't identical, §4.12's drift detection reports the
  whole dataset as changed and `--apply-corrections` would mass-supersede it (§5).
* **Rule 2 cadence** — expanding a weekly dose daily would claim 7x the real dose. That is
  fabricated data in the table the glass box invites judges to click into, not mere noise.
* **change markers** — the reconstruction states an ongoing behavior twice (§2 marker + §3
  period); converting both quantitatively double-counts the first day, which is the P0 class
  of bug this phase exists to prevent.
* **strict parsing** — a malformed entry raises rather than silently dropping a health event.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from cli import convert as convert_cli
from cli.replay_dataset import (
    DatasetError,
    PayloadEntry,
    dumps_canonical,
    load_payload_table,
)
from engine.types import validate_payload

CUTOVER = date(2026, 7, 1)
TZ = "Asia/Kolkata"


# ── fixtures ──────────────────────────────────────────────────────────────────────────────


def _md(*body: str) -> str:
    """Wrap entry lines in the reconstruction's section skeleton."""
    return "\n".join(body)


TIMELINE = _md(
    "## → §2 Timeline",
    "",
    "### 2026-03",
    "",
    "- **2026-03-25** (exact, E01) — blood-report: baseline panel [S]",
    "- **2026-03-26** (exact, memory) — meal-pattern: started 4 eggs/day — see §3 diet phases",
    "- **2026-03-28** (~week, memory) — note: second appointment [S]",
    "",
    "## → §3 Period facts",
    "",
    "### Diet phases",
    "",
    "- **2006-08-14 → 2026-03-26** (exact/exact, memory) — meal-pattern: strictly vegetarian.",
    "  Not eggetarian — no meat of any kind.",
    "- **2026-03-26 → 2026-03-30** (exact/exact, memory) — meal-pattern: 4 eggs daily",
    "",
    "### Supplement stacks",
    "",
    "- **2026-03-28 → 2026-04-25** (exact/exact, memory) — supplement: Vitamin D 60,000 IU weekly",
    "- **2026-06-24 → ongoing** (exact, memory) — medication: oral minoxidil 2.5mg",
    "",
    "### Sleep regimes",
    "",
    "- (none recorded)",
)

TABLE = {
    "blood-report.2026-03-25": {
        "summary": "Baseline blood report",
        "payload": {"panel": "baseline", "markers": {"vitamin_d_ng_ml": 6.2}},
    },
    "meal-pattern.2026-03-26.2026-03-30": {
        "cadence": "daily",
        "summary": "4 eggs",
        "payload": {
            "items": [{"name": "egg", "qty": 4}],
            "nutrition": {"protein_g": 24.0, "kcal": 280.0, "estimated": True},
        },
    },
    "supplement.2026-03-28.2026-04-25": {
        "cadence": "weekly",
        "summary": "Vitamin D 60,000 IU weekly",
        "payload": {"name": "Vitamin D", "dose_mg": 1.5, "dose_iu": 60000},
    },
    "medication.2026-06-24.ongoing": {
        "cadence": "daily",
        "summary": "Oral minoxidil 2.5mg",
        "payload": {"name": "oral minoxidil", "dose_mg": 2.5},
    },
}


@pytest.fixture
def table(tmp_path: Path):
    path = tmp_path / "payloads.json"
    path.write_text(json.dumps(TABLE), encoding="utf-8")
    return load_payload_table(path)


def _convert(markdown: str = TIMELINE, *, tbl=None, cutover: date = CUTOVER):
    return convert_cli.convert(markdown, tbl, cutover=cutover, tz=TZ)


def _by_id(records):
    return {r.record_id: r for r in records}


# ── determinism ───────────────────────────────────────────────────────────────────────────


def test_conversion_is_byte_deterministic(table):
    """Same markdown + same table → identical bytes. §4.12's drift detection depends on it."""
    first = "".join(dumps_canonical(r.to_json()) + "\n" for r in _convert(tbl=table))
    second = "".join(dumps_canonical(r.to_json()) + "\n" for r in _convert(tbl=table))
    assert first == second


def test_record_id_is_stable_across_rewording(table):
    """Rewording a description must not change any record_id — otherwise regenerating after a
    doc edit makes every record look new and re-ingests it (§4.3, the P0 duplicate path)."""
    reworded = TIMELINE.replace("4 eggs daily", "four (4) eggs, daily").replace(
        "baseline panel", "baseline blood panel"
    )
    assert set(_by_id(_convert(tbl=table))) == set(_by_id(_convert(reworded, tbl=table)))


# ── Rule 1: expand only quantified recurring assertions ───────────────────────────────────


def test_quantified_period_expands_per_day(table):
    meals = [r for r in _convert(tbl=table) if r.type == "meal"]
    assert [r.event_time[:10] for r in meals] == [
        "2026-03-26",
        "2026-03-27",
        "2026-03-28",
        "2026-03-29",
        "2026-03-30",
    ]


def test_background_state_is_one_note_not_a_meal(table):
    """The lifelong-vegetarian period asserts what was *absent* — no per-day quantity to sum.
    Writing it as `meal` would put a non-meal into every meal aggregate."""
    veg = [r for r in _convert(tbl=table) if "vegetarian" in (r.summary or "")]
    assert len(veg) == 1
    assert veg[0].type == "note"
    assert veg[0].expanded_from is None


def test_background_note_keeps_its_continuation_lines(table):
    veg = next(r for r in _convert(tbl=table) if "vegetarian" in (r.summary or ""))
    assert "no meat of any kind" in veg.payload["text"]


# ── Rule 2: expand at the assertion's own cadence ─────────────────────────────────────────


def test_weekly_cadence_does_not_expand_daily(table):
    """THE fabricated-dose guard. 2026-03-28 → 2026-04-25 is 29 days but 5 weekly doses;
    daily expansion would assert 7x the vitamin D actually taken."""
    doses = [r for r in _convert(tbl=table) if r.payload.get("dose_iu") == 60000]
    assert [r.event_time[:10] for r in doses] == [
        "2026-03-28",
        "2026-04-04",
        "2026-04-11",
        "2026-04-18",
        "2026-04-25",
    ]


def test_unknown_cadence_is_rejected(tmp_path: Path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"k": {"cadence": "fortnightly", "payload": {}}}), encoding="utf-8")
    with pytest.raises(DatasetError, match="unknown cadence"):
        load_payload_table(path)


# ── Rule 3: clamp at the live-logging cutover ─────────────────────────────────────────────


def test_ongoing_period_clamps_at_cutover(table):
    """An `ongoing` period expanded past the cutover would double-count days live logging owns."""
    mino = [r for r in _convert(tbl=table) if r.payload.get("name") == "oral minoxidil"]
    assert mino[0].event_time[:10] == "2026-06-24"
    assert mino[-1].event_time[:10] == "2026-06-30"  # the day before the cutover
    assert len(mino) == 7


def test_period_entirely_after_cutover_yields_nothing(table):
    """The minoxidil period starts 2026-06-24; against an earlier cutover it contributes no
    rows at all, while periods that ended before the cutover are unaffected."""
    records = _convert(tbl=table, cutover=date(2026, 6, 1))
    assert [r for r in records if r.payload.get("name") == "oral minoxidil"] == []
    assert [r for r in records if r.payload.get("dose_iu") == 60000]  # ended in April, kept


# ── the change-marker rule (prevents the double-count) ────────────────────────────────────


def test_section2_marker_covered_by_a_period_becomes_a_note(table):
    """§2 records "started 4 eggs/day" on the same day §3's period begins. Converting both
    quantitatively writes two meals on 2026-03-26 — the P0 double-count."""
    on_first_day = [
        r for r in _convert(tbl=table) if r.type == "meal" and r.event_time[:10] == "2026-03-26"
    ]
    assert len(on_first_day) == 1
    assert on_first_day[0].expanded_from is not None  # the period row, not the §2 marker

    marker = _by_id(_convert(tbl=table))["meal-pattern.2026-03-26"]
    assert marker.type == "note"
    assert "started 4 eggs/day" in marker.payload["text"]


def test_marker_rule_uses_containment_not_start_match(table):
    """A mid-period change marker (the reconstruction's "chicken added from 2026-06-23" sits
    inside the 06-15 → 07-05 phase) must also be recognised, so containment is the test."""
    md = TIMELINE.replace(
        "- **2026-03-28** (~week, memory) — note: second appointment [S]",
        "- **2026-03-28** (exact, memory) — meal-pattern: added more eggs mid-phase",
    ).replace("2026-03-26 → 2026-03-30", "2026-03-26 → 2026-03-30")
    marker = _by_id(_convert(md, tbl=table)).get("meal-pattern.2026-03-28")
    assert marker is not None and marker.type == "note"


# ── expanded_from marker + confidence ─────────────────────────────────────────────────────


def test_expanded_rows_carry_the_marker_point_events_do_not(table):
    records = _convert(tbl=table)
    for r in records:
        if r.type in {"meal", "supplement"}:
            assert r.expanded_from is not None, r.record_id
            assert r.expanded_from["cadence"] in {"daily", "weekly"}
            assert r.expanded_from["composition"] in TABLE
        if r.type == "blood_report":
            assert r.expanded_from is None


def test_expanded_rows_carry_lowered_confidence(table):
    """§4.1's honesty mechanism: an expanded row asserts a specific day drawn from a pattern,
    so it must be less certain than the point event it derives from."""
    records = _convert(tbl=table)
    point = next(r for r in records if r.type == "blood_report")
    expanded = next(r for r in records if r.type == "meal")
    assert expanded.confidence < point.confidence
    assert expanded.confidence == pytest.approx(0.95 * 0.7, abs=0.01)


def test_every_record_of_a_composition_shares_identical_macros(table):
    """One reviewed value per composition, not per record — per-day estimation would inject
    spurious variance into exactly the aggregate series the causal story rests on (§4.1)."""
    nutrition = {
        json.dumps(r.payload["nutrition"], sort_keys=True)
        for r in _convert(tbl=table)
        if r.type == "meal"
    }
    assert len(nutrition) == 1


# ── payload table contract ────────────────────────────────────────────────────────────────


def test_missing_payload_entry_is_an_explicit_error(tmp_path: Path):
    """A non-note type with no reviewed payload must halt, never emit a null-nutrition row."""
    path = tmp_path / "t.json"
    path.write_text(json.dumps({}), encoding="utf-8")
    with pytest.raises(DatasetError, match="no payload-table entry"):
        _convert(tbl=load_payload_table(path))


def test_entry_needs_exactly_one_of_payload_or_segments(tmp_path: Path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"k": {"cadence": "daily"}}), encoding="utf-8")
    with pytest.raises(DatasetError, match="exactly one of"):
        load_payload_table(path)


def test_segments_switch_payload_partway_through_a_period(tmp_path: Path):
    """The reconstruction's "chicken added from 2026-06-23" inside a running diet phase."""
    entry = PayloadEntry(
        segments=[
            {"from": "2026-03-26", "payload": {"protein_g": 24}},
            {"from": "2026-03-29", "payload": {"protein_g": 60}},
        ],
        cadence="daily",
    )
    assert entry.payload_for(date(2026, 3, 28))["protein_g"] == 24
    assert entry.payload_for(date(2026, 3, 29))["protein_g"] == 60
    assert entry.payload_for(date(2026, 3, 30))["protein_g"] == 60


def test_missing_payload_table_file_is_explicit(tmp_path: Path):
    with pytest.raises(DatasetError, match="payload table not found"):
        load_payload_table(tmp_path / "nope.json")


def test_malformed_payload_table_json_is_explicit(tmp_path: Path):
    path = tmp_path / "t.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(DatasetError, match="not valid JSON"):
        load_payload_table(path)


# ── type mapping ──────────────────────────────────────────────────────────────────────────


def test_medication_maps_to_supplement_with_prescription_category(table):
    mino = next(r for r in _convert(tbl=table) if r.payload.get("name") == "oral minoxidil")
    assert mino.type == "supplement"
    assert mino.payload["category"] == "prescription"


def test_nutritional_supplement_gets_its_own_category(table):
    vit_d = next(r for r in _convert(tbl=table) if r.payload.get("dose_iu") == 60000)
    assert vit_d.payload["category"] == "nutritional"


def test_meal_type_is_never_inferred(table):
    """The reconstruction never records breakfast/lunch/dinner; guessing one would be an
    invented fact inside a provenance='reconstructed' row (ADR-4)."""
    for r in _convert(tbl=table):
        if r.type == "meal":
            assert "meal_type" not in r.payload


def test_unknown_reconstruction_type_is_rejected(table):
    md = TIMELINE.replace("— note: second appointment", "— telepathy: second appointment")
    with pytest.raises(DatasetError, match="unknown reconstruction type"):
        _convert(md, tbl=table)


def test_all_emitted_payloads_validate_against_the_engine_registry(table):
    """The direct-ingest path treats a validation failure as fatal (§4.11), so a converter that
    emits an invalid payload would halt the replay run. Catch it here instead."""
    for r in _convert(tbl=table):
        validate_payload(r.type, r.payload)


# ── strict parsing ────────────────────────────────────────────────────────────────────────


def test_malformed_entry_raises_rather_than_being_skipped(table):
    md = TIMELINE.replace(
        "- **2026-03-25** (exact, E01) — blood-report: baseline panel [S]",
        "- **2026-03-25** (exact E01) — blood-report: baseline panel [S]",  # missing comma
    )
    with pytest.raises(DatasetError, match="refusing to skip it silently"):
        _convert(md, tbl=table)


def test_narrative_bullets_are_ignored(table):
    """`- (none recorded)` under §3 Sleep regimes is prose, not a malformed entry."""
    assert any(r.type == "meal" for r in _convert(tbl=table))  # parsed past it without raising


def test_non_entry_sections_are_not_parsed(table):
    md = TIMELINE + _md(
        "",
        "## → §4 Evidence locker",
        "",
        "- **2026-01-01** (exact, memory) — note: should not be converted",
    )
    assert "note.2026-01-01" not in _by_id(_convert(md, tbl=table))


def test_source_ref_records_section_provenance(table):
    records = _by_id(_convert(tbl=table))
    assert records["blood-report.2026-03-25"].source_ref == "§2 Timeline :: 2026-03-25"
    assert (
        records["meal-pattern.2026-03-26.2026-03-30#2026-03-27"].source_ref
        == "§3 Diet phases :: 2026-03-26 → 2026-03-30"
    )


# ── CLI surface ───────────────────────────────────────────────────────────────────────────


def test_cli_writes_jsonl_and_manifest(tmp_path: Path):
    src, pay, out = tmp_path / "s.md", tmp_path / "p.json", tmp_path / "d.jsonl"
    src.write_text(TIMELINE, encoding="utf-8")
    pay.write_text(json.dumps(TABLE), encoding="utf-8")

    convert_cli.main(
        ["--source", str(src), "--payloads", str(pay), "--out", str(out), "--cutover", "2026-07-01"]
    )

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 1
    assert all(json.loads(ln)["record_id"] for ln in lines)  # homogeneous: every line a record

    manifest = json.loads((tmp_path / "d.manifest.json").read_text(encoding="utf-8"))
    assert manifest["replay_cutover_date"] == "2026-07-01"
    assert manifest["source_document_sha256"] and manifest["payload_table_sha256"]


def test_cli_refuses_to_overwrite_without_force(tmp_path: Path):
    """Regenerating over a reviewed dataset discards hand-edits (§4.12) — it takes intent."""
    src, pay, out = tmp_path / "s.md", tmp_path / "p.json", tmp_path / "d.jsonl"
    src.write_text(TIMELINE, encoding="utf-8")
    pay.write_text(json.dumps(TABLE), encoding="utf-8")
    argv = ["--source", str(src), "--payloads", str(pay), "--out", str(out)]
    argv += ["--cutover", "2026-07-01"]

    convert_cli.main(argv)
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        convert_cli.main(argv)
    convert_cli.main([*argv, "--force"])  # explicit intent succeeds


def test_manifest_hashes_track_input_changes(tmp_path: Path):
    """The manifest hashes converter *inputs*, never the JSONL — that's what makes "is this
    dataset stale?" answerable without fighting the hand-edit workflow (§4.1)."""
    src, pay = tmp_path / "s.md", tmp_path / "p.json"
    src.write_text(TIMELINE, encoding="utf-8")
    pay.write_text(json.dumps(TABLE), encoding="utf-8")
    before = convert_cli.build_manifest(src, pay, cutover=CUTOVER, tz=TZ)

    src.write_text(TIMELINE + "\n", encoding="utf-8")
    after = convert_cli.build_manifest(src, pay, cutover=CUTOVER, tz=TZ)

    assert before.source_document_sha256 != after.source_document_sha256
    assert before.payload_table_sha256 == after.payload_table_sha256


def test_month_buckets_never_leak_into_source_ref(table):
    """§2's month headings are date buckets, including the annotated final one
    (`### 2026-07 (current — live logging takes over from here)`). Every §2 entry must carry
    the same provenance prefix regardless of which bucket it sits under."""
    md = TIMELINE.replace(
        "### 2026-03", "### 2026-03 (current — live logging takes over from here)"
    )
    refs = {r.source_ref.split(" :: ")[0] for r in _convert(md, tbl=table) if r.type != "meal"}
    assert refs == {"§2 Timeline", "§3 Diet phases", "§3 Supplement stacks"}


def test_subsection_sensitivity_marker_is_stripped(table):
    """`[S]` marks a heading as sensitive; it is metadata, not part of the section's name."""
    md = TIMELINE.replace("### Supplement stacks", "### Supplement stacks  [S]")
    refs = {r.source_ref for r in _convert(md, tbl=table)}
    assert not any("[S]" in ref for ref in refs)
    assert any(ref.startswith("§3 Supplement stacks :: ") for ref in refs)
