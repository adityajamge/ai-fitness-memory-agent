"""Reconstruction markdown + reviewed payload table → replay JSONL (T8 M1).

    python -m cli.convert --source docs/evidence/timeline-entries.md \
                          --payloads docs/evidence/replay_payloads.json \
                          --out data/replay/history.jsonl \
                          --cutover 2026-07-01

Design: docs/engineering/replay-architecture.md §4.1. This is a **pure** step — no database,
no model, no network. It runs once (and again on any correction), and its output is reviewed
by a human before `cli/replay.py` ever touches it (§4.12).

**What this parser does and does not do.** It reads *structure* from the markdown — dates,
types, certainty, cadence anchors, section provenance — and nothing else. Every precise value
(lab markers, doses, macros, device metrics) is looked up from the reviewed payload table by a
derived key. The prose descriptions become `note` text and `summary` strings verbatim; they are
never mined for numbers. That boundary is deliberate: a regex that silently mistranscribes
`6.20` would corrupt exactly the value M5 verifies.

**Byte-determinism is a guarantee, not an aspiration** (§5): the same markdown plus the same
payload table must produce an identical JSONL, or §4.12's drift detection reports the whole
dataset as changed and `--apply-corrections` would mass-supersede it.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cli.replay_dataset import (
    CATEGORY_MAP,
    CERTAINTY_CONFIDENCE,
    EXPANSION_CONFIDENCE_FACTOR,
    TYPE_MAP,
    DatasetError,
    Manifest,
    PayloadEntry,
    PayloadTable,
    ReplayRecord,
    event_time_for,
    load_payload_table,
    occurrences,
    write_dataset,
)

CONVERTER_VERSION = "1.0.0"
DATASET_VERSION = "1"

# ── markdown grammar ──────────────────────────────────────────────────────────────────────
# The reconstruction is human-readable prose with a regular entry line. We anchor on that line
# shape only; anything that doesn't match is skipped as narrative (with §2/§3 entry lines
# guarded by the strict-parse check in _parse, so a malformed entry can never vanish silently).

_SECTION_RE = re.compile(r"^##\s+→\s+§(?P<num>\d+)\s+(?P<title>.+?)\s*$")
_SUBSECTION_RE = re.compile(r"^###\s+(?P<title>.+?)\s*$")

_POINT_RE = re.compile(
    r"^- \*\*(?P<day>\d{4}-\d{2}-\d{2})\*\*\s+"
    r"\((?P<certainty>[^,)]+),\s*(?P<evidence>[^)]+)\)\s*"
    r"—\s*(?P<rtype>[a-z][a-z-]*)\s*:\s*(?P<desc>.*)$"
)
_PERIOD_RE = re.compile(
    r"^- \*\*(?P<start>\d{4}-\d{2}-\d{2})\s*→\s*(?P<end>\d{4}-\d{2}-\d{2}|ongoing)\*\*\s+"
    r"\((?P<certainty>[^,)]+),\s*(?P<evidence>[^)]+)\)\s*"
    r"—\s*(?P<rtype>[a-z][a-z-]*)\s*:\s*(?P<desc>.*)$"
)
#: Every entry line opens with a bolded date, so a `- **…` bullet that matches neither shape
#: above is malformed and raises — the "never a silent skip" guard, so a typo'd date or a
#: missing certainty can't quietly drop a health event out of the dataset. Bullets that don't
#: open with `- **` are narrative (e.g. §3 Sleep regimes' "- (none recorded)") and are ignored.
_BULLET_RE = re.compile(r"^- \*\*")

_SECTIONS_WITH_ENTRIES = {"2", "3"}
#: `### 2026-03` in §2 groups entries by month; it carries no provenance the entry's own date
#: doesn't already carry, unlike §3's semantic subsections ("Diet phases", "Training blocks").
#: Matches a trailing annotation too — the reconstruction's last bucket is
#: `### 2026-07 (current — live logging takes over from here)`, still a month bucket.
_MONTH_BUCKET_RE = re.compile(r"^\d{4}-\d{2}\b")
#: Separates the section from the date in `source_ref`. Deliberately not `/`: §3's subsection
#: titles contain slashes, which would make the field ambiguous to split (see `_Entry.source_ref`).
_REF_SEP = "::"


@dataclass(frozen=True)
class _Entry:
    """One parsed markdown entry, before type mapping or expansion."""

    rtype: str  # the reconstruction's own type vocabulary
    start: date
    end: date | None  # None for a point event; the period end otherwise
    end_is_ongoing: bool
    certainty: str  # the START certainty; the end's is narrative only
    description: str
    section: str  # "2" | "3"
    section_title: str
    subsection: str | None
    line_no: int

    @property
    def is_period(self) -> bool:
        return self.end is not None or self.end_is_ongoing

    @property
    def payload_key(self) -> str:
        """Stable join key into the reviewed payload table, and the basis of `record_id`.

        Derived from the entry's *coordinates* (type + dates), never its prose — so rewording a
        description leaves every key untouched and nothing re-ingests (§4.3)."""
        if not self.is_period:
            return f"{self.rtype}.{self.start.isoformat()}"
        end = "ongoing" if self.end_is_ongoing else self.end.isoformat()  # type: ignore[union-attr]
        return f"{self.rtype}.{self.start.isoformat()}.{end}"

    @property
    def source_ref(self) -> str:
        """Human-facing provenance: where in the reconstruction this record came from.

        Shape: ``<section> :: <date-or-range>``. The delimiter is `` :: `` rather than `` / ``
        because §3's own subsection titles *contain* slashes ("Supplement / medication stacks",
        "Injuries / illnesses / life events…") — a slash separator makes the string ambiguous to
        split, which is a trap for anyone auditing a dataset or a failure artifact. Nothing
        parses this field today; the unambiguous delimiter keeps it that way by choice rather
        than by luck.
        """
        # §3's subsections are semantic ("Diet phases", "Training blocks") and belong in the
        # provenance string; §2's are month buckets ("### 2026-03") that add nothing the date
        # in the ref doesn't already say, so they're dropped.
        where = f"§{self.section} {self.section_title}"
        if self.subsection and not _MONTH_BUCKET_RE.match(self.subsection):
            where = f"§{self.section} {self.subsection}"
        if not self.is_period:
            return f"{where} {_REF_SEP} {self.start.isoformat()}"
        end = "ongoing" if self.end_is_ongoing else self.end.isoformat()  # type: ignore[union-attr]
        return f"{where} {_REF_SEP} {self.start.isoformat()} → {end}"


# ── parsing ───────────────────────────────────────────────────────────────────────────────


def _clean(text: str) -> str:
    """Strip the markdown the prose carries so it reads plainly as note text or a summary."""
    text = re.sub(r"\s*\[S\]\s*$", "", text)  # the sensitivity marker is metadata, not content
    text = text.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def _parse(markdown: str) -> list[_Entry]:
    """Read every §2 timeline entry and §3 period fact. Other sections are narrative."""
    entries: list[_Entry] = []
    section: str | None = None
    section_title = ""
    subsection: str | None = None
    pending: list[str] = []  # continuation lines belonging to the entry under construction

    def flush() -> None:
        if not pending or not entries:
            return
        extra = " ".join(_clean(re.sub(r"^\s*-\s*", "", line)) for line in pending)
        if extra:
            last = entries[-1]
            entries[-1] = _Entry(**{**last.__dict__, "description": f"{last.description} {extra}"})
        pending.clear()

    for line_no, raw in enumerate(markdown.splitlines(), start=1):
        if m := _SECTION_RE.match(raw):
            flush()
            section, section_title, subsection = m["num"], m["title"].strip(), None
            continue
        if m := _SUBSECTION_RE.match(raw):
            flush()
            # `[S]` is a sensitivity marker on the heading, not part of its name — strip it here
            # for the same reason _clean strips it from descriptions.
            subsection = _clean(m["title"])
            continue
        if section not in _SECTIONS_WITH_ENTRIES:
            continue

        # Continuation: an indented, non-blank line extends the entry above it.
        if raw.startswith((" ", "\t")) and raw.strip():
            pending.append(raw)
            continue
        flush()

        if m := _PERIOD_RE.match(raw):
            ongoing = m["end"] == "ongoing"
            entries.append(
                _Entry(
                    rtype=m["rtype"],
                    start=date.fromisoformat(m["start"]),
                    end=None if ongoing else date.fromisoformat(m["end"]),
                    end_is_ongoing=ongoing,
                    certainty=m["certainty"].split("/")[0].strip(),
                    description=_clean(m["desc"]),
                    section=section,
                    section_title=section_title,
                    subsection=subsection,
                    line_no=line_no,
                )
            )
        elif m := _POINT_RE.match(raw):
            entries.append(
                _Entry(
                    rtype=m["rtype"],
                    start=date.fromisoformat(m["day"]),
                    end=None,
                    end_is_ongoing=False,
                    certainty=m["certainty"].strip(),
                    description=_clean(m["desc"]),
                    section=section,
                    section_title=section_title,
                    subsection=subsection,
                    line_no=line_no,
                )
            )
        elif _BULLET_RE.match(raw):
            raise DatasetError(
                f"line {line_no}: bullet inside §{section} matches neither the point-event nor "
                f"the period-fact shape — refusing to skip it silently:\n  {raw.strip()}"
            )
    flush()
    return entries


# ── type mapping ──────────────────────────────────────────────────────────────────────────


#: Stands in for a period whose end is `ongoing` when testing date containment.
_FAR_FUTURE = date(9999, 12, 31)


def _period_coverage(entries: list[_Entry]) -> dict[str, list[tuple[date, date]]]:
    """For each reconstruction type, the §3 date ranges that already state that behavior."""
    coverage: dict[str, list[tuple[date, date]]] = {}
    for e in entries:
        if e.section == "3" and e.is_period:
            end = _FAR_FUTURE if e.end_is_ongoing else e.end  # type: ignore[assignment]
            coverage.setdefault(e.rtype, []).append((e.start, end))
    return coverage


def _is_change_marker(entry: _Entry, coverage: dict[str, list[tuple[date, date]]]) -> bool:
    """Is this §2 point event a *marker* for behavior a §3 period already states?

    The reconstruction records an ongoing behavior twice: once in §2 as the dated moment it
    changed ("started 4 eggs/day + 200g packet dahi … — see §3 diet phases") and once in §3 as
    the period fact. Converting both as quantitative events would write a meal **and** a
    day-one expansion row for the same day — a silent double-count, which is the exact P0 class
    of bug this phase exists to prevent (§5).

    The test is deterministic and needs no prose reading: a §2 point event is a marker when a §3
    period **of the same reconstruction type covers its date**. Containment rather than
    start-matching is what catches the mid-period changes too (the "100–150g chicken added from
    2026-06-23" marker sits inside the 06-15 → 07-05 diet phase, not at its start).

    Markers are converted to dated `note`s rather than dropped: they are exactly the narrative
    Story A's causal chain cites, and a note carries no quantity, so nothing double-counts.
    """
    if entry.section != "2" or entry.is_period:
        return False
    return any(start <= entry.start <= end for start, end in coverage.get(entry.rtype, []))


def _engine_type(
    entry: _Entry, table_entry: PayloadEntry | None, coverage: dict[str, list[tuple[date, date]]]
) -> str:
    """Map a reconstruction type to a registered engine type (§4.1).

    Two narrowings sit on top of the raw table, and both exist to stop non-events from being
    counted as events:

    1. **Change markers** become `note` (see `_is_change_marker`).
    2. **A `*-pattern` that does not expand becomes `note`.** `meal-pattern` means "this is what
       eating looked like", which is a `meal` only when it is a recurring *quantified* assertion.
       The lifelong-vegetarian period and the junk-food phase are background states — writing
       them as `meal` rows would put two non-meals into `count_events(meal)` and into every meal
       aggregate. Whether an entry expands is exactly whether the reviewed table gave it a
       cadence, so this needs no inference either.
    """
    if _is_change_marker(entry, coverage):
        return "note"
    if entry.rtype.endswith("-pattern"):
        expands = entry.is_period and table_entry is not None and table_entry.cadence
        if not expands:
            return "note"
    try:
        return TYPE_MAP[entry.rtype]
    except KeyError as exc:
        raise DatasetError(
            f"line {entry.line_no}: unknown reconstruction type {entry.rtype!r} "
            f"(known: {sorted(TYPE_MAP)})"
        ) from exc


def _base_confidence(entry: _Entry, table_entry: PayloadEntry | None) -> float:
    if table_entry is not None and table_entry.confidence is not None:
        return table_entry.confidence
    try:
        return CERTAINTY_CONFIDENCE[entry.certainty]
    except KeyError as exc:
        raise DatasetError(
            f"line {entry.line_no}: unknown certainty {entry.certainty!r} "
            f"(known: {sorted(CERTAINTY_CONFIDENCE)})"
        ) from exc


def _default_note_payload(entry: _Entry) -> dict[str, str]:
    """A `note` needs no reviewed payload — preserving the raw text *is* the payload."""
    return {"text": entry.description}


# ── conversion ────────────────────────────────────────────────────────────────────────────


def convert(
    markdown: str, table: PayloadTable, *, cutover: date, tz: str
) -> list[ReplayRecord]:
    """Turn the reconstruction into replay records, applying §4.1's three expansion rules."""
    entries = _parse(markdown)
    coverage = _period_coverage(entries)  # needed before typing: see _is_change_marker

    records: list[ReplayRecord] = []
    for entry in entries:
        table_entry = table.get(entry.payload_key)
        etype = _engine_type(entry, table_entry, coverage)

        if table_entry is None:
            if etype != "note":
                raise DatasetError(
                    f"line {entry.line_no}: no payload-table entry for key "
                    f"{entry.payload_key!r} (type {entry.rtype!r} needs reviewed values; only "
                    f"`note` records may default). Add it to the payload table."
                )
            table_entry = PayloadEntry(payload=_default_note_payload(entry))
        elif etype == "note" and table_entry.payload is None and not table_entry.segments:
            table_entry = PayloadEntry(payload=_default_note_payload(entry))

        # Rule 1 — expand only when the assertion carries a per-occurrence quantity, declared
        # as a cadence in the reviewed table. Rule 2 — expand at THAT cadence, never daily by
        # default. A period without a cadence is a background state or a qualitative phase and
        # yields exactly one record dated at its start.
        if entry.is_period and table_entry.cadence:
            records.extend(_expand(entry, table_entry, etype, cutover=cutover, tz=tz))
        else:
            records.append(_single(entry, table_entry, etype, tz=tz))

    records.sort(key=lambda r: (r.event_time, r.record_id))
    return records


def _payload_with_category(entry: _Entry, etype: str, payload: dict) -> dict:
    """Attach the nutritional/prescription discriminator so `supplement` carries both kinds
    without either becoming misleading (§4.1). `extra="allow"` makes this migration-free.

    Only applied when the record actually lands as a `supplement`; a medication *marker* that
    converted to a `note` has no dose to categorize."""
    if etype != "supplement" or entry.rtype not in CATEGORY_MAP:
        return payload
    return {**payload, "category": payload.get("category", CATEGORY_MAP[entry.rtype])}


def _single(entry: _Entry, te: PayloadEntry, etype: str, *, tz: str) -> ReplayRecord:
    payload = _payload_with_category(entry, etype, te.payload_for(entry.start))
    return ReplayRecord(
        record_id=entry.payload_key,
        type=etype,
        event_time=event_time_for(entry.start, tz, te.event_time),
        tz=tz,
        confidence=round(_base_confidence(entry, te), 2),
        payload=payload,
        source_ref=entry.source_ref,
        summary=te.summary or entry.description,
    )


def _expand(
    entry: _Entry, te: PayloadEntry, etype: str, *, cutover: date, tz: str
) -> list[ReplayRecord]:
    """Materialize one record per occurrence, clamped at the live-logging cutover (Rule 3).

    Each row carries `expanded_from` and a lowered confidence: it asserts a specific day drawn
    from an asserted pattern, not an observation, and the glass box must be able to say so.
    """
    # Rule 3: never expand into the window live logging owns, or the same day is counted twice.
    last = cutover - timedelta(days=1)
    end = last if entry.end_is_ongoing else min(entry.end, last)  # type: ignore[type-var]
    if end < entry.start:
        return []  # the whole period sits after the cutover — live logging owns it

    confidence = round(_base_confidence(entry, te) * EXPANSION_CONFIDENCE_FACTOR, 2)
    marker = {
        "period_start": entry.start.isoformat(),
        "period_end": "ongoing" if entry.end_is_ongoing else entry.end.isoformat(),  # type: ignore[union-attr]
        "cadence": te.cadence,
        "assertion": entry.description,
        "composition": entry.payload_key,
    }
    out: list[ReplayRecord] = []
    for day in occurrences(entry.start, end, te.cadence):  # type: ignore[arg-type]
        out.append(
            ReplayRecord(
                record_id=f"{entry.payload_key}#{day.isoformat()}",
                type=etype,
                event_time=event_time_for(day, tz, te.event_time),
                tz=tz,
                confidence=confidence,
                payload=_payload_with_category(entry, etype, te.payload_for(day)),
                source_ref=entry.source_ref,
                expanded_from=marker,
                summary=te.summary_for(day) or entry.description,
            )
        )
    return out


# ── entry point ───────────────────────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(
    source: Path, payloads: Path, *, cutover: date, tz: str, now: datetime | None = None
) -> Manifest:
    now = now or datetime.now(timezone.utc)
    return Manifest(
        dataset_version=DATASET_VERSION,
        converter_version=CONVERTER_VERSION,
        source_document=source.as_posix(),
        source_document_sha256=_sha256(source),
        payload_table=payloads.as_posix(),
        payload_table_sha256=_sha256(payloads),
        generated_at=now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        replay_cutover_date=cutover.isoformat(),
        default_tz=tz,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Convert the reconstruction markdown into replay JSONL (T8 M1)."
    )
    parser.add_argument("--source", type=Path, required=True, help="reconstruction markdown")
    parser.add_argument("--payloads", type=Path, required=True, help="reviewed payload table")
    parser.add_argument("--out", type=Path, required=True, help="output .jsonl path")
    parser.add_argument(
        "--cutover", required=True, help="YYYY-MM-DD; expansion stops the day before this"
    )
    parser.add_argument("--tz", default="Asia/Kolkata", help="IANA timezone for event times")
    parser.add_argument(
        "--force", action="store_true", help="overwrite an existing dataset (discards hand-edits)"
    )
    args = parser.parse_args(argv)

    # Regenerating over a reviewed, possibly hand-edited dataset is destructive (§4.12), so it
    # takes an explicit flag rather than happening as a side effect of re-running the command.
    if args.out.exists() and not args.force:
        raise SystemExit(
            f"{args.out} already exists — refusing to overwrite reviewed output. "
            f"Re-run with --force if you mean to discard any hand-edits."
        )

    table = load_payload_table(args.payloads)
    records = convert(
        args.source.read_text(encoding="utf-8"),
        table,
        cutover=date.fromisoformat(args.cutover),
        tz=args.tz,
    )
    manifest = build_manifest(
        args.source, args.payloads, cutover=date.fromisoformat(args.cutover), tz=args.tz
    )
    manifest_path = write_dataset(records, manifest, args.out)

    expanded = sum(1 for r in records if r.expanded_from is not None)
    print(f"wrote {len(records)} record(s) to {args.out}  ({expanded} expanded, ")
    print(f"      {len(records) - expanded} point events)")
    print(f"wrote manifest to {manifest_path}")


if __name__ == "__main__":
    main()
