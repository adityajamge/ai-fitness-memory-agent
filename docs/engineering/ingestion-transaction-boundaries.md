# Ingestion Transaction Boundaries & the Never-Lose-Input Guarantee

> Engineering deep dive (see [README.md](README.md) conventions). Canonical reference for how
> the Phase 2 write path (`engine/ingestion.py`) uses database transactions and how each
> failure mode interacts with the **never-lose-input** guarantee ([ADR-13.5](../office-hours/09-decisions.md#adr-13)).
> Decisions: [09-decisions.md → ADR-13.5/13.6/13.14](../office-hours/09-decisions.md#adr-13).
> Design context: [03-memory-engine.md → Ingestion](../office-hours/03-memory-engine.md),
> [04-database-design.md](../office-hours/04-database-design.md). Code comments in
> `engine/ingestion.py` should link here rather than restate this.

## 1. Why this document exists

The ingestion pipeline makes two kinds of calls that can fail independently: **model calls**
(extraction, embeddings — over the network, to Bedrock) and **database writes** (to
CockroachDB). "Never lose input" is a product guarantee, but it is only meaningful if the
*exact* interaction between those failures and the transaction boundary is pinned down.
This document is that specification. The code implements it; the tests
([12-test-plan.md](../office-hours/12-test-plan.md), `engine/tests/test_ingestion.py`) assert it.

## 2. The guarantee, stated precisely

> **Never-lose-input:** given a reachable database, every ingest turn durably persists a
> representation of the user's raw input **before the receipt returns** — as typed event
> memories when extraction *and* validation succeed, or as a single `note` memory preserving
> the raw text otherwise. The user always gets an honest receipt describing which happened.

Scope and limits (stated so the demo and README never overclaim — honesty posture, ADR-13.13):

- The guarantee holds against **model/parse failures** (Bedrock throttles, malformed
  extraction output, payload validation failures). These degrade to the note fallback; input
  survives.
- The **only** condition under which nothing is written is **database unavailability**. That
  is an infrastructure failure, not a silent drop: the whole turn rolls back atomically and
  surfaces as a **retriable error** — never a partial or half-recorded write (mirrors the
  turn-commit-failure row of the [failure-modes table](../office-hours/12-test-plan.md), whose
  handling is "single transaction → turn retriable, never half-recorded").
- "Persisted" means committed to CockroachDB. Embeddings are explicitly **not** part of the
  guarantee — they are a nullable enrichment (see §6).

## 3. Hard rules (invariants the code must never violate)

1. **No model call or network I/O inside a database transaction.** Extraction and embedding
   both complete (or fail) *before* the write transaction opens. A transaction holds only
   fast, local DB work — it never blocks on Bedrock. This keeps write transactions short
   (CockroachDB contention, and the ~300ms consolidation budget that rides ingestion in
   Phase 5, both depend on it).
2. **One turn's typed events commit atomically** — all of them, or none. There is no state in
   which a turn is represented by a subset of its events.
3. **A turn is represented in the database exactly once** — either as its typed events *or* as
   one note. Never both (no shadow rows — ADR-13.5 "no shadow note" on the success path).
4. **The receipt is derived from what actually committed**, after commit. A receipt claiming
   `parse_status="ok"` is issued only after the typed-event transaction has committed.
5. **Embedding failure never fails a turn and never rolls back a write.** (§6)
6. **Backfill never affects the turn that triggered it.** It runs after commit, in its own
   transaction(s), best-effort. (§7)

## 4. Pipeline stages and where the boundary sits

```
ingest_text(user_id, text)
│
├─ (A) EXTRACTION            model call, 1 inline retry        ── no DB ──┐
│        │ success → list[ExtractedEvent]                                 │  all model
│        │ failure (ExtractionError after retry) ─────────────► NOTE PATH │  work happens
│        ▼                                                                │  OUTSIDE any
├─ (B) VALIDATION            Pydantic per event (engine/types) ── no DB ──┤  transaction
│        │ all valid → list[Memory]                                       │
│        │ any invalid ──────────────────────────────────────► NOTE PATH │
│        ▼                                                                │
├─ (C) EMBEDDING            model call over summaries          ── no DB ──┤
│        │ success → vectors attached                                     │
│        │ EmbeddingError → vectors = NULL (turn continues) ──────────────┘
│        ▼
├─ (D) ══════════ SINGLE WRITE TRANSACTION ══════════
│        INSERT all typed event memories (row-at-a-time for the vector index)
│        [Phase 6: + turn row + evidence_trace, same transaction — ADR-13.14]
│        COMMIT
│        ▼
├─ (E) build receipt from committed rows   (parse_status="ok")
│        ▼
└─ (F) opportunistic backfill  (post-commit, separate txn, bounded, best-effort — §7)

NOTE PATH:
   (D') ══ SINGLE WRITE TRANSACTION ══  INSERT one `note` memory (raw text in payload)  COMMIT
   (E') build receipt  (parse_status="incomplete", message "saved — parsing incomplete")
```

The transaction boundary is at **(D)/(D')** only. Everything above it is retryable in-memory
work with no durable side effects; everything at it is atomic; everything below it
(receipt, backfill) observes already-committed state.

## 5. Partial extraction and validation failure — the all-or-nothing rule

A turn may extract to several events (though most single turns produce one). Two sub-cases:

- **Extraction returns zero events** for a turn that clearly contained loggable content: this
  is a parse failure → **note path**. (An intentionally contentless turn — e.g. "thanks!" —
  extracting to zero events is a legitimate no-op, not a note; the model interface signals
  the difference by raising `ExtractionError` for the former and returning `[]` for the
  latter. For Phase 2 the conservative default is: empty result on a non-trivial input →
  note.)
- **Extraction returns N events, k of which fail Pydantic validation:** the **entire turn
  falls back to the note path** (raw text preserved), and the validation error is captured for
  observability.

**Decision — validation is part of "extraction success," evaluated all-or-nothing per turn.**
Rationale:

- It keeps the ADR-13.5 model **binary** (typed events *or* note), which is what the test plan
  encodes — no third "partially typed" state to design, render, or explain.
- It never writes a **misleading partial row** (half a meal with a dropped item is worse than
  an honest note that says "parsing incomplete" and can be reprocessed).
- Input is **not** lost: the note preserves the raw text, and `reprocess_note` (§8) is the
  first-class path that turns it into typed events once extraction/validation succeeds — so
  all-or-nothing costs nothing permanently.
- Validation failures are rare by construction: payloads use `extra="allow"` (ADR-13.6), so
  only a *type coercion* failure on a declared hot field or a missing *required* field
  triggers it. Required fields are kept minimal precisely to keep this path narrow.

> This is the one genuine behavior choice in the write path. If per-event partial acceptance
> is ever wanted (write the valid events, note the residual), it is a deliberate future change
> — not the default — because it reintroduces the partially-typed state this rule avoids.

## 6. Embedding failures — nullable, pre-transaction, deferred

Embeddings are computed in stage (C), **before** the write transaction, so a failure is
handled without any rollback:

- `embed(summaries)` is all-or-nothing at the call level: on `EmbeddingError`, **all** of this
  turn's memories are inserted with `embedding = NULL`. (Partial-batch embedding success is a
  possible future optimization; Phase 2 keeps it simple.)
- The typed-event write (D) proceeds normally with NULL embeddings. The turn **succeeds**;
  `parse_status="ok"`. The receipt flags `embedding_pending=true` on the affected refs so the
  UI can say "pending embedding" (failure-modes table: "receipt notes pending embedding").
- The rows are picked up later by **backfill** (§7): opportunistically on the user's next
  ingest turn and via `cli/backfill.py` (T15). The `memories` partial index
  `WHERE embedding IS NULL` makes that scan cheap.

Why embeddings are outside the guarantee: a memory with a NULL embedding is fully usable by
the SQL-aggregation retrieval path (Phase 3); only the *semantic* recall path needs the
vector, and that path already filters out NULL-embedding rows
([06-retrieval-strategy.md](../office-hours/06-retrieval-strategy.md)) until backfill completes.
Losing an embedding degrades recall temporarily; it never loses a memory.

## 7. Backfill transaction semantics

- Runs in stage (F), **strictly after** the turn's write transaction has committed. It reads a
  committed, consistent snapshot; it can never see or corrupt half of the current turn.
- **Bounded** per turn (`backfill_batch`, default 32 rows) so it cannot blow the turn's latency
  budget.
- Each embedding write is its own short transaction (embed off-transaction, then
  `UPDATE … SET embedding = … WHERE id = %s AND user_id = %s`). A failure updating one row
  does not roll back others.
- **Best-effort:** a backfill failure is logged and swallowed — it is *never* surfaced as a
  turn failure, because the turn already succeeded. The rows simply remain NULL for the next
  backfill pass.

## 8. `reprocess_note` — the supersession transaction

`reprocess_note(user_id, note_id)` is the recovery path that upgrades a note into typed
events (invoked opportunistically or from CLI; it is what the test-plan path "retry succeeds →
typed events supersede note" exercises).

- Stages (A)–(C) run on the note's stored raw text, off-transaction, exactly as `ingest_text`.
- **On success**, a **single write transaction** does both: INSERT the new typed event
  memories **and** `UPDATE` the note to `status='superseded', superseded_by=<primary new id>`
  (the first inserted event id, chosen as the chain anchor). Atomic: either the note is
  superseded *and* its typed events exist, or neither — the note is never left dangling as
  `superseded` with no successor, and typed events never appear without retiring the note.
- **On failure** (extraction/validation still failing): **no write at all.** The note stays
  `status='active'` and can be reprocessed again later. Nothing is lost, nothing is duplicated.
- Retraction never deletes (ADR-9): the note row persists as `superseded` — the engine's
  history of having first failed to parse is itself memory.

## 9. Failure-point → outcome matrix

| Failure point | In a txn? | What commits | `parse_status` | User sees | Recovery |
|---|---|---|---|---|---|
| Extraction fails after inline retry | no | 1 note (raw text) | `incomplete` | "saved — parsing incomplete" | `reprocess_note` |
| Extraction returns empty on real input | no | 1 note | `incomplete` | "saved — parsing incomplete" | `reprocess_note` |
| One+ events fail validation | no | 1 note (raw text) | `incomplete` | "saved — parsing incomplete" | `reprocess_note` |
| Embedding fails | no (pre-txn) | typed events, `embedding=NULL` | `ok` | "…(embedding pending)" | backfill / CLI |
| DB write fails (D)/(D') | yes | nothing (rollback) | — (turn errors) | retriable error | resubmit turn |
| Backfill fails (F) | own txn | nothing extra | `ok` (unchanged) | nothing | next backfill pass |
| `reprocess_note` extraction fails | no | nothing | — | note unchanged | reprocess later |
| `reprocess_note` DB write fails | yes | nothing (rollback) | — | note unchanged | reprocess later |

**Silent-loss cells: zero.** Every non-success outcome is either an honest receipt, an
honest retriable error, or a no-op that leaves recoverable state.

## 10. Double-submit (defined behavior)

The write path does **no** deduplication. Submitting the same meal twice produces two distinct
memory rows — deliberate, defined behavior (test plan "Interaction edges: double-submit same
meal"). Idempotency is a property of `reprocess_note` (keyed on a specific note), **not** of
`ingest_text`. A user correcting a mistaken double-log is a future product feature, not a
write-path concern.

## 11. Connection & transaction mechanics

- Writes use **explicit transactions** (`engine/db.py::transaction()`, `autocommit` off,
  `dict_row`), not the `autocommit=True` mode the canaries use for DDL.
- Typed events are inserted **row-at-a-time within the one transaction** — not for atomicity
  (the transaction gives that) but because C-SPANN vector-index inserts degrade in large
  batches (same footgun the replay CLI guards, T8; see the comment at
  `engine/tests/test_vector_canary.py`).
- Schema DDL (`setup_schema`) is a separate concern: idempotent `CREATE … IF NOT EXISTS`, run
  at app startup and via `cli/migrate.py` (D4). It is never part of a write transaction.

## 12. Phase 6 forward-compatibility

When T7 lands, the `turns` row and its `evidence_traces` row join the **same** write
transaction (D)/(D') as the memories (ADR-13.14: "written in one transaction after a turn
completes"). This document's boundary does not move — the trace/turn writes are additional
statements inside the *existing* single transaction, preserving rule 2 (atomic turn) and the
turn-commit-failure guarantee. Phase 2 creates those tables but does not write to them.

## Maintenance notes

- Revisit if ADR-13.5 changes, if per-event partial acceptance is adopted (§5), or when T7
  adds turn/trace writes to the transaction (§12) — update §4 and §12 together.
- Do **not** move model calls inside the transaction to "save a round-trip" (rule 1) — it
  reintroduces long transactions and network-dependent lock hold times.
- The all-or-nothing validation rule (§5) is load-bearing for the binary receipt model; changing
  it is a product decision, not a refactor.

## Related files

| File | Relationship |
|---|---|
| `engine/ingestion.py` | Implements this specification |
| `engine/repository.py` | The parameterized, user-scoped write queries invoked inside (D)/(D') |
| `engine/db.py` | `transaction()` context manager + `setup_schema` |
| `engine/types.py` | Stage (B) validation (T3, ADR-13.6) |
| `engine/model.py` | `ModelProvider` contract; `ExtractionError` / `EmbeddingError` |
| `engine/tests/test_ingestion.py` | Asserts every row of the §9 matrix |
| [../office-hours/09-decisions.md](../office-hours/09-decisions.md) | ADR-13.5 (failure policy), 13.6 (registry), 13.14 (one-transaction turn) |
