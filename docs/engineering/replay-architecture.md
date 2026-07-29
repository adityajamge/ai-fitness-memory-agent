# Replay Architecture: Phase 4 History Bootstrap — Design Review

> Pre-implementation architecture review for **T8** (replay CLI,
> [11-implementation-tasks.md](../office-hours/11-implementation-tasks.md#task-list)) and
> **Phase 4** ([implementation-roadmap.md](../implementation-roadmap.md#phase-4--history-bootstrap-replay-3-5-days-includes-human-reconstruction-time)).
> Written 2026-07-29, before any replay code exists, following the same review discipline used
> ahead of Phase 2 and Phase 3. **This is the canonical reference for Phase 4 implementation** —
> new code, task updates, and code comments should link here rather than re-derive this
> reasoning. Companion doc:
> [ingestion-transaction-boundaries.md](ingestion-transaction-boundaries.md) (the write-path
> spec every replay call obeys unmodified). Related: [TODOS.md](../../TODOS.md) (note-fallback
> confidence and entity canonicalization — both examined here in full, kept short there).
>
> **Status of the decisions below:** proposed and reviewed, not yet locked. Items marked
> **OPEN** in §8 need an explicit call before implementation starts. Once decisions are made
> during implementation, promote the final shape into
> [09-decisions.md](../office-hours/09-decisions.md) as a new ADR — mirroring how ADR-14
> absorbed the in-flight decisions Phase 3 made — and mark this document accordingly.

## 1. Why this document exists

Phase 4 is not a new memory capability — no new schema, no new retrieval path, no new agent
tool. It is **the existing Phase 2 write path (`IngestionService.ingest_text`) used as a
batch, long-running, interruptible client**: the same call a chat turn makes, called hundreds
to thousands of times in a row, with two requirements a single interactive request never had —
don't re-pay for work already done, and don't lose your place if the process dies at record 400
of 1800. That framing is what makes this a design review rather than a straightforward "write a
CLI" task: nearly every real risk in this phase is a *durability/idempotency* problem, not an
extraction-quality problem.

## 2. Objective, scope, and what this phase is not

**Objective:** push 6–12 months of the builder's own LLM-assisted-reconstructed health history
through the production ingestion pipeline, so the account stops being empty and the causal story
the demo depends on (OQ5,
[10-open-questions.md](../office-hours/10-open-questions.md)) becomes verifiable in the database.

**Why it exists** (two independent reasons, both already named elsewhere in the docs):

1. The money-question demo is unanswerable over a few days of live-logged data — it needs
   months of dated history, and ADR-4 bans both synthetic personas and raw SQL seeding as ways
   to get it.
2. The Phase 3 engineering review flagged replay as the dominant Bedrock cost and schedule risk
   (outside-voice finding #7) — every reconstructed record needs a real extraction call, so
   caching and resumability are not conveniences, they are what makes iterating on the
   reconstruction affordable at all.

**User-visible outcome:** none directly. Phase 4 ships no new UI or API surface; the outcome is
entirely inside the database. Afterward, the existing Phase 3 chat answers "protein in June?"
and the money question with real, dated, cited evidence instead of "nothing logged."

**Architecture impact:** minimal by design — one new component (`cli/replay.py` plus small
helper modules for the cache and resume ledger). Everything downstream of `ingest_text`
(validation, failure policy, transaction boundary, embeddings) is reused exactly as Phase 2
built it. If this phase touches `engine/ingestion.py` at all, it should be one small, additive
parameter (see §4.6), not a rework.

## 3. Architecture map

### What already exists and is directly reusable

- `IngestionService.ingest_text(user_id, text, *, source="chat", provenance="live", now=None, tz=None) -> Receipt`
  already accepts everything replay needs to override. Nothing to add for the happy path.
- The entire Phase 2 failure policy (extraction fail → note, validation fail → note,
  never-lose-input) applies automatically. Replay needs no error handling of its own for
  "extraction failed" — that is Phase 2's job, already implemented and tested.
- `insert_memories`/`insert_memory` (`engine/repository.py`) are row-at-a-time by design,
  already guarding the C-SPANN batch-insert footgun. Replay must call into this, never around
  it, and must never try to "optimize" it into a multi-row batch insert.
- `FakeModelProvider` with `extract_calls`/`embed_calls` counters
  (`engine/tests/conftest.py`) is exactly the fixture needed to assert "second run makes zero
  model calls."
- The `cli/` composition-root pattern (`cli/migrate.py`, `cli/backfill.py`):
  `load_settings() → Database → IngestionService`, argparse entry point,
  `if __name__ == "__main__"`. Replay should read as a sibling of `backfill.py`, not a new
  pattern.
- The schema already anticipates this feature: the `source` column comment
  (`04-database-design.md`) lists `'replay'` as a valid value, `provenance` already has
  `'reconstructed'`, and the bi-temporal `event_time`/`created_at` split (ADR-5) was adopted
  specifically because reconstructed memories need it.

### What must not change

- **The transaction boundary.** One `ingest_text` call = one transaction; no model call ever
  runs inside a transaction (`ingestion-transaction-boundaries.md` rule 1). A "batch-insert the
  whole replay file in one transaction" design would violate this and resurrect the exact
  vector-index batch footgun the T1 canary exists to catch.
- **No deduplication inside `ingest_text`.** This is defined, tested behavior — double-submit
  produces two rows, deliberately. Replay cannot lean on the engine to protect it from
  double-processing; that protection must live entirely in the CLI (§4.3).
- **The `ModelProvider.extract_events` empty-result contract** (`[]` = affirmed no-op,
  `ExtractionError` = everything else, `engine/model.py`). An extraction cache that flattens
  this distinction would silently reintroduce the exact silent-loss bug D1 closed in Phase 2
  (`ingestion-transaction-boundaries.md` §13).

### The contract replay must call, per record

```python
receipt = ingestion_service.ingest_text(
    user_id, text,
    source="replay", provenance="reconstructed",
    now=record.event_time,   # the record's OWN historical timestamp, never wall-clock now
    tz=record.tz,
)
```

Already proven end-to-end — `ingestion-transaction-boundaries.md` states outright that the
replay CLI "can now push `source='replay'`, `provenance='reconstructed'` through `ingest_text`
and every outcome... stays reconstructed." Nothing about this call needs inventing.

## 4. Design decisions

Ten decisions. Four (4.4, 4.5, 4.7, 4.8) are already settled by existing ADRs/code and listed
so the plan doesn't silently reinvent them. The rest carry alternatives, trade-offs, and a
recommendation; **OPEN** ones are repeated in §8 for explicit sign-off.

### 4.1 Replay input format — **OPEN**

| Option | Trade-off |
|---|---|
| **A1. JSONL, one record per line** — `{text, event_time_hint, tz, confidence_hint, source_note}` | Keeps the CLI deterministic; hands it an already-dated record. |
| A2. Narrative text files, CLI splits by date | Pushes NL-adjacent parsing into a deterministic tool — violates the "engine never interprets language" posture the codebase holds elsewhere (the canonicalization decision, ADR-14.10). |
| A3. Raw chat-shaped strings, no metadata | Loses the historical anchor — extraction's relative-date resolution ("yesterday") has nothing to anchor to, defeating half of ADR-5's bi-temporal design. |

**Recommendation: A1.** Reconstruction (the judgment-heavy work) stays the separate step
already planned in Phase 0 ("raw reconstruction inputs gathered locally"); the CLI's only job
is to replay already-dated, already-typed records through the production pipeline, exactly as
[03-memory-engine.md §2](../office-hours/03-memory-engine.md#2-seed-replay-reconstruction)
describes it.

**Why still open:** the actual shape of the builder's raw reconstruction output isn't known to
this review. If it already exists in some other structured form, A1's schema needs to match
reality rather than being designed in the abstract.

### 4.2 Extraction cache

| Option | Trade-off |
|---|---|
| **B1. Local file (JSON/SQLite), key = hash(text, now, tz, model_id, prompt/cache-version)** | Cheapest; matches actual usage (local iteration, then one production run); trivially inspectable/deletable. |
| B2. A `replay_cache` DB table | New table + migration for a pure dev-iteration aid — infra for its own sake, the exact thing ADR-3's posture rejects. |
| B3. No cache | Defeats the entire point of T8 (replay is "the dominant Bedrock cost line," outside-voice finding #7). Not a real option. |

**Recommendation: B1**, with the cache key including a **prompt/cache-format version stamp**,
not just the model ID — a prompt change then invalidates automatically instead of silently
serving stale extractions. This is the sharpest available cache bug and costs one extra field
to avoid.

### 4.3 Idempotent resume — the load-bearing decision

| Option | Trade-off |
|---|---|
| **C1. Local resume ledger, separate from the extraction cache, keyed by input-record identity, written only after `ingest_text` returns** | No schema change; mirrors how `cli/backfill.py`'s own idempotency already works (query DB state, not a uniqueness constraint). |
| C2. Add an `external_ref` dedup column to `memories` | Touches a table 255 existing tests assume a shape for, to solve a problem that is local to one CLI. |
| C3. Match on `(user_id, source, event_time, summary)` before inserting | Fragile: legitimately similar records collide; re-extraction wording changes (a cache-version bump) make a record "look new" and duplicate anyway. |

**Recommendation: C1, plus a companion `--rebuild-ledger` mode** that walks existing
`source='replay'` rows for the user and reconstructs which input records are already
represented. A bare append-only ledger file is a single point of failure — lose it and the next
run has no way to know what's committed. A DB-derived rebuild path removes that failure mode
for one extra CLI command.

**The one invariant this decision lives or dies on:** a ledger entry is written **strictly
after** `ingest_text` returns — never before, never batched. This is the same "receipt only
after commit" rule Phase 2 already lives by
(`ingestion-transaction-boundaries.md` rule 4), applied to a second, CLI-owned durability
record. See §5 for why this is the single highest-severity risk in the phase.

**Record-key stability:** use a content-hash of the raw input record, not its line number/file
position — so appending to or reordering the input file never invalidates prior progress.

### 4.4 Batching — settled, reuse as-is

Row-at-a-time inserts, one transaction per record, already enforced at the repository layer. No
new decision. The only new batching knob is progress/checkpoint-flush granularity, covered in
§4.9.

### 4.5 Provenance — settled, reuse as-is

`source="replay"`, `provenance="reconstructed"` — already wired end-to-end through
`ingest_text`, `_persist_note`, and `reprocess_note` (D3 fix, closed 2026-07-21). No new
decision; replay just passes the two literal values.

### 4.6 Confidence (note-fallback during replay) — needs closing, tracked in [TODOS.md](../../TODOS.md)

| Option | Trade-off |
|---|---|
| F1. Flat reconstructed-note confidence (e.g. 0.6), hardcoded | ~15 min, but every reconstructed note gets the same number regardless of how confident that specific reconstruction actually was. |
| **F2. Thread a per-record confidence hint through `ingest_text` → note-fallback** | More honest; costs one optional parameter on `ingest_text`/`_persist_note`, defaulting to `None` so live-chat behavior (`_NOTE_CONFIDENCE = 1.0`) is unchanged. |
| F3. Leave `_NOTE_CONFIDENCE = 1.0` unchanged (do nothing) | Already flagged in TODOS.md as a live honesty bug once T8 exists — a reconstructed note claiming full certainty of LLM-reconstructed text contradicts the documented column semantics ("1.0 for directly observed live data," `04-database-design.md`). |

**Recommendation: F2.** TODOS.md itself names this the better option, deferred only because
"only T8 will have" the value — §4.1's `confidence_hint` field gives T8 that value for free.
Doing F1 now means redoing this once §4.1 lands anyway.

### 4.7 Duplicate detection — settled, reuse §4.3

Same underlying mechanism as the resume ledger — it *is* the duplicate-detection mechanism for
replay-triggered duplicates. `ingest_text`'s own no-dedup behavior stays correct and untouched
for live chat; replay does not get a second, engine-level dedup feature.

### 4.8 Transaction boundaries — settled, reuse as-is

One `ingest_text` call = one transaction, exactly as Phase 2 built it. Ledger writes (§4.3)
happen in the CLI's own process, strictly after commit, never inside the same DB transaction —
the same "own short transaction, best-effort" pattern `backfill_embeddings` already uses.

### 4.9 Progress tracking

| Option | Trade-off |
|---|---|
| **I1. Flush ledger + log progress after every record** | Simplest and safest; a crash re-processes at most the in-flight record. |
| I2. Batch ledger writes every N records | Fewer file writes, but a crash between flushes silently re-processes up to N records — reintroducing exactly the duplicate risk §4.3 exists to prevent. |

**Recommendation: I1.** At replay's expected volume (hundreds to low thousands of records, each
already paying a network round-trip for extraction), the extra file write per record is noise.
I2 trades that noise for a correctness gap that is not worth it.

### 4.10 Failure recovery (run-level) — needs closing

Record-level failure is already Phase 2's job (extraction/validation failure → note,
"processed" for the ledger). The open run-level question: what happens on sustained failure
(Bedrock throttling hard, expired credentials)?

| Option | Trade-off |
|---|---|
| J1. Never halt — every record either succeeds or degrades to a note | A transient outage silently turns the whole historical import into a wall of unparsed notes — recoverable via `reprocess_note`, but a bad first state for exactly the data the demo depends on. |
| **J2. Halt after N consecutive record-level failures, resumable, with a `--force` override** | Stops burning cache/ledger churn on a broken run; resuming is free (§4.3 already makes it so). |

**Recommendation: J2**, default threshold low (e.g. 5), overridable for the deliberate case
(testing the note-fallback path itself).

## 5. Risk analysis

Ranked by severity.

**[P0 — correctness] Duplicate memories on naive resume.** If resume state is not derived
strictly from post-commit ledger writes (§4.3), any crash-and-restart on a partially processed
file re-calls `ingest_text` for already-committed records. Since `ingest_text` has no dedup by
design, this silently doubles those rows — directly inflating the `SUM`/aggregate numbers the
money question depends on. This is the one failure mode that can make the demo's core claim
*look* wrong even though the pipeline "worked." §4.3 and §8's Q2 exist because of this.

**[Scope conflict — resolve before estimating this phase] Entity canonicalization.**
[TODOS.md](../../TODOS.md) records this as an **ACCEPTED** architectural decision — adopting it
before replay canonicalizes all 6–12 months of reconstructed history for free on first
ingestion; adopting it after means re-extracting history — and states it should land "with or
just before T8." Verified in code (`engine/types.py`, `agent/providers/bedrock.py`): **not
implemented**. It is **absent** from `implementation-roadmap.md`'s Phase 4 deliverables/DoD and
from T8's own backlog entry and estimate. Right now this requirement lives only in TODOS.md,
invisible to anyone reading the roadmap or backlog. See §8 Q3.

**[Cost/schedule] The cache only protects re-runs.** A 6–12 month history at multiple logged
events/day is plausibly 500–2000+ extraction calls plus a matching number of embedding calls on
the **first** full run alone, before any bug forces a second pass. No document addresses
Bedrock rate limits (TPS/TPM) over a sustained sequential run — this needs a backoff/retry
posture, not just an extraction cache.

**[Cost — verified in code] Opportunistic backfill compounds during replay.**
`IngestionService._opportunistic_backfill` fires after every `ingest_text`/`_persist_note` call
(`engine/ingestion.py`), and each firing scans for up to `backfill_batch` (default 32) other
NULL-embedding rows and calls `model.embed()` again if any exist. During a long replay run this
can multiply embedding calls well beyond one-per-record, especially right after any cluster of
embedding failures. This is correct, intentional Phase 2 behavior, not a bug — but its cost
multiplier at replay volume has never been measured. Cheap mitigation: run `cli/backfill.py`
once at the end of a replay run instead of paying the opportunistic sweep on every record
(needs a way to skip it per-call, or the cost can simply be accepted — a one-line decision, not
a redesign).

**[Performance] No parallelism anywhere in the design.** Every record is a sequential round
trip (extract → maybe embed → insert). Likely fine at demo volume, but nothing measures a
*bulk* run specifically — T12's latency profile covers single-turn latency, not a
thousand-record replay's wall-clock time.

**[Race condition — low risk, worth stating explicitly] Concurrent live traffic during
replay.** No coordination exists between a running replay process and the live API process
beyond CockroachDB's own transaction isolation — structurally fine (append-only inserts, no
shared mutable state), but an implicit operating assumption ("run replay against a quiet
account") that should be stated rather than left to be discovered.

**[Data integrity] The ledger is a single point of failure unless it is rebuildable.** Covered
in §4.3 — the DB-rebuild companion command exists specifically to close this.

**[Doc conflict — minor, non-blocking]**
[08-roadmap.md](../office-hours/08-roadmap.md) splits replay into "~3 months" (Milestone 1)
then "full 6–12 months" (Milestone 2); `implementation-roadmap.md` Phase 4 folds both into one
phase. Not a substantive contradiction (Phase 4 ≈ Milestone 1 per the explicit mapping table,
and Milestone 2's replay bullet is really "run the same CLI again with more data" — no new
engineering) — worth a one-line reconciliation note whenever either doc is next touched, so a
future reader doesn't infer two different tools exist.

## 6. Milestone plan

Five milestones, each independently reviewable and testable, following the M1–M6 pattern
Phase 3 used.

**M1 — Replay input contract + extraction cache (pure, no DB writes)**
- Objective: JSONL record schema (§4.1); `cached_extract(text, now, tz, model_id) ->
  list[ExtractedEvent]` wrapping `model.extract_events`.
- Files: `cli/replay.py` (parsing + cache only), `cli/replay_cache.py`,
  `cli/tests/test_replay_cache.py`
- Tests: cache miss → 1 extract call; hit → 0 calls; cache key changes on
  model_id/now/tz/prompt-version → miss; malformed record → explicit parse error, never a
  silent skip
- Risks: over-designing the schema before seeing real reconstruction data (§4.1's open item)
- Verification: `pytest cli/tests/test_replay_cache.py`
- Expected commit: `feat(cli): replay input contract + extraction cache (T8 part 1)`

**M2 — Idempotent resume ledger, rebuildable from the DB**
- Objective: `ReplayLedger.mark_done(record_key)` / `.is_done(record_key)` /
  `.rebuild_from_db(user_id)`
- Files: `cli/replay_ledger.py`, `cli/tests/test_replay_ledger.py`
- Tests: mark→is_done roundtrip; rebuild reconstructs correct state from real `source='replay'`
  rows; missing/corrupted ledger file → explicit fresh start, not a crash
- Risks: record-key stability (mitigated by content-hashing, §4.3)
- Verification: `pytest cli/tests/test_replay_ledger.py` against real CockroachDB
- Expected commit: `feat(cli): idempotent replay resume ledger (T8 part 2)`

**M3 — Replay main loop: wire cache + ledger + `ingest_text` together**
- Objective: iterate records, skip ledger-done ones, call `ingest_text(..., source="replay",
  provenance="reconstructed", now=record.event_time, tz=record.tz)`, mark ledger post-commit,
  consecutive-failure halt (§4.10)
- Files: `cli/replay.py` (main loop), `cli/tests/test_replay.py`
- Tests: small fixture file (5–10 records) end-to-end against `FakeModelProvider` + real db;
  interrupt-simulate (stop after record 3, restart, assert records 1–3 don't re-trigger
  `extract_calls`); full second run after completion → zero new extract calls, zero new memory
  rows; consecutive-failure halt fires and later resumes cleanly; forced double-run over the
  same file never produces two memory rows per input record (the P0 risk, §5)
- Risks: highest-integration-risk milestone — §4.3/4.4/4.6/4.10 all meet here even if each unit
  is individually correct
- Verification: `pytest cli/tests/test_replay.py`; manual dry run against a small real
  reconstructed sample
- Expected commit: `feat(cli): replay CLI main loop — idempotent resume (T8)`

**M4 — Confidence threading + canonicalization (if confirmed in scope, §8 Q3)**
- Objective: land §4.6 (optional confidence-override parameter on `ingest_text`/
  `_persist_note`); if canonicalization is confirmed in-scope, land it here so it is active
  before M5's production run
- Files: `engine/ingestion.py`, `engine/tests/test_ingestion.py`; if included:
  `agent/providers/bedrock.py`, `engine/types.py`, `engine/retrieval.py` + tests
- Tests: confidence override honored for replay, unchanged default for live chat
  (regression-critical); canonicalization (if included): extraction emits `canonical`,
  `lookup_events` matches on it
- Risks: canonicalization is a real scope item wearing a TODO's clothes — keep it a separately
  reviewable unit inside this milestone, don't let it expand silently
- Verification: full `engine/`/`agent/` suite stays green
- Expected commit: `feat(engine): reconstructed-note confidence threading + entity
  canonicalization (T8 close-out)`

**M5 — Production run: bootstrap the builder's account, verify OQ5**
- Objective: run the CLI against the real 6–12 months of reconstructed history; verify the
  causal story's numbers exist in the DB
- Files: none (operational) — optionally `docs/replay-run-log.md`
- Tests: n/a — DoD is a query: the money question's underlying aggregation returns the story's
  real numbers
- Risks: this is the actual OQ5 go/no-go gate; a "no" here sends work back to Phase 0's
  fallback story, not to this phase's code
- Verification: manual — `aggregate_memories` against real data matches the written-down causal
  story
- Expected commit: none required, or `docs: record Phase 4 replay run + OQ5 resolution`

**Recommended order:** M1 → M2 → M3 → M4 → M5, strictly. M3 is the highest-integration-risk
step and should only start once M1 and M2 are independently correct and tested in isolation —
"make the change easy, then make the easy change" applied to the two risky primitives (cache,
ledger) before wiring them into the loop that also touches the real ingestion path.

## 7. Testing strategy

Extends [12-test-plan.md](../office-hours/12-test-plan.md)'s three-line `cli/replay` stub into
the full block:

```
[+] cli/replay_cache (M1)
  ├── cache miss → 1 extract call, populates cache
  ├── cache hit → 0 extract calls, returns cached events
  ├── key changes on (text, now, tz, model_id, prompt-version) → miss
  └── malformed JSONL record → explicit error, not a silent skip
[+] cli/replay_ledger (M2)
  ├── mark_done → is_done roundtrip
  ├── rebuild_from_db reconstructs state from real source='replay' rows
  ├── missing/corrupted ledger file → fresh start, not a crash
  └── [→PERF] rebuild scan time at ~2000-row scale
[+] cli/replay main loop (M3)
  ├── end-to-end small fixture (5-10 records) → correct memories + notes
  ├── interrupt after record N, resume: records 1..N not reprocessed
  │        (extract_calls flat), records N+1.. complete
  ├── second full run after completion: 0 extract calls, 0 new memory rows
  ├── consecutive-failure halt fires at threshold, resumable after
  ├── duplicate-prevention: forced double-run over the same file never
  │        produces two memory rows per input record (the P0 risk, §5)
  └── provenance: every inserted row source='replay', provenance='reconstructed'
[+] engine/ingestion confidence threading (M4)
  ├── replay confidence_hint flows into note-fallback confidence
  └── live chat (no override) unchanged — _NOTE_CONFIDENCE=1.0 regression guard
[+] engine/types + retrieval canonicalization (M4, if in scope)
  ├── extraction payload carries canonical field where applicable
  └── lookup_events matches on canonical, still exact/deterministic
[+] performance (M3/M5)
  ├── [→PERF] wall-clock for a representative bulk run (e.g. 500 records)
  └── [→PERF] opportunistic-backfill call count during a bulk run
      (verifies the §5 cost-compounding finding isn't worse than expected)
```

`[→PERF]` items are measurements this phase should produce (feeding T12's latency profile), not
hard pass/fail budgets Phase 4 needs to hit.

## 8. Open questions

1. **Replay input format (§4.1).** Does the raw reconstruction data already exist in some
   structured shape, or is the JSONL schema net-new design that should be checked against the
   real reconstruction inputs before M1 starts?
2. **Ledger design (§4.3).** Recommended is a local file plus a DB-rebuild companion command. A
   simpler alternative — no separate ledger file at all, always derive resume state from a
   startup query over existing `source='replay'` rows — has one fewer moving part and may be
   sufficient at this data volume. Decide before M2.
3. **Canonicalization timing (§5).** TODOS.md says it must land "with or just before T8"; it is
   absent from the roadmap's Phase 4 scope and estimate. Bundle it into M4, or record an
   explicit, deliberate deferral? Either is fine — leaving it undecided is the one bad option.
4. **Consecutive-failure halt threshold (§4.10).** The default (proposed: 5) is a guess until
   there's a feel for how Bedrock actually behaves under a sustained sequential run.

## Maintenance notes

- Revisit this document once M1–M5 land: promote the locked-in shape of §4's decisions into a
  new ADR in [09-decisions.md](../office-hours/09-decisions.md) (mirroring ADR-14's absorption
  of Phase 3's in-flight decisions), and update this document's header status line rather than
  leaving two documents both claiming to be canonical.
- Do **not** relax the §4.3 ledger invariant ("mark done only after commit") to simplify the
  main loop — that single ordering rule is what keeps a resumed run from silently duplicating
  memories, and duplicates in the reconstructed history corrupt the aggregate numbers the demo's
  money question depends on.
- Do **not** batch multiple `ingest_text` calls into one DB transaction to "speed up" a bulk
  run — this reintroduces the C-SPANN batch-insert footgun the T1 canary and
  `ingestion-transaction-boundaries.md` both guard against.
- If entity canonicalization (§8 Q3) is deferred past Phase 4, note the cost explicitly
  wherever that deferral is recorded: re-extracting already-replayed history to canonicalize it
  later is strictly more expensive than doing it first, even with the extraction cache (a
  prompt change invalidates the cache by design, §4.2).

## Related files

| File | Relationship |
|---|---|
| `engine/ingestion.py` | The write path replay calls unmodified except for §4.6's optional confidence parameter |
| `engine/repository.py` | Row-at-a-time insert guarantee replay depends on |
| `engine/model.py` | `ModelProvider` contract, including the empty-result semantics the extraction cache must preserve |
| `cli/backfill.py` | The sibling CLI pattern replay's composition root mirrors |
| `cli/tests/conftest.py`, `engine/tests/conftest.py` | `FakeModelProvider` call counters used throughout §6's test plan |
| [ingestion-transaction-boundaries.md](ingestion-transaction-boundaries.md) | The transaction-boundary and never-lose-input spec this document extends into batch/replay territory |
| [TODOS.md](../../TODOS.md) | Note-fallback confidence and entity canonicalization — both examined in full here, kept short there |
| [09-decisions.md](../office-hours/09-decisions.md) | Destination for the ADR this document's decisions should be promoted into once implemented |
