# Replay Architecture: Phase 4 History Bootstrap

> **Status: ARCHITECTURE LOCKED — 2026-07-30.** Canonical reference for **T8** (replay CLI,
> [11-implementation-tasks.md](../office-hours/11-implementation-tasks.md#task-list)) and
> **Phase 4** ([implementation-roadmap.md](../implementation-roadmap.md#phase-4--history-bootstrap-replay-3-5-days-includes-human-reconstruction-time)).
> Reviewed pre-implementation 2026-07-29 (same discipline used ahead of Phases 2 and 3); the
> four open questions were resolved interactively with the builder 2026-07-29 → 2026-07-30 and
> every decision in §4 is now settled. New code, task updates, and code comments should link
> here rather than re-derive this reasoning.
>
> **Do not silently re-litigate §4.** To reverse a decision, record a new one here with
> rationale, the same rule ADR-13/ADR-14 follow.
>
> Companion docs:
> [ingestion-transaction-boundaries.md](ingestion-transaction-boundaries.md) (the write-path
> spec every replay call obeys). Related: [TODOS.md](../../TODOS.md).
>
> **Data source — `docs/evidence/timeline-entries.md`, deliberately NOT in this repo.** The Phase 0
> reconstruction holds unsanitized personal health data (lab values, clinic names, DOB, medication
> doses), so it is gitignored under [ADR-7](../office-hours/09-decisions.md#adr-7) — raw
> reconstruction inputs stay local; only a sanitized derivative ever ships publicly. References to
> it below describe a **local-only file**; a clone will not contain it, and M1's converter must be
> pointed at a local copy.
>
> **When M1–M6 land**, promote §4 into a new ADR in
> [09-decisions.md](../office-hours/09-decisions.md) — mirroring how ADR-14 absorbed Phase 3's
> in-flight decisions — and update this header rather than leaving two documents both claiming
> to be canonical.

## 1. Why this document exists

Phase 4 is not a new memory capability — no new schema, no new retrieval path, no new agent
tool. It is **the existing Phase 2 write path used as a batch, long-running, interruptible
client**: the same call a chat turn makes, invoked hundreds of times in a row, with two
requirements a single interactive request never had — don't re-pay for work already done, and
don't lose your place if the process dies at record 400 of 1800.

That framing is what makes this a design review rather than a "write a CLI" task: nearly every
real risk here is a **durability/idempotency** problem, not an extraction-quality problem. The
single highest-severity risk (§5) is silently duplicating memories, because duplicates inflate
the aggregate numbers the demo's causal story rests on — a wrong answer that looks right.

## 2. Objective and scope

**Objective:** push the builder's reconstructed health history
(`docs/evidence/timeline-entries.md` — local-only, see header) through the production
ingestion pipeline, so the account stops being empty and Story A's numbers become verifiable in
the database (OQ5, [10-open-questions.md](../office-hours/10-open-questions.md)).

**Why it exists:**

1. The money question is unanswerable over a few days of live data — it needs months of dated
   history, and ADR-4 bans both synthetic personas and raw SQL seeding as ways to get it.
2. The Phase 3 review flagged replay as the dominant Bedrock cost and schedule risk
   (outside-voice #7) — caching and resumability are what make iterating on the reconstruction
   affordable at all.

**User-visible outcome:** none directly. Phase 4 ships no new UI or API surface; the outcome
lives in the database. Afterward the existing Phase 3 chat answers history questions with real,
dated, cited evidence instead of "nothing logged."

**Architecture impact.** Larger than the original estimate assumed, because reading the
reconstruction revealed it is a structured document with periods, exact lab values, and its own
type vocabulary. Phase 4 adds:

- `cli/convert.py` — one-time markdown → JSONL converter (pure; no DB, no model)
- `cli/replay.py` + `replay_cache.py` + `replay_ledger.py` + `replay_dataset.py`
- one new `IngestionService` entry point (`ingest_events`) sharing the whole Phase 2 tail (§4.11)
- one optional parameter on the note path (§4.6)
- one query-time normalization stop-gap in `engine/retrieval.py` (§4.13)

Everything downstream of validation — transaction boundary, never-lose-input, embeddings,
receipts, backfill — is reused exactly as Phase 2 built it.

## 3. Architecture map

### Reusable as-is

- `IngestionService.ingest_text(user_id, text, *, source, provenance, now, tz) -> Receipt`
  already accepts everything replay needs. Nothing to add for the extraction path.
- The whole Phase 2 failure policy (extraction fail → note, validation fail → note,
  never-lose-input) applies automatically.
- `insert_memories`/`insert_memory` are row-at-a-time by design, already guarding the C-SPANN
  batch-insert footgun.
- **`mark_superseded()`** (`engine/repository.py`) and the `reprocess_note` transaction pattern
  (insert replacements + retire the original, atomically) are exactly what §4.12's correction
  workflow needs. No new persistence primitive.
- `FakeModelProvider` with `extract_calls`/`embed_calls` counters (`engine/tests/conftest.py`) —
  the fixture that makes "second run makes zero model calls" assertable.
- The `cli/` composition-root pattern (`migrate.py`, `backfill.py`):
  `load_settings() → Database → IngestionService`, argparse, `if __name__ == "__main__"`.
- The schema already anticipates this: `source` lists `'replay'`, `provenance` has
  `'reconstructed'`, and ADR-5's bi-temporal `event_time`/`created_at` split exists *because*
  reconstructed memories need it.

### Must not change

- **The transaction boundary.** One ingest call = one transaction; no model call inside a
  transaction (`ingestion-transaction-boundaries.md` rule 1). Batching a whole file into one
  transaction would violate this and resurrect the vector-index footgun the T1 canary guards.
- **No deduplication inside `ingest_text`.** Defined, tested behavior — double-submit produces
  two rows deliberately. Replay's protection lives entirely in the CLI (§4.3).
- **The `extract_events` empty-result contract** (`[]` = affirmed no-op, `ExtractionError` =
  everything else). A cache that flattens this reintroduces the silent-loss bug D1 closed.
- **ADR-14.11 strict slots** — no invented defaults. §4.13 inherits this for empty search terms.

### The per-record contract

```python
# extraction path (free-text assertions — the model estimates nutrition)
ingestion_service.ingest_text(
    user_id, record.text,
    source="replay", provenance="reconstructed",
    now=record.event_time,   # the record's OWN historical timestamp, never wall-clock now
    tz=record.tz,
    note_confidence=record.confidence,   # §4.6
)

# direct path (source already carries exact values — labs, device scans, doses)
ingestion_service.ingest_events(
    user_id, [record.as_extracted_event()],
    source="replay", provenance="reconstructed",
)
```

## 4. Design decisions — LOCKED

| § | Decision | Resolution |
|---|---|---|
| 4.1 | Dataset format & converter | JSONL + sidecar manifest, converter-generated |
| 4.2 | Extraction cache | Local file, versioned key |
| 4.3 | Idempotent resume | Local ledger, explicit `record_id`, `--rebuild-ledger` |
| 4.4 | Batching | Unchanged — row-at-a-time |
| 4.5 | Provenance | Unchanged — `source='replay'`, `provenance='reconstructed'` |
| 4.6 | Note confidence | Per-record hint threaded through |
| 4.7 | Duplicate detection | The ledger, no engine-level dedup |
| 4.8 | Transaction boundaries | Unchanged |
| 4.9 | Progress tracking | Flush per record |
| 4.10 | Run-level failure | Halt at 5 consecutive, `--force` override |
| 4.11 | Direct-ingest path | Second entry point, shared tail |
| 4.12 | Correction workflow | Supersession under `--apply-corrections` |
| 4.13 | Item-matching stop-gap | Query-time normalization only |

### 4.1 Dataset format, the converter, and period expansion

**Decision: JSONL records + a sidecar manifest, produced by a one-time converter from the
reconstruction markdown. The replay CLI consumes only JSONL and never parses markdown.**

*Rejected:* the CLI parsing narrative text (pushes language interpretation into a deterministic
tool — the invariant ADR-14.10 protects); raw strings with no metadata (loses the historical
anchor, so relative dates have nothing to resolve against, defeating half of ADR-5).

#### Record shape

```jsonc
{
  "record_id": "diet-phase-3.20260326",     // converter-owned, read-only (§4.3)
  "kind": "extract",                         // "extract" | "direct"  (§4.11)
  "type": "meal",                            // a REGISTERED engine type
  "event_time": "2026-03-26T08:00:00+05:30",
  "tz": "Asia/Kolkata",
  "confidence": 0.6,
  "text": "4 eggs + 200g dahi",             // kind=extract
  "payload": { },                            // kind=direct — already-typed
  "expanded_from": {                         // synthetic period rows only
    "period_start": "2026-03-26",
    "period_end":   "2026-04-24",
    "cadence":      "daily",
    "assertion":    "4 eggs + 200g dahi daily"
  },
  "source_ref": "§3 Diet phases / 2026-03-26 → 2026-04-24"
}
```

#### Manifest (sidecar, not a header line)

```jsonc
// dataset.manifest.json
{
  "dataset_version": "1",
  "converter_version": "1.0.0",
  "source_document": "docs/evidence/timeline-entries.md",
  "source_document_sha256": "…",   // staleness detection vs the markdown
  "generated_at": "2026-07-30T…Z",
  "replay_cutover_date": "2026-07-01",
  "default_tz": "Asia/Kolkata",
  "extraction_prompt_version": "1"  // feeds the §4.2 cache key
}
```

**Why a sidecar rather than a first-line header:** keeping the JSONL **homogeneous** — every
line a record — preserves `wc -l`, `head`, `grep`, `jq`, and hand-editing, and removes the
"line 1 is special" footgun.

**Why the cutover lives here rather than in converter logic:** hardcoding a date makes a
project-specific fact into code; a CLI flag leaves it in shell history so a regeneration months
later isn't reproducible. Declaring it with the data is deterministic, self-describing, and
generic for every future user's dataset.

**The manifest hashes the source markdown, never the JSONL.** Hashing the JSONL would fight the
hand-edit workflow (§4.12) — every legitimate correction would read as corruption. Hashing the
markdown gives the check that matters: *has the reconstruction changed since this was
generated?*

#### Period expansion — three rules

The reconstruction has two shapes: §2 dated point events, and §3 **period facts** spanning
ranges. `memories.event_time` is a single timestamp, so ranges must be resolved at conversion
time. Naive daily expansion of every period yields **~7,800 records** against this dataset — of
which 7,164 are the vegetarian period alone. The rules below yield **~430**.

**Rule 1 — expand only if the assertion carries a per-occurrence quantity.** Objective, checkable
per record, needs no demo knowledge:

- `"4 eggs + 200g dahi daily"` → quantities. `aggregate_memories(protein_g, group_by=day)` needs
  a row per day or the period's average is silently wrong. **Expand.**
- `"strictly vegetarian"` (2006-08-14 → 2026-03-26) → asserts what was *absent*; nothing to sum.
  **One record.**
- `"junk-food phase, negligible protein"` → qualitative. Expanding would push 61 vague
  descriptions through extraction and yield 61 *invented* nutrition estimates — ADR-4 violated 61
  times. **One record.**

**Rule 2 — expand at the assertion's own cadence, never daily-by-default.**
`"Vitamin D 60,000 IU once weekly"` expanded daily would claim **7× the actual dose** — fabricated
data in the table the glass box invites judges to click into. Weekly → ~13 records, not 88.

**Rule 3 — clamp `ongoing` periods at `replay_cutover_date`, not at today.** The reconstruction
marks *"2026-07 (current — live logging takes over from here)"*. Expanding an ongoing period past
the cutover while live-logging the same days **double-counts** them.

#### The honesty mechanism (what keeps expansion ADR-4-compliant)

A synthetic row asserts a meal happened on a specific day, but the builder asserted a *pattern*,
not 30 observations. Every expanded record therefore carries, beyond `provenance='reconstructed'`:

- **lowered `confidence`** relative to point events (a device scan is ~1.0; "you ate 4 eggs on
  2026-04-03" is materially less)
- the inline **`expanded_from`** marker, so a citation chip renders *"one day of an asserted
  pattern spanning 2026-03-26 → 2026-04-24"* rather than *"you logged this meal."*

Without that marker, expansion would violate ADR-4. With it, the row is honest about what it is.

**Why `expanded_from` is specific rather than a generalized `reconstruction_origin`** (evaluated
and rejected): the codebase already has two provenance layers, and the plausible alternative
values belong to them, not to a third. `imported_dataset` is the **`source` column**'s job (its
comment is already open-ended: `'chat' | 'photo_upload' | 'file_upload' | 'replay' | ...`);
`manual_reconstruction` is already fully expressed by `source='replay'` +
`provenance='reconstructed'`. A generalized field would duplicate `source` — the
two-sources-of-truth problem ADR-13.14 exists to prevent. What `expanded_from` does that `source`
structurally cannot is record a **derivation edge with data attached** (bounds, cadence, parent
assertion). For it to need generalizing, a *second derivation kind* would have to appear, and the
obvious candidate — interpolation between measurements — is explicitly banned by ADR-13.12 ("gaps
stay missing"). Decisive argument: ADR-13.6's `extra="allow"` makes adding a sibling key later
free, so the cost of being wrong in the specific direction is near zero.

#### Type mapping (no registry changes in Phase 4)

| Reconstruction type | Engine type | Note |
|---|---|---|
| `note` | `note` | |
| `blood-report` | `blood_report` | rename only |
| `body-scan` | `body_scan` | rename only |
| `meal-pattern` | `meal` | period → Rules 1–3 |
| `workout-pattern` | `workout` | period → Rules 1–3 |
| `supplement` | `supplement` | `category="nutritional"` |
| `medication` | `supplement` | `category="prescription"` |
| `illness` | `note` | dated life event, not queried quantitatively |
| `habit` | `note` | |

**On mapping medication → `supplement`:** there is **no documented statement** that `supplement`
was intended to cover prescriptions — checked `engine/types.py`, `04-database-design.md`, and the
ADRs. The alternative (medication → `note`) was rejected on evidence: `NotePayload` requires
`text` and has **no dose structure**, so it would turn `oral minoxidil 2.5mg` into unstructured
prose exactly where dose *changes* (1.25mg → 2.5mg, weekly → biweekly) are the substance of
Story B's causal chain. The explicit `category` field (free via `extra="allow"`) keeps the two
separable, so `supplement` is **extended in writing** rather than silently overloaded.

### 4.2 Extraction cache

**Decision: a local file, keyed on
`hash(text, now, tz, model_id, extraction_prompt_version)`.**

*Rejected:* a `replay_cache` DB table (new table + migration for a dev-iteration aid — infra for
its own sake, the posture ADR-3 rejects); no cache (defeats T8's purpose entirely).

The **prompt-version stamp comes from the manifest**, not just the model ID: a prompt change then
invalidates automatically instead of silently serving stale extractions. That is the sharpest
available cache bug and it costs one field to avoid.

### 4.3 Idempotent resume — the load-bearing decision

**Decision: a local ledger file keyed on an explicit converter-generated `record_id`, plus a
`--rebuild-ledger` mode that reconstructs state from committed `source='replay'` rows.**

*Rejected:* an `external_ref` column on `memories` (touches a table 255 tests assume, for a
CLI-local problem); content matching on `(user_id, source, event_time, summary)` (fragile —
similar records collide, and re-extraction wording changes make a record "look new" anyway).

```jsonc
// ledger entry
{
  "record_id": "diet-phase-3.20260326",
  "content_hash": "sha256:…",     // drift detection (§4.12)
  "memory_ids": ["0f9c…"],        // supersession targets (§4.12)
  "ingested_at": "2026-07-30T…Z",
  "record": { }                    // snapshot, for human-readable diffs (§4.12)
}
```

**⚠️ `record_id` is derived from `(source_ref, occurrence index/date)` — NOT from the record's
text.** This revises the original review's content-hash proposal, and the reason is a real
duplicate path: under content-hashing, regenerating the JSONL after *any* wording change produces
a different key for an already-committed record → the ledger reports "not done" → it is ingested
again. That is §5's P0 risk reachable through an ordinary documentation edit. Deriving the ID from
stable reconstruction coordinates instead means:

- reword the markdown and regenerate → same IDs → nothing re-ingests
- hand-edit a quantity in the JSONL → same ID → a correction, not a duplicate
- add a genuinely new period → new IDs only for new records

**`record_id` is converter-owned and read-only to humans.** A hand-invented ID breaks determinism
the moment you regenerate — the converter emits its own, the record looks new, and you get the
duplicate this whole design avoids. Because the ID is a deterministic function of the record's own
`source_ref` and occurrence, **the CLI recomputes it and hard-errors on any ID it could not have
produced** — a checked invariant, not a README note (the same posture as `_GuardedSerde` in
[graph-state-durability.md](graph-state-durability.md), where the guard rather than the convention
is the guarantee). A record you want to add by hand is unsupported: add it to the markdown and
regenerate.

**The invariant this decision lives or dies on:** a ledger entry is written **strictly after** the
ingest call returns — never before, never batched. Same "receipt only after commit" rule as
`ingestion-transaction-boundaries.md` rule 4, applied to a CLI-owned durability record.

**Why a rebuild path exists:** a bare append-only file is a single point of failure. Losing it
must not be fatal, so `--rebuild-ledger` walks committed `source='replay'` rows and reconstructs
what is already represented.

### 4.4 Batching — unchanged

Row-at-a-time inserts, one transaction per record, already enforced at the repository layer.
Nobody may "optimize" this into a multi-row insert (§5, maintenance notes).

### 4.5 Provenance — unchanged

`source="replay"`, `provenance="reconstructed"`, already wired end-to-end through `ingest_text`,
`_persist_note`, and `reprocess_note` (D3, closed 2026-07-21).

### 4.6 Note-fallback confidence

**Decision: thread a per-record confidence hint through to the note path** (optional parameter,
defaulting to `None` so live-chat behavior — `_NOTE_CONFIDENCE = 1.0` — is byte-identical).

*Rejected:* a flat hardcoded reconstructed-note confidence (every note gets the same number
regardless of how certain that reconstruction actually was); leaving `1.0` (a reconstructed note
claiming full certainty of LLM-reconstructed text contradicts `04-database-design.md`'s "1.0 for
directly observed live data" — already flagged in TODOS.md). §4.1's `confidence` field supplies
the value for free, so the flat option would only need redoing.

### 4.7 Duplicate detection — the ledger

Same mechanism as §4.3. `ingest_text`'s no-dedup behavior stays correct and untouched for live
chat; replay gets no engine-level dedup feature.

### 4.8 Transaction boundaries — unchanged

One ingest call = one transaction. Ledger writes happen in the CLI process, strictly after commit,
never inside the DB transaction — the same pattern `backfill_embeddings` already uses.

### 4.9 Progress tracking

**Decision: flush the ledger and log progress after every record.** A crash re-processes at most
the in-flight record. Batching flushes every N records would silently re-process up to N on a
crash, reintroducing exactly the risk §4.3 exists to prevent — and at replay volume, where every
record already pays a network round trip, one file write is noise.

### 4.10 Run-level failure recovery

**Decision: halt after 5 consecutive record-level failures; `--force` overrides.**

Record-level failure is already Phase 2's job (extraction/validation → note, counted as
processed). Five is low enough to catch expired credentials or a systematic converter bug within
seconds, high enough that a couple of transient Bedrock throttles don't stop a long run.
*Rejected:* never halting (a transient outage silently turns the historical import into a wall of
unparsed notes).

**Every failure — fatal or not — is written to a run artifact**
(`replay-failures-<timestamp>.jsonl`), not just stderr, because a run that halts after N failures
must hand you all N:

| Field | Why |
|---|---|
| `record_id` | correlates with the ledger |
| `record_number`, `jsonl_line` | locate it in the file |
| `kind` (`extract`/`direct`) | which entry point failed |
| `source_record` | the original JSONL line |
| `constructed_payload` | what was actually handed to validation |
| `validation_errors` | `ValidationError.errors()` — structured `{loc, msg, type}` |
| `source_ref` | fix the *markdown*, not just the JSONL |

Non-fatal extraction-path note fallbacks land here too, so you know what is worth reprocessing.

### 4.11 The direct-ingest path

**Decision: a second `IngestionService` entry point that skips extraction and nothing else.**

The reconstruction's blood-report and body-scan entries already carry exact lab and device values
— including `Vitamin D 6.20 → 38.4 ng/mL`, the two numbers Story A rests on. Re-parsing them
through an LLM risks a transcription error landing in precisely the values the demo verifies, and
costs extraction calls for data that is already typed.

**One pipeline, two entry points — enforced structurally, not by convention:**

```
(A) extraction + retry      ← the ONLY stage the direct path skips
(B) validation              ┐
(C) embeddings              │  lifted into ONE shared tail,
(D) single write txn        ├─ called by BOTH ingest_text and
(E) receipt from committed  │  ingest_events
(F) opportunistic backfill  ┘
```

Both paths then share one implementation of the transaction boundary, never-lose-input, receipts,
and backfill. This is a DRY improvement to existing code, not an addition — there is no second
pipeline to keep in sync.

**Validation failure on the direct path is fatal, not a note fallback.** On the extraction path a
validation failure means an unpredictable LLM, and preserving raw text is the right recovery. Here
there is no LLM — a failure means **the converter emitted bad data**, and a note fallback would
silently paper over a converter bug across hundreds of records. It halts with the §4.10 artifact
instead; §4.10's consecutive-failure rule stops the run if it is systemic.

**Which records use which path:** free-text assertions go through extraction, because extraction
adds *nutrition estimation* the converter cannot do (the converter does not know the protein in 4
eggs). Records whose source already carries exact structured values — labs, device scans,
supplement doses — go direct.

### 4.12 The correction workflow

**Decision: corrections propagate by supersession, detected via ledger content drift, applied only
under `--apply-corrections`.**

**The problem this solves is not obvious.** Under §4.3's stable IDs, editing a fact does *not*
reach the database: change `4 eggs → 3 eggs`, regenerate, re-run — the `record_id` is unchanged,
the ledger says "done", replay skips it, the database still says 4 eggs, and the run reports
success. Stable IDs prevent duplicates *and* silently prevent corrections; both halves need
designing.

Three cases per record instead of two:

| Ledger state | Action |
|---|---|
| `record_id` absent | ingest |
| present, `content_hash` matches | skip |
| present, `content_hash` **differs** | **corrected** — report; act only under `--apply-corrections` |

Applying a correction inserts the new rows **and** `mark_superseded`s the ledger's recorded
`memory_ids` **in one transaction** — precisely the `reprocess_note` pattern, and ADR-9's posture
("retraction never deletes; the engine's history of being wrong is itself memory") applied to
reconstruction corrections.

**Why the flag, rather than automatic:** if the converter ever drifts subtly (float formatting,
whitespace), automatic behavior would mass-supersede the entire dataset in one run. Detection is
free and always on; the wide-blast-radius action requires intent — the same posture as the
converter's `--force`.

**Diffs print on every run, flag or not**, so the review gate is automatic and nothing can be
superseded that you have not already seen:

```
CHANGED  meal / 2026-03-26  (record_id: diet-phase-3.20260326)
  text        "4 eggs + 200g dahi"  →  "3 eggs + 200g dahi"
  confidence  0.60  (unchanged)
  committed   memory 0f9c… "4 eggs and 200g curd"

3 changed · 0 new · 427 unchanged
Re-run with --apply-corrections to supersede the 3 committed rows.
```

The diff is pure local computation from the ledger's stored `record` snapshot — no DB read, no
model call. **Documented limit:** for extraction-path records the diff shows the *input text*
change, not the resulting payload change, because the new payload does not exist until extraction
runs. Payload-level diffs are available only for direct-ingest records.

#### Which artifact do you edit? (authoritative)

**The markdown is canonical for facts, permanently. The JSONL is canonical for what was
replayed.**

| Error kind | Fix where | Why |
|---|---|---|
| **Factual** — the fact is wrong | **the markdown**, then regenerate | the markdown carries `[S]` markers, `E01`/`E02` evidence refs, the certainty vocabulary (`exact`/`~week`/`~month`), and the causal reasoning — none of which the JSONL can hold. A JSONL-only fix loses *why*, which is where ADR-4's honesty posture lives |
| **Conversion** — markdown right, translation wrong | **the converter**, then regenerate | otherwise regeneration reintroduces the bug |
| **One-off conversion oddity** not worth a converter change | hand-edit the JSONL | documented escape hatch; logged as debt, since regeneration undoes it |

**Hand-editing the JSONL is for fixing translation, never for fixing truth.** That distinction is
what stops the two artifacts from diverging, and `source_document_sha256` is its enforcement: the
CLI can report "this JSONL is stale relative to its source" instead of letting them drift quietly.

The converter is **deterministic** (same markdown → byte-identical output) and **refuses to
overwrite an existing JSONL without `--force`**, so hand-edits are never lost silently.

```
Reconstruction markdown  (canonical for FACTS)
        │  cli.convert  — deterministic, --force to overwrite
        ▼
   JSONL + manifest  ──►  HUMAN REVIEW / EDIT  ◄── conversion errors only
        │                                          (facts go back to the markdown)
        │  cli.replay   — separate command, never auto-chained
        ▼
   ingest_text / ingest_events  →  committed memories
        │
        └─► ledger  →  later drift detected  →  --apply-corrections  →  supersession
```

### 4.13 Item-matching stop-gap (query-time normalization)

**Decision: normalize how a token is *written*, at query time only. Deciding which tokens *mean*
the same thing is Phase 5's canonicalization engine** (§8).

#### The contract

```python
def normalize_item(s: str) -> str: ...
```

Applied **identically to the search term and to the compared value** — one function, one call
site pair. Asymmetric application is a matching bug by construction.

**Guarantees:**

| | |
|---|---|
| **Deterministic** | Output depends only on `s`. No locale, no I/O, no global state, no clock. `str.casefold()` is locale-independent by definition (unlike `str.lower()` under some locales), and category tests use Unicode general categories, never a locale-sensitive `ispunct`. |
| **Pure** | No side effects; the argument is not mutated. |
| **Idempotent** | `normalize_item(normalize_item(x)) == normalize_item(x)` for all `x`. Applying it twice — by accident, or because a caller cannot tell whether a value is already normalized — is always safe. |
| **Total except empty** | Raises on a normalized-empty result rather than returning `""` (see step 6). |

*Honest boundary on determinism:* fixed **for a given Unicode data version**. General-category
assignments can change between Python releases; for the Latin + Devanagari food vocabulary in play
no such change is plausible, but the guarantee is "deterministic given a Unicode version," not
"fixed forever."

#### The algorithm

1. **Unicode NFC** — not NFKC. NFKC maps *different* characters together (`㎏`→`kg`, `①`→`1`),
   which is a meaning judgment, not a spelling one.
2. **`str.casefold()`** — not `.lower()`. Full Unicode case folding handles `ß`→`ss` and
   non-Latin scripts correctly.
3. **Unicode NFC again.** ⚠️ **Load-bearing, and not redundant.** Case folding is *not closed under
   normalization* (UAX #15) — folding can emit sequences that are no longer NFC. Without this
   second pass a later re-application would re-normalize and could yield a different string,
   **breaking the idempotence guarantee above**. This mirrors the Unicode standard's own canonical
   caseless-matching definition, which likewise normalizes after folding. Do not "simplify" it away.
4. **Repeatedly strip from both ends until stable**: Unicode general-category **P** characters and
   whitespace. Fixed-point iteration, so `"chicken, "` and `" ,chicken"` both resolve.
5. **Collapse internal whitespace runs** to a single space.
6. **Normalized-empty → reject the call**, never match-everything (ADR-14.11 strict slots: no
   invented defaults).

Each step is individually idempotent (NFC and casefold by Unicode definition; steps 4–5 by
construction — a fixed point cannot move, a collapsed run cannot re-collapse), and step 5 cannot
recreate work for step 4 because it never adds characters at the edges. The composition is
therefore idempotent, which the test suite asserts directly rather than assuming.

#### Explicit non-goals

**`normalize_item()` intentionally does NOT, and must never be extended to:**

| Non-goal | Why it is excluded |
|---|---|
| stem words | language knowledge |
| singularize / pluralize (`eggs` → `egg`) | English rules, wrong for `dahi`, `paneer`, `roti`, `sabji` |
| strip accents / diacritics (`purée` → `puree`) | a language judgment, not a spelling one |
| tokenize | changes the unit of comparison |
| substring / token containment (`grilled chicken` ⊃ `chicken`) | softens `lookup_events` from exact to fuzzy — see below |
| synonym expansion / alias mapping (`murgh` = `chicken`) | domain vocabulary; TODOS.md already rejected static tables |
| fuzzy / edit-distance matching | non-deterministic ranking below the tool-call boundary |
| spell correction | language knowledge |
| transliteration / translation (`dahi` = `curd`) | language knowledge |
| NFKC compatibility folding | maps *different* characters together (step 1) |
| remove **internal** punctuation | `low-fat`, `sugar-free`, `omega-3`, `B-12` — the hyphen is part of the token |

Every row is either Phase 5's canonicalization engine (§8) or explicitly rejected there. They are
excluded for one shared reason: each requires **language or domain knowledge**, which would put
interpretation below the tool-call boundary and break ADR-14's "the engine never interprets
language" invariant. Case folding and whitespace collapse are not interpretation; these are.

**This list is enforced by tests, not by convention** (§7): each non-goal has an assertion that the
corresponding match does *not* occur, so a well-meaning "improvement" fails CI rather than silently
widening the stop-gap into half of Phase 5 in the wrong layer.

#### Worked examples

| Input | → | |
|---|---|---|
| `"Chicken"` | `chicken` | case fold |
| `"chicken,"` / `"(chicken)"` | `chicken` | edge punctuation |
| `" grilled  chicken "` | `grilled chicken` | trim + collapse |
| `"low-fat"` / `"Omega-3"` | `low-fat` / `omega-3` | **internal punctuation preserved** |
| `"purée"` | `purée` | **accent preserved** |
| `"eggs"` | `eggs` | **not** `egg` |
| `"..."` | *rejected* | normalized-empty |

Two of the non-goals deserve their reasoning spelled out, because both look like harmless wins:

- **Why internal punctuation survives while internal whitespace collapses.** The asymmetry is
  principled, not inconsistent: multiple spaces carry no meaning, so collapsing them is hygiene —
  but a hyphen can *be* the token (`low-fat`, `sugar-free`, `omega-3`, `whole-wheat`, `B-12`).
  Deciding `low-fat` ≡ `lowfat` is a meaning judgment.
- **Why substring containment is excluded** (the "see below" in the table). It is subtler than the
  others and architecturally worse: `lookup_events` is documented as *exact* containment, and that
  precision is exactly what makes it the trustworthy counterpart to the fuzzy vector path. Blurring
  it collapses the exact-vs-semantic split 06's retrieval design deliberately maintains, leaving
  two fuzzy paths and no reliable one.

**Query-time only, by design.** The stop-gap lives entirely in `engine/retrieval.py` and touches
no write path. That costs the JSONB index on this filter (irrelevant at a few hundred rows, and
consistent with how [vector-index-and-filtered-knn.md](vector-index-and-filtered-knn.md) already
documents index non-use) and buys the property that matters: **it is deletable in one commit** when
Phase 5 lands, rather than entangled in ingested data.

## 5. Risk analysis

**[P0 — correctness] Duplicate memories.** `ingest_text` has no dedup by design, so any resume or
regeneration path that mis-identifies a committed record doubles its rows — directly inflating the
aggregates Story A rests on. The one failure mode that makes the demo's core claim *look* wrong
while the pipeline reports success. **Mitigated by:** §4.3's post-commit-only ledger writes,
`record_id` derived from stable coordinates rather than content, the mechanically-checked ID
invariant, `--rebuild-ledger`, and M5's forced-double-run test.

**[Correctness — closed by design] Regeneration-induced duplicates.** Surfaced while designing the
correction workflow: content-hash keying would have made an ordinary markdown edit a
duplicate-generating operation. Closed by §4.3's stable `record_id` + §4.12's drift-detection
path. Recorded because the *original* review got this wrong.

**[Data integrity] Converter determinism is load-bearing.** If the converter is not
byte-deterministic, every regeneration reports the whole dataset as "changed" (§4.12) and an
`--apply-corrections` run would mass-supersede it. **Mitigated by:** determinism as a stated
requirement with its own M1 test, and the flag gate so drift is never acted on automatically.

**[Cost/schedule] The cache only protects re-runs.** The first full run pays every extraction and
embedding call. At ~430 records (~120 on the extraction path after §4.11) this is affordable, but
nothing in the docs addresses Bedrock rate limits over a sustained sequential run — a
backoff/retry posture is needed, not just a cache.

**[Cost — verified in code] Opportunistic backfill compounds during replay.**
`_opportunistic_backfill` fires after *every* ingest call and each firing scans up to
`backfill_batch` (default 32) other NULL-embedding rows, calling `embed()` again if any exist.
Correct Phase 2 behavior, but its multiplier at replay volume has never been measured.
**Mitigation available:** run `cli/backfill.py` once at the end instead of paying the sweep per
record. Measured in M6 (§7).

**[Recall quality — new] Near-identical expanded summaries may crowd vector top-k.** ~30 rows with
the same "4 eggs + 200g dahi" summary all embed to nearly the same vector, so a food query could
return one period's days instead of a diverse history. Not blocking; measured in M6, and the
structured `lookup_events` path is unaffected.

**[Performance] No parallelism.** Every record is a sequential round trip. Fine at demo volume,
but T12's latency profile covers single turns, not a bulk run — M6 measures it.

**[Race condition — low] Concurrent live traffic during replay.** No coordination beyond
CockroachDB's transaction isolation. Structurally fine (append-only inserts, no shared mutable
state), but an operating assumption worth stating: run replay against a quiet account.

**[Resolved] Entity canonicalization scope conflict.** The original review flagged this as an
ACCEPTED decision in TODOS.md that was absent from the roadmap and T8's estimate. **Resolved
2026-07-30:** deferred to Phase 5, with §4.13's stop-gap landing now. TODOS.md's "cheapest window"
argument assumed re-extracting 6–12 months of history; measured against the real dataset it is
~120 extraction-path records, so later re-extraction is affordable. See §8.

**[Doc conflict — minor]** [08-roadmap.md](../office-hours/08-roadmap.md) splits replay into
"~3 months" (M1) then "full 6–12 months" (M2); `implementation-roadmap.md` folds both into Phase 4.
Not substantive (Phase 4 ≈ M1 per the mapping table; M2's bullet is "run the same CLI with more
data"). Worth one reconciling line whenever either doc is next touched.

## 6. Milestone plan

Restructured from the original five once the converter became a first-class deliverable. Six
milestones, each independently reviewable and testable — the Phase 3 M1–M6 rhythm.

| | Milestone | Depends on |
|---|---|---|
| M1 | Dataset contract + converter (pure) | — |
| M2 | Extraction cache | — |
| M3 | Resume ledger | — |
| M4 | Engine changes: direct-ingest, note confidence, normalization | — |
| M5 | Replay main loop + failure artifact + corrections | M2, M3, M4 |
| M6 | Production run + OQ5 verification | M1, M5 |

**M1 — Dataset contract + converter** *(pure: no DB, no model)*
- Objective: record + manifest models (§4.1); markdown → JSONL converter implementing Rules 1–3,
  the type mapping, `expanded_from`, and deterministic `record_id`.
- Files: `cli/replay_dataset.py`, `cli/convert.py`, `cli/tests/test_convert.py`
- Tests: **byte-determinism** (same input → identical output, twice); Rule 1 (quantified expands,
  qualitative and background do not); Rule 2 (weekly cadence yields weekly records, *not* daily —
  the fabricated-dose guard); Rule 3 (`ongoing` clamps at `replay_cutover_date`); `record_id`
  stability across text edits; `expanded_from` present on every synthetic row and absent on point
  events; type mapping incl. `medication → supplement/category=prescription`;
  `--force` required to overwrite; malformed markdown → explicit error, never a silent skip
- Risks: expansion-rule edge cases; determinism regressions
- Verification: `pytest cli/tests/test_convert.py`; eyeball the ~430-record output against
  `timeline-entries.md`
- Commit: `feat(cli): reconstruction → JSONL converter + dataset contract (T8 M1)`

**M2 — Extraction cache**
- Objective: `cached_extract(...)` wrapping `model.extract_events` (§4.2)
- Files: `cli/replay_cache.py`, `cli/tests/test_replay_cache.py`
- Tests: miss → 1 extract call, populates; hit → 0 calls; key changes on each of
  `text`/`now`/`tz`/`model_id`/`prompt_version` → miss; **the `[]`-vs-`ExtractionError` contract
  survives a cache round trip** (the D1 silent-loss guard)
- Risks: flattening the empty-result contract
- Verification: `pytest cli/tests/test_replay_cache.py`
- Commit: `feat(cli): replay extraction cache (T8 M2)`

**M3 — Resume ledger**
- Objective: `ReplayLedger` — `mark_done`, `is_done`, `content_hash` drift, `memory_ids`, record
  snapshot, `rebuild_from_db` (§4.3)
- Files: `cli/replay_ledger.py`, `cli/tests/test_replay_ledger.py`
- Tests: mark → is_done roundtrip; drift detection (same ID, different hash → `corrected`);
  `rebuild_from_db` reconstructs state from real `source='replay'` rows; missing/corrupt file →
  explicit fresh start, not a crash; **`record_id` recomputation rejects an ID the converter could
  not have produced**; `[→PERF]` rebuild scan at ~2000 rows
- Risks: the post-commit write-ordering invariant
- Verification: `pytest cli/tests/test_replay_ledger.py` against real CockroachDB
- Commit: `feat(cli): idempotent replay resume ledger (T8 M3)`

**M4 — Engine changes** *(two independent units; keep them separately reviewable)*
- Objective: (a) `ingest_events` + the shared (B)–(F) tail (§4.11) and the note-confidence
  parameter (§4.6); (b) `normalize_item` in retrieval (§4.13)
- Files: `engine/ingestion.py`, `engine/retrieval.py`, `engine/tests/test_ingestion.py`,
  `engine/tests/test_retrieval_*.py`
- Tests: direct path shares transaction/receipt/backfill semantics with `ingest_text`
  (**property: both paths produce identical row shape for equivalent input**); direct-path
  validation failure is **fatal**, no note written; note-confidence hint honored for replay and
  **`_NOTE_CONFIDENCE = 1.0` unchanged for live chat (regression-critical)**; `normalize_item`'s
  three contract properties (**idempotence — including an input whose casefold denormalizes, the
  step-3 guard** — purity/determinism, symmetric application), every example row of §4.13, and
  **one assertion per §4.13 non-goal** proving the match does not occur
- Risks: touching a write path 255 tests depend on — the full suite must stay green
- Verification: full `engine/` + `agent/` + `api/` suite
- Commit: `feat(engine): direct-ingest entry point, replay note confidence, item normalization (T8 M4)`

**M5 — Replay main loop**
- Objective: iterate records, route by `kind`, skip/ingest/report-corrected per §4.12, mark ledger
  post-commit, §4.10 halt + failure artifact, `--apply-corrections`, `--rebuild-ledger`
- Files: `cli/replay.py`, `cli/tests/test_replay.py`
- Tests: small fixture end-to-end against `FakeModelProvider` + real DB; **interrupt after record
  N, resume — records 1..N do not re-trigger `extract_calls`**; second full run → 0 extract calls,
  0 new rows; **forced double-run produces no duplicate rows (the P0 guard)**; corrected record →
  reported with a diff and *not* applied without the flag; with the flag → new rows active, old
  `status='superseded'` with `superseded_by` set, one transaction; halt at 5 consecutive, resumable;
  failure artifact contains every §4.10 field; provenance is `replay`/`reconstructed` on every row
- Risks: highest-integration milestone — §4.3, §4.10, §4.11, §4.12 all meet here
- Verification: `pytest cli/tests/test_replay.py`; manual dry run on a 10-record slice
- Commit: `feat(cli): replay main loop — idempotent resume + corrections (T8 M5)`

**M6 — Production run + OQ5**
- Objective: convert and replay the real reconstruction; verify Story A's numbers in the DB
- Files: none (operational) — optionally `docs/replay-run-log.md`
- Tests: n/a — the DoD is a query
- Measurements (feeding T12 and §5): bulk wall-clock; opportunistic-backfill call count; recall
  top-k diversity across expanded periods
- Risks: the OQ5 go/no-go. A "no" sends work to Phase 0's fallback story (Story C), not to this
  phase's code
- Verification: `aggregate_memories` / `lookup_events` return Story A's real values
  (Vit D 6.20 → 38.4, B12 152 → 752, dated 2026-03-25 / 2026-07-03)
- Commit: `docs: Phase 4 replay run + OQ5 resolution`

## 7. Testing strategy

Replaces [12-test-plan.md](../office-hours/12-test-plan.md)'s three-line `cli/replay` stub.

```
[+] cli/convert  (M1)
  ├── byte-determinism: same markdown → identical JSONL, twice
  ├── Rule 1: quantified → expanded · qualitative/background → single record
  ├── Rule 2: weekly cadence → weekly records (NOT daily — fabricated-dose guard)
  ├── Rule 3: 'ongoing' clamps at replay_cutover_date
  ├── record_id stable across markdown rewording
  ├── expanded_from present on synthetic rows, absent on point events
  ├── type mapping incl. medication → supplement/category=prescription
  ├── refuses to overwrite without --force
  └── malformed markdown → explicit error, never a silent skip
[+] cli/replay_cache  (M2)
  ├── miss → 1 extract call · hit → 0 calls
  ├── key varies on text/now/tz/model_id/prompt_version
  └── []-vs-ExtractionError contract survives a cache round trip (D1 guard)
[+] cli/replay_ledger  (M3)
  ├── mark_done → is_done roundtrip
  ├── drift: same record_id + different content_hash → 'corrected'
  ├── rebuild_from_db reconstructs from real source='replay' rows
  ├── missing/corrupt ledger → explicit fresh start, not a crash
  ├── record_id recomputation rejects a non-derivable id
  └── [→PERF] rebuild scan at ~2000 rows
[+] engine/ingestion + retrieval  (M4)
  ├── PROPERTY: direct and extract paths yield identical row shape
  ├── direct-path validation failure is FATAL (no note written)
  ├── note-confidence hint honored for replay
  ├── live chat unchanged — _NOTE_CONFIDENCE=1.0 regression guard
  ├── normalize_item CONTRACT
  │     ├── PROPERTY: idempotent — normalize(normalize(x)) == normalize(x)
  │     │        (incl. inputs whose casefold denormalizes — the step-3 guard)
  │     ├── PROPERTY: pure + deterministic — repeated calls, no locale dependence
  │     ├── every §4.13 example row (case, whitespace, edge punctuation)
  │     ├── applied symmetrically to search term AND stored value
  │     └── normalized-empty term → rejected (ADR-14.11)
  └── normalize_item NON-GOALS — one assertion per §4.13 row, each proving
        the match does NOT happen (a widening "improvement" fails CI):
        stem · plural · accent-strip · tokenize · substring · synonym ·
        alias · fuzzy · spell-correct · transliterate · NFKC ·
        internal-punctuation removal
[+] cli/replay main loop  (M5)
  ├── end-to-end fixture → correct memories + notes
  ├── interrupt after N, resume: 1..N not reprocessed (extract_calls flat)
  ├── second full run: 0 extract calls, 0 new rows
  ├── forced double-run → NO duplicate rows  ◄── the P0 guard
  ├── corrected record reported with diff, NOT applied without the flag
  ├── --apply-corrections → new rows active, old superseded + superseded_by, one txn
  ├── halt at 5 consecutive failures, resumable
  ├── failure artifact carries every §4.10 field
  └── provenance: every row source='replay', provenance='reconstructed'
[+] measurements  (M6)
  ├── [→PERF] bulk-run wall clock
  ├── [→PERF] opportunistic-backfill call count during a bulk run
  └── [→PERF] recall top-k diversity across expanded periods
```

`[→PERF]` items are measurements this phase produces (feeding T12), not pass/fail gates.

## 8. Resolved decisions & Phase 5 handoffs

All four open questions from the 2026-07-29 review are resolved.

| # | Question | Resolution |
|---|---|---|
| Q1 | Replay input format | JSONL + sidecar manifest, converter-generated from the Phase 0 reconstruction; expansion Rules 1–3; type mapping; DOB supplied so the `lifelong` marker is withdrawn (§4.1) |
| Q2 | Ledger design | Local ledger file + `--rebuild-ledger`; explicit `record_id` supersedes content-hash keying (§4.3) |
| Q3 | Canonicalization timing | **Deferred to Phase 5**; §4.13's normalization stop-gap lands now |
| Q4 | Halt threshold | 5 consecutive, `--force` override (§4.10) |

### Handed to Phase 5

**1. Entity canonicalization** (TODOS.md). The extractor emitting `canonical` alongside `logged`.
TODOS.md's "cheapest window" argument assumed re-extracting 6–12 months of history; measured
against the real dataset that is ~120 extraction-path records, so later adoption is affordable.
§4.13's stop-gap closes the casing/whitespace half now. The correctness case remains real: until
canonicalization lands, `"when did I last eat chicken?"` can miss a replayed `"Grilled Chicken"`.

**2. Period-aware aggregation — the known-better architecture.** §4.1's synthetic daily expansion
is a deliberate **replay-scoped tactic**, not the architecture's opinion about periods. The
principled design stores every period as one row (`{start, end, cadence, per-occurrence payload}`)
and lets `aggregate_memories` materialize occurrences on the fly for the queried window:

| | §4.1 expansion (Phase 4) | Period-aware aggregation (better) |
|---|---|---|
| Storage | O(days) | O(periods) — any duration |
| ADR-4 | honest only via `expanded_from` + lowered confidence | nothing synthetic ever written |
| Recall quality | near-identical summaries crowd top-k (§5) | one row per period |
| Cost | small converter | modifies Phase 3's aggregation builders; needs a double-count rule |

Deferred on **scope, not merit**: it touches builders behind 255 passing tests, needs its own ADR,
and requires solving a genuinely hard sub-problem — *if a user live-logs "4 eggs" on a day inside a
"4 eggs daily" period, does it count once or twice?*

**3. Long-lived periods stated in live chat — a real gap that exists today.** A deployed user
saying *"I've been vegetarian since birth, I was born 2007-01-29"* hits live `ingest_text`. It
cannot expand to ~7,000 rows (that would blow the single-transaction rule, the latency budget, and
never-lose-input at once), so it must produce **one** period-bearing memory — which aggregations
will not see. For a background state that is correct. For a *quantified* habit stated in chat
("I eat 4 eggs every day") it is a confidently-incomplete answer, the failure class the glass box
exists to prevent. Handoff 2 is what fixes it.

## Maintenance notes

- **When M1–M6 land**, promote §4 into a new ADR in
  [09-decisions.md](../office-hours/09-decisions.md) and update this document's header, rather than
  leaving two documents both claiming to be canonical.
- Do **not** relax §4.3's post-commit ledger invariant to simplify the main loop. That single
  ordering rule is what keeps a resumed run from silently duplicating memories, and duplicates in
  the reconstructed history corrupt the aggregates the money question depends on.
- Do **not** let `record_id` become content-derived again. §4.3 explains the exact duplicate path
  that revision closed; it is not a stylistic preference.
- Do **not** batch multiple ingest calls into one DB transaction to speed up a bulk run — that
  reintroduces the C-SPANN batch-insert footgun the T1 canary and
  `ingestion-transaction-boundaries.md` both guard.
- Do **not** make `--apply-corrections` automatic, and do not weaken the converter's determinism
  requirement. The two together are what keep a subtle formatting change from mass-superseding the
  dataset (§5).
- §4.13's normalization must stay **query-time and write-path-free**, so Phase 5 can delete it in
  one commit. Its non-goal list is a **contract, not a backlog** — every entry is enforced by a
  test asserting the match does *not* happen. Adding plural, substring, synonym, or accent handling
  is implementing half of Phase 5 in the wrong layer, and CI will say so.
- Do **not** remove the second NFC pass in §4.13 step 3 as redundant. Case folding is not closed
  under normalization (UAX #15); that pass is the whole reason the idempotence guarantee holds, and
  the input that breaks it is rare enough to survive casual testing.
- If §4.11's shared tail is ever forked into two implementations, the never-lose-input guarantee
  stops being one testable property. Keep the single tail.

## Related files

| File | Relationship |
|---|---|
| `engine/ingestion.py` | The write path; M4 adds `ingest_events` + the note-confidence parameter and lifts the shared (B)–(F) tail |
| `engine/repository.py` | Row-at-a-time inserts; `mark_superseded` is §4.12's correction primitive |
| `engine/retrieval.py` | Home of §4.13's query-time normalization stop-gap |
| `engine/model.py` | `ModelProvider` contract, incl. the empty-result semantics §4.2's cache must preserve |
| `engine/types.py` | Stage-(B) validation both entry points share; unchanged by Phase 4 |
| `cli/backfill.py` | The sibling CLI pattern replay's composition root mirrors; §5's end-of-run mitigation |
| `cli/tests/conftest.py`, `engine/tests/conftest.py` | `FakeModelProvider` call counters used throughout §7 |
| `docs/evidence/timeline-entries.md` | The Phase 0 reconstruction — canonical for **facts** (§4.12). **Local-only, gitignored** (ADR-7); not present in a clone |
| [ingestion-transaction-boundaries.md](ingestion-transaction-boundaries.md) | The transaction-boundary and never-lose-input spec this document extends into batch territory |
| [graph-state-durability.md](graph-state-durability.md) | Precedent for §4.3's "the guard, not the convention, is the guarantee" posture |
| [TODOS.md](../../TODOS.md) | Note confidence (§4.6, now decided) and entity canonicalization (§8, deferred to Phase 5) |
| [09-decisions.md](../office-hours/09-decisions.md) | Destination for the ADR §4 becomes once implemented |
