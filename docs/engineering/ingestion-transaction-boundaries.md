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
7. **Consolidation never affects the turn that triggered it.** Same posture, same reason (§7.1,
   Phase 5 stage (F₀)).

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
├─ (F₀) CONSOLIDATION  (Phase 5 — post-commit, own txns, budgeted, best-effort — §7.1)
│        derives insights from the series this turn touched; appends them to the receipt
│        ▼
└─ (F₁) opportunistic backfill  (post-commit, separate txn, bounded, best-effort — §7)

NOTE PATH:
   (D') ══ SINGLE WRITE TRANSACTION ══  INSERT one `note` memory (raw text in payload)  COMMIT
   (E') build receipt  (parse_status="incomplete", message "saved — parsing incomplete")
```

The transaction boundary is at **(D)/(D')** only. Everything above it is retryable in-memory
work with no durable side effects; everything at it is atomic; everything below it
(receipt, backfill) observes already-committed state.

## 5. Partial extraction and validation failure — the all-or-nothing rule

A turn may extract to several events (though most single turns produce one). Two sub-cases:

- **Extraction returns zero events:** the model interface signals intent by raising
  `ExtractionError` when the input clearly contained loggable content (→ **note path**) and
  returning `[]` only for a genuinely contentless turn — e.g. "thanks!" — which is a
  legitimate no-op (`parse_status="ok"`, message `"nothing to log"`), not a note.

  **The provider is what makes this real** (D1, fixed 2026-07-21). The engine cannot tell the
  two apart — it sees an empty list either way — so the decision is pushed to the only
  component that can make it. `BedrockProvider`'s forced tool carries a **required
  `no_loggable_content` boolean**: an empty `events` list is accepted as a no-op only when the
  model affirms it with `true`; a `false`, missing, or non-boolean flag raises
  `ExtractionError` and routes the turn to the note path. A model that answers in prose
  instead of calling the tool already raised. The contract is written out in
  `engine/model.py::extract_events` and any future provider must honor it.
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

## 7.1 Stage (F₀) — consolidation (Phase 5)

Consolidation rides this tail between the receipt and the backfill. Canonical reference:
[consolidation-architecture.md §4.8](consolidation-architecture.md).

- **Post-commit, always.** Running it before (D) would let a *derived*-data failure roll back a
  fact the user reported — inverting never-lose-input for the sake of a hypothesis.
- **Outside the write transaction** (invariant I-14), which is what keeps **rule 1** true: it
  performs several round trips, and none of them happen while a transaction is open.
- **Never fails a turn** (I-15). A raising consolidator is caught and logged exactly as backfill
  is; the memories stay committed and the receipt stays honest. An insight lost to an error costs
  one re-derivation, because the next ingest touching the series recomputes it and the identity
  rule makes that idempotent.
- **No model call.** Insights are written with `embedding = NULL` and picked up by (F₁) (I-16),
  so the stage adds no Bedrock round trip to the ingest path.
- **Scoped to touched series.** A meal payload carries four metrics but only `protein_g` is
  consolidatable, so lunch costs one series scan; a turn touching nothing consolidatable opens no
  connection at all.
- **Budgeted, and a deferral is a result, not an error.** Overflow leaves the remainder for the
  on-demand path and the turn is undisturbed.

The receipt gains an `insights` list, kept separate from `created`: the user reported what is in
`created`, while an insight is a claim the engine made *about* it, and one list would let a receipt
imply the user logged something they never said.

## 8. `reprocess_note` — the supersession transaction

`reprocess_note(user_id, note_id)` is the recovery path that upgrades a note into typed
events; it is what the test-plan path "retry succeeds → typed events supersede note"
exercises. Its product surface is **`POST /api/memories/{id}/reprocess`**
(`api/routers/ingest.py`, D2 — added 2026-07-22): authenticated, user-scoped, returning the
same receipt shape as `/api/ingest`; every ineligible target (nonexistent, another user's,
not a note, already superseded) is a uniform 404. An *opportunistic* trigger (retrying notes
without being asked) was deliberately not added — it spends Bedrock calls on turns the user
didn't initiate, and belongs with Phase 3's agent turn model if it belongs anywhere.

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
| Extraction returns empty, model affirms contentless | no | nothing | `ok` | "nothing to log" | n/a (no-op by design) |
| Extraction returns empty on real input | no | 1 note (raw text) | `incomplete` | "saved — parsing incomplete" | `reprocess_note` |
| One+ events fail validation | no | 1 note (raw text) | `incomplete` | "saved — parsing incomplete" | `reprocess_note` |
| Embedding fails | no (pre-txn) | typed events, `embedding=NULL` | `ok` | "…(embedding pending)" | backfill / CLI |
| DB write fails (D)/(D') | yes | nothing (rollback) | — (turn errors) | retriable error | resubmit turn |
| Backfill fails (F) | own txn | nothing extra | `ok` (unchanged) | nothing | next backfill pass |
| `reprocess_note` extraction fails | no | nothing | — | note unchanged | reprocess later |
| `reprocess_note` DB write fails | yes | nothing (rollback) | — | note unchanged | reprocess later |

**Silent-loss cells: zero** (restored 2026-07-21 when D1 was fixed —
[§13](#13-known-deviations-audited-2026-07-21)). Every non-success outcome is either an honest
receipt, an honest retriable error, or a no-op that leaves recoverable state. The "empty on
real input" row depends on providers honoring the empty-result contract; `BedrockProvider`
enforces it, and any new provider must too.

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

**Stage (F₀) sits outside that transaction and must stay there** (§7.1, I-14). An insight is
derived data: losing it costs one re-derivation, whereas widening the atomic turn to cover it
would put several round trips — and a failure mode that has nothing to do with the user's input —
inside the transaction that guarantees never-lose-input.

## 13. Known deviations (audited 2026-07-21)

Three places where the code and this specification disagreed, found by a full repo-vs-docs
audit at the end of Phase 2 — documented here rather than silently rewritten into the spec,
because each is a behavior decision, not a typo. **All three are now fixed** (D3 + D1 on
2026-07-21, D2 on 2026-07-22); the table is kept as the record of what drifted and how it
was closed.

| # | Deviation | Spec says | Code does | Impact |
|---|---|---|---|---|
| ~~**D1**~~ | Empty extraction on contentful input | note path, `incomplete` (§5, §9) | ~~no-op receipt, `ok`, "nothing to log"~~ | **FIXED 2026-07-21** — resolved in the *provider*, not the engine: required `no_loggable_content` flag on the forced tool, unaffirmed empties raise `ExtractionError`. `IngestionService` unchanged. 9 offline tests in `agent/tests/test_bedrock_provider.py` |
| ~~**D2**~~ | `reprocess_note` reachability | "invoked opportunistically or from CLI" (§8) | ~~no caller outside tests~~ | **FIXED 2026-07-22** — `POST /api/memories/{id}/reprocess` (authenticated, scoped, uniform 404 for ineligible targets); 7 tests in `api/tests/test_reprocess.py`. Opportunistic trigger deliberately not added (§8) |
| ~~**D3**~~ | Note-fallback provenance | notes inherit the turn's `source`/`provenance` | ~~`_persist_note` hardcoded `provenance="live"`; `reprocess_note` hardcoded `source="chat"`/`provenance="live"`~~ | **FIXED 2026-07-21** — both now thread the caller's / the note's own origin; `get_note_text` became `get_note` so reprocessing can read it back. Guarded by 4 tests in `engine/tests/test_ingestion.py` |

D3 was the one with a deadline attached: had it survived into T8, reconstructed notes would
have entered the database mislabelled as live observations — exactly the honesty property
ADR-13.10 exists to protect. The replay CLI can now push `source="replay",
provenance="reconstructed"` through `ingest_text` and every outcome, including both note
fallbacks and any later reprocess, stays reconstructed.

## Maintenance notes

- Revisit if ADR-13.5 changes, if per-event partial acceptance is adopted (§5), or when T7
  adds turn/trace writes to the transaction (§12) — update §4 and §12 together.
- All §13 deviations are closed (last: D2, 2026-07-22); the struck-through rows stay as the
  record. If a new spec-vs-code drift is ever found, add it there the same way rather than
  silently rewriting the sections above.
- Do **not** move model calls inside the transaction to "save a round-trip" (rule 1) — it
  reintroduces long transactions and network-dependent lock hold times.
- The all-or-nothing validation rule (§5) is load-bearing for the binary receipt model; changing
  it is a product decision, not a refactor.

## Related files

| File | Relationship |
|---|---|
| `engine/ingestion.py` | Implements this specification |
| `engine/repository.py` | The parameterized, user-scoped write queries invoked inside (D)/(D'); `get_note` supplies the note's text + origin to `reprocess_note` |
| `engine/db.py` | `transaction()` context manager + `setup_schema` |
| `engine/types.py` | Stage (B) validation (T3, ADR-13.6) |
| `engine/model.py` | `ModelProvider` contract; `ExtractionError` / `EmbeddingError` |
| `engine/tests/test_ingestion.py` | Asserts every row of the §9 matrix |
| [../office-hours/09-decisions.md](../office-hours/09-decisions.md) | ADR-13.5 (failure policy), 13.6 (registry), 13.14 (one-transaction turn) |
