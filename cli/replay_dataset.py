"""The replay dataset contract: records, manifest, and the reviewed payload table (T8 M1).

This is the data layer shared by the whole Phase 4 toolchain — `cli/convert.py` writes it,
and the resume ledger and replay loop (M2/M4) read it. It deliberately contains no parsing
and no I/O beyond reading its own artifacts.

Design: docs/engineering/replay-architecture.md §4.1 (dataset format), §4.3 (record_id).
The three artifacts and who owns them:

    timeline-entries.md   canonical for FACTS      human-authored, local-only (ADR-7)
    replay_payloads.json  canonical for PAYLOADS   reviewed dev-time artifact
    <dataset>.jsonl       canonical for WHAT RAN   generated; regenerable at any time

**Why a payload table exists.** Exact values — lab markers, device metrics, doses, meal
macros — cannot be derived from the reconstruction's prose without inference, and a silent
transcription error in (say) the Vitamin D 6.20 → 38.4 pair would break M5's verification in
the least detectable way possible. They are transcribed once, reviewed once, and joined by key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from engine.model import ExtractedEvent

__all__ = [
    "TYPE_MAP",
    "CERTAINTY_CONFIDENCE",
    "EXPANSION_CONFIDENCE_FACTOR",
    "DEFAULT_EVENT_HOUR",
    "CADENCE_DAYS",
    "Manifest",
    "PayloadEntry",
    "PayloadTable",
    "ReplayRecord",
    "DatasetError",
    "load_payload_table",
    "load_manifest",
    "read_dataset",
    "write_dataset",
    "dumps_canonical",
]


class DatasetError(ValueError):
    """Raised for any malformed dataset artifact. Never a silent skip — a converter or table
    problem must surface loudly (§4.10: a bad input means bad data in hundreds of records)."""


# ── conversion constants (all deterministic, all documented) ──────────────────────────────

#: Reconstruction type → registered engine type (docs/…/replay-architecture.md §4.1).
#: `medication` folds into `supplement` with `category="prescription"` so the dose stays
#: structured; `illness`/`habit` are dated life events, not quantitative series.
TYPE_MAP: dict[str, str] = {
    "note": "note",
    "blood-report": "blood_report",
    "body-scan": "body_scan",
    # A bare weigh-in, distinct from the composition scan that may accompany it. Kept separate
    # because the consolidatable `weight_kg` series reads `weight` rows while `body_fat_pct`
    # reads `body_scan` rows (engine/retrieval.py METRICS) — folding weigh-ins into body scans
    # would leave the weight series empty, and writing the same number into both types would
    # let one measurement be counted twice.
    "weight": "weight",
    "meal-pattern": "meal",
    "workout-pattern": "workout",
    "sleep-pattern": "sleep",
    "supplement": "supplement",
    "medication": "supplement",
    "illness": "note",
    "habit": "note",
}

#: Reconstruction types that carry a `category` discriminator on the engine payload, so a
#: prescription stays separable from a nutritional supplement (§4.1 — `supplement` is
#: extended in writing rather than silently overloaded).
CATEGORY_MAP: dict[str, str] = {
    "supplement": "nutritional",
    "medication": "prescription",
}

#: The reconstruction's certainty vocabulary → a confidence float. This is the only place
#: the markdown's `(exact, memory)` annotation becomes a number; a payload-table entry may
#: override it.
CERTAINTY_CONFIDENCE: dict[str, float] = {
    "exact": 0.95,
    "~week": 0.80,
    "~month": 0.60,
    "n/a": 0.50,
}

#: Expanded period rows assert a *specific day* drawn from an asserted *pattern*, so they are
#: less certain than the point events they derive from (§4.1 honesty mechanism — this factor
#: and the `expanded_from` marker are what keep expansion ADR-4-compliant).
EXPANSION_CONFIDENCE_FACTOR = 0.7

#: The reconstruction records dates, not times. Noon local is a neutral, documented default;
#: a payload-table entry may pin a real time where one is known (e.g. the 10:15 body scan).
DEFAULT_EVENT_HOUR = 12

#: Rule 2 (§4.1): expand at the assertion's OWN cadence. Expanding a weekly 60,000 IU dose
#: daily would claim 7x the real dose — fabricated data, not merely noise.
CADENCE_DAYS: dict[str, int] = {"daily": 1, "weekly": 7, "biweekly": 14}


# ── artifacts ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Manifest:
    """Sidecar metadata for one generated dataset (§4.1).

    Hashes converter *inputs*, never the JSONL: hashing the output would fight the review
    workflow (§4.12), where hand-editing a generated record is a documented escape hatch.
    Hashing the inputs answers the question that actually matters — *have my sources changed
    since this was generated?*
    """

    dataset_version: str
    converter_version: str
    source_document: str
    source_document_sha256: str
    payload_table: str
    payload_table_sha256: str
    generated_at: str
    replay_cutover_date: str
    default_tz: str

    def to_json(self) -> dict[str, Any]:
        return {
            "dataset_version": self.dataset_version,
            "converter_version": self.converter_version,
            "source_document": self.source_document,
            "source_document_sha256": self.source_document_sha256,
            "payload_table": self.payload_table,
            "payload_table_sha256": self.payload_table_sha256,
            "generated_at": self.generated_at,
            "replay_cutover_date": self.replay_cutover_date,
            "default_tz": self.default_tz,
        }


@dataclass(frozen=True)
class ReplayRecord:
    """One line of the dataset — a fully typed, ready-to-ingest event.

    There is no free-text variant: every record carries a validated payload and takes the
    direct-ingest path (§4.11), which is what makes replay zero-extraction.
    """

    record_id: str
    type: str  # a REGISTERED engine type (engine/types.py)
    event_time: str  # ISO-8601 with offset
    tz: str
    confidence: float
    payload: dict[str, Any]
    source_ref: str
    expanded_from: dict[str, Any] | None = None
    summary: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "record_id": self.record_id,
            "type": self.type,
            "event_time": self.event_time,
            "tz": self.tz,
            "confidence": self.confidence,
            "payload": self.payload,
            "source_ref": self.source_ref,
        }
        if self.summary is not None:
            out["summary"] = self.summary
        if self.expanded_from is not None:
            out["expanded_from"] = self.expanded_from
        return out

    def as_extracted_event(self, *, extra_payload: dict[str, Any] | None = None) -> ExtractedEvent:
        """Convert to the shape ``IngestionService.ingest_events``/``ingest_events_superseding``
        accept (T8 M4, docs/engineering/replay-architecture.md §3's per-record contract).

        This module stays ledger-agnostic on purpose: stamping
        ``cli.replay_ledger.REPLAY_RECORD_ID_KEY`` into the payload is the caller's job (pass it
        via ``extra_payload``), not this method's — importing ``cli.replay_ledger`` here would
        cycle back, since that module already imports from this one. ``extra_payload`` is merged
        into a **copy** of ``self.payload``, so the returned event never aliases (and a caller
        mutating it can never corrupt) this record's own payload — which matters because the
        ledger later hashes and snapshots ``self`` unchanged.
        """
        if self.summary is None:
            raise DatasetError(f"record {self.record_id!r} has no summary; cannot ingest")

        # `expanded_from` is a sibling of `payload` on the record but has no column and no
        # ``ExtractedEvent`` field, so it rides **inline in the payload** — the only place it
        # can live (ADR-13.6's extra="allow" makes that migration-free). Carrying it is not
        # optional: §4.1's honesty mechanism is *two* signals, lowered confidence AND this
        # marker, and "without that marker, expansion would violate ADR-4". It is what lets a
        # citation chip say "one day of an asserted pattern spanning 2026-03-26 → 2026-04-24"
        # instead of "you logged this meal". Omitting it silently (as this adapter first did)
        # produces rows that are factually right but overstate what was actually observed.
        payload: dict[str, Any] = {**self.payload}
        if self.expanded_from is not None:
            payload["expanded_from"] = self.expanded_from
        payload.update(extra_payload or {})

        return ExtractedEvent(
            type=self.type,
            event_time=datetime.fromisoformat(self.event_time),
            tz=self.tz,
            confidence=self.confidence,
            summary=self.summary,
            payload=payload,
        )


@dataclass(frozen=True)
class PayloadEntry:
    """One reviewed payload-table entry, keyed by a converter-derived payload key.

    ``cadence`` is what makes Rules 1 and 2 (§4.1) **data-driven and reviewable** rather than
    inferred from prose: an entry with no cadence is a background state or a qualitative
    assertion and yields exactly one record; an entry with a cadence is a recurring quantified
    event and expands at that cadence.

    ``segments`` handles a period whose composition changes partway through (the reconstruction's
    "100–150g chicken added from 2026-06-23" inside the 06-15 → 07-05 diet phase). Each segment
    applies from its ``from`` date until the next one starts.
    """

    payload: dict[str, Any] | None = None
    segments: list[dict[str, Any]] = field(default_factory=list)
    cadence: str | None = None
    confidence: float | None = None
    summary: str | None = None
    event_time: str | None = None  # "HH:MM" local, when a real time is known
    note: str | None = None  # provenance / review commentary; never reaches the database

    def payload_for(self, on: date) -> dict[str, Any]:
        """The payload applicable on ``on`` — constant, or the latest segment that has started."""
        if self.payload is not None:
            return self.payload
        applicable = [s for s in self.segments if date.fromisoformat(s["from"]) <= on]
        if not applicable:
            raise DatasetError(f"no payload segment applies on {on.isoformat()}")
        return max(applicable, key=lambda s: s["from"])["payload"]

    def summary_for(self, on: date) -> str | None:
        if self.payload is not None or not self.segments:
            return self.summary
        applicable = [s for s in self.segments if date.fromisoformat(s["from"]) <= on]
        if not applicable:
            return self.summary
        return max(applicable, key=lambda s: s["from"]).get("summary", self.summary)


PayloadTable = dict[str, PayloadEntry]


# ── loading & serialization ───────────────────────────────────────────────────────────────


def load_payload_table(path: Path) -> PayloadTable:
    """Read and structurally validate the reviewed payload table.

    Validation is strict on purpose: an entry that is malformed here becomes malformed data in
    every record it covers, and the direct-ingest path treats a bad payload as fatal (§4.11).
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DatasetError(f"payload table not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DatasetError(f"payload table is not valid JSON ({path}): {exc}") from exc

    if not isinstance(raw, dict):
        raise DatasetError("payload table must be a JSON object keyed by payload key")

    table: PayloadTable = {}
    for key, value in raw.items():
        if key.startswith("$"):  # reserved for file-level commentary ("$schema", "$note")
            continue
        if not isinstance(value, dict):
            raise DatasetError(f"payload table entry {key!r} must be an object")

        payload = value.get("payload")
        segments = value.get("segments", [])
        if (payload is None) == (not segments):
            raise DatasetError(
                f"payload table entry {key!r} must have exactly one of 'payload' or 'segments'"
            )
        for seg in segments:
            if "from" not in seg or "payload" not in seg:
                raise DatasetError(f"segment in {key!r} needs both 'from' and 'payload'")
            date.fromisoformat(seg["from"])  # raises on a malformed date

        cadence = value.get("cadence")
        if cadence is not None and cadence not in CADENCE_DAYS:
            raise DatasetError(
                f"payload table entry {key!r} has unknown cadence {cadence!r} "
                f"(expected one of {sorted(CADENCE_DAYS)})"
            )

        table[key] = PayloadEntry(
            payload=payload,
            segments=segments,
            cadence=cadence,
            confidence=value.get("confidence"),
            summary=value.get("summary"),
            event_time=value.get("event_time"),
            note=value.get("note"),
        )
    return table


def dumps_canonical(obj: Any) -> str:
    """Serialize deterministically — sorted keys, no incidental whitespace, UTF-8 preserved.

    Byte-determinism is load-bearing (§5): if regeneration is not byte-identical, every rerun
    reports the whole dataset as 'changed' (§4.12) and an --apply-corrections run would
    mass-supersede it.
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def write_dataset(records: list[ReplayRecord], manifest: Manifest, jsonl_path: Path) -> Path:
    """Write the JSONL + its sidecar manifest. Returns the manifest path.

    The JSONL stays **homogeneous** — every line is a record, no header — so `wc -l`, `grep`,
    `jq`, and hand-editing all keep working (§4.1).
    """
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(dumps_canonical(r.to_json()) + "\n" for r in records)
    jsonl_path.write_text(body, encoding="utf-8", newline="\n")

    manifest_path = jsonl_path.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest.to_json(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest_path


def load_manifest(jsonl_path: Path) -> Manifest:
    """Load the sidecar manifest for ``jsonl_path`` (§4.1's `<dataset>.manifest.json`
    convention — the counterpart to ``write_dataset``'s own naming)."""
    manifest_path = jsonl_path.with_suffix(".manifest.json")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DatasetError(f"manifest not found: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise DatasetError(f"manifest is not valid JSON ({manifest_path}): {exc}") from exc

    try:
        return Manifest(
            dataset_version=raw["dataset_version"],
            converter_version=raw["converter_version"],
            source_document=raw["source_document"],
            source_document_sha256=raw["source_document_sha256"],
            payload_table=raw["payload_table"],
            payload_table_sha256=raw["payload_table_sha256"],
            generated_at=raw["generated_at"],
            replay_cutover_date=raw["replay_cutover_date"],
            default_tz=raw["default_tz"],
        )
    except KeyError as exc:
        raise DatasetError(f"{manifest_path} missing required field {exc}") from exc


def read_dataset(jsonl_path: Path) -> list[ReplayRecord]:
    """Read a JSONL dataset written by ``write_dataset`` (T8 M4). Strict on purpose — a
    malformed line here means bad data reaching hundreds of records downstream (§4.10), so it
    is never a silent skip.

    The file is homogeneous (§4.1: "every line a record, no header"), so this is just parse +
    validate per line; no manifest-line special case to worry about.
    """
    try:
        text = jsonl_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DatasetError(f"dataset not found: {jsonl_path}") from exc

    records: list[ReplayRecord] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{jsonl_path}:{lineno} is not valid JSON: {exc}") from exc
        try:
            records.append(
                ReplayRecord(
                    record_id=raw["record_id"],
                    type=raw["type"],
                    event_time=raw["event_time"],
                    tz=raw["tz"],
                    confidence=raw["confidence"],
                    payload=raw["payload"],
                    source_ref=raw["source_ref"],
                    expanded_from=raw.get("expanded_from"),
                    summary=raw.get("summary"),
                )
            )
        except KeyError as exc:
            raise DatasetError(f"{jsonl_path}:{lineno} missing required field {exc}") from exc
    return records


def event_time_for(on: date, tz: str, hhmm: str | None) -> str:
    """Compose an ISO-8601 event_time in the dataset's timezone (ADR-5: event_time is when it
    happened; created_at, set by the database, is when we learned it)."""
    if hhmm is None:
        clock = time(hour=DEFAULT_EVENT_HOUR)
    else:
        hour, _, minute = hhmm.partition(":")
        clock = time(hour=int(hour), minute=int(minute or 0))
    return datetime.combine(on, clock, tzinfo=ZoneInfo(tz)).isoformat()


def occurrences(start: date, end: date, cadence: str) -> list[date]:
    """Every date the assertion applies to, at its own cadence (Rule 2)."""
    step = timedelta(days=CADENCE_DAYS[cadence])
    out: list[date] = []
    cursor = start
    while cursor <= end:
        out.append(cursor)
        cursor += step
    return out
