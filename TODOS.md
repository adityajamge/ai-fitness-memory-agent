# TODOS

## Production abuse & spend controls (deferred 2026-07-12, /plan-eng-review D14)

- **What:** Layered abuse/spend protection for the public app: per-account daily model-call
  budget, global daily spend cap that flips the app to read-only with an honest banner, per-IP
  signup throttle, optional email verification.
- **Why:** Open signup + per-message Bedrock cost = unbounded spend under abuse. Deferred
  deliberately (builder decision): the current focus is the Memory Engine, the hackathon, and
  the portfolio; simple email+password auth only. See ADR-13.15 in
  `docs/office-hours/09-decisions.md`.
- **Pros:** Budget becomes mathematically bounded; read-only degradation is itself a
  production-readiness story.
- **Cons:** ~half a day of work (CC: ~4-5h) touching auth middleware, a usage-counter table,
  and config; adds operational knobs to maintain.
- **Context:** The reviewed design (2026-07-12) was: usage counters keyed by user_id/day in
  CockroachDB, checked in the model-interface wrapper (single choke point — all Bedrock calls
  already flow through it); global cap read from config; IP throttle at the signup route.
  Stopgap in the meantime: AWS billing alerts.
- **Depends on / blocked by:** Simple auth (Milestone 1) must exist first. Do before any
  post-hackathon public promotion of the URL.

## Note-fallback confidence during replay (`_NOTE_CONFIDENCE = 1.0`)

- **What:** `engine/ingestion.py` writes every note-fallback memory with `confidence = 1.0`,
  justified by the comment *"we're certain the user said it; only the parse is incomplete."*
  That reasoning holds for a live chat turn and **not** for a reconstructed one: when the
  replay CLI (T8) pushes old records through `ingest_text` and extraction fails, we persist a
  note asserting full confidence in text that was itself LLM-reconstructed from memory,
  chat logs, and gym sheets.
- **Why:** confidence is a judged, user-visible honesty signal (04-database-design.md:
  "1.0 for directly observed live data"; reconstructed memories are supposed to be flagged by
  `confidence < 1` **and** `provenance='reconstructed'`). Today the provenance half is correct
  (fixed 2026-07-21, D3) while the confidence half over-claims.
- **Pros:** removes the last place a reconstructed row can look as certain as an observed one;
  makes the glass-box UI's confidence column trustworthy across both provenances.
- **Cons:** ~15 minutes. Needs one product decision — a flat reconstructed-note confidence
  (e.g. 0.6) vs. inheriting the confidence the replay caller already assigned to the batch.
  The latter is better but means threading a value that only T8 will have.
- **Context:** surfaced by the 2026-07-21 Phase-2 audit while fixing D3; deliberately left out
  of that change to keep it scoped to provenance. Not user-visible today because `chat` is the
  only ingestion source in production.
- **Depends on / blocked by:** decide **with** T8 (replay CLI, Phase 4) — that is the first
  code that can produce a reconstructed note, and the first place a sensible confidence value
  is actually known.

## Drop embedding normalization when CockroachDB ships cosine distance

- **What:** When CockroachDB vector indexes support cosine (or inner-product) distance,
  evaluate removing the unit-normalization requirement on embeddings (ADR-13.2) and update
  the vector canary test accordingly.
- **Why:** Normalization exists solely because C-SPANN is Euclidean-only today (verified
  2026-07-12, https://www.cockroachlabs.com/docs/stable/vector); unit vectors make L2 ≡
  cosine. When cosine ships natively, the workaround is dead weight.
- **Pros:** Removes a non-obvious invariant future contributors could silently break.
- **Cons:** Trivial; re-verifying ranking equivalence takes an hour.
- **Context:** Titan V2 embeddings are normalized at the source (`normalize=true`), so today
  the requirement costs nothing — this TODO is the breadcrumb explaining why it exists and
  when it can die. The canary test asserts K-NN ordering on normalized vectors.
- **Depends on / blocked by:** CockroachDB vector index cosine support reaching the tier we
  run on (roadmap item as of v25.x).

## Write-side entity canonicalization (accepted 2026-07-23 — before Phase 4 replay)

- **Status:** ACCEPTED architectural decision (not an M3 task). Extends the extraction
  contract; do **not** implement query-time synonym/variant expansion — that was evaluated
  and rejected (see below).
- **What:** During ingestion, the extractor emits a **canonical entity** alongside the
  original logged value on typed items, whenever a canonical form applies. Shape (payload
  hot fields, `extra="allow"` — no migration):
  - Food item: `canonical="chicken"`, `logged="Grilled Chicken"`, `preparation="grilled"`
  - Exercise: `canonical="bench_press"`, `logged="Flat Bench Press"`
  - Supplement/medication: `canonical="vitamin_d"`, `logged="Vitamin D3 60000 IU"`
- **Why:** `lookup_events` uses exact JSONB containment (`@>`) over extracted items. Without
  a canonical name, "when did I last eat chicken?" exact-matches `"Chicken"` but silently
  misses `"Grilled Chicken"` — a *confident* wrong answer, the worst failure class for a
  glass box. Canonical names make the structured path correct by construction; semantic
  recall stays as the fuzzy fallback for whatever canonicalization can't anticipate.
- **Why write-side, not read-side:** normalization runs **once per memory at ingestion**
  instead of on every query forever. It keeps the deterministic engine boundary intact —
  the engine never interprets language (06); canonicalization is the extractor's job (the
  one NL layer already sanctioned on the write path). Query-time variant expansion was
  rejected: a static synonym table rots and can't cover an open, multilingual, personal
  food vocabulary; LLM variant generation is just worse-coverage semantic search with a new
  hallucination surface and run-to-run nondeterminism — and putting either below the
  tool-call boundary would break the "engine never interprets language" invariant.
- **Timing (the reason this is logged now):** the account has no history until Phase 4
  replay (T8). Adopting canonicalization **before** replay canonicalizes all 6–12 months of
  reconstructed history on first ingestion, free. Adopting it after means re-extracting
  history (the T8 extraction cache softens but doesn't eliminate the re-run). This is the
  cheapest window.
- **Scope of change:** extraction prompt + tool schema (`agent/providers/bedrock.py`), one
  or two hot fields per relevant payload type (`engine/types.py`), and `lookup_events` gains
  an optional match on `canonical` (still exact containment, still deterministic). Retrieval
  architecture is otherwise unchanged — this is a data-quality upgrade, not a new path.
- **Independent of this:** add case-insensitive item matching to `lookup_events` regardless
  (mechanical string hygiene, engine-legal, ~zero cost) — a cheap partial mitigation until
  canonicalization lands.
- **Depends on / blocked by:** decide and land **with or just before T8** (Phase 4 replay),
  the first pipeline that produces reconstructed memories at scale.

---

# Phase 3 assembly — architectural decisions recorded while docs are frozen

> Surfaced by the M3 (context assembly) audit, 2026-07-24. These are decisions the code
> **already implements**; they are logged here because the office-hours docs are frozen.
> **When docs unfreeze, migrate each into the cited ADR/design doc and delete it here** —
> TODOS.md is the holding pen, not their permanent home.

## A3 — Citation-validation surface is `citable_ids`, not `trace.evidence` (blocks T7)

- **Status:** ACCEPTED decision + **open contract item that T7 (Phase 6) must honor**. This
  is the highest-priority note here: left unresolved it becomes a correctness bug in
  citation validation.
- **The issue:** ADR-12 / ADR-13.13 say the narrator may cite only IDs "present in the turn's
  EvidenceTrace," and "the UI reads the trace." But `assemble()` is a pure function (see D2):
  an aggregate's contributing memory IDs live in `ContextBlock.aggregates[].buckets[]
  .evidence_ids` (surfaced by `ContextBlock.citable_ids()`), and are **not** in
  `trace.evidence` — nor anywhere else in the trace (an aggregate's `RetrievalStep` records
  SQL + params + row_count, not result IDs). So a *valid* citation of an aggregated meal
  would be flagged **invalid** if T7 validates strictly against the persisted trace.
- **What T7 must do (decide one, before building citation validation):**
  1. Validate the answer's citations against the turn's full citable set — `trace.evidence`
     IDs ∪ aggregate/count contributing IDs — not `trace.evidence` alone; **and/or**
  2. Extend the persisted trace so aggregate/count contributing IDs are reachable *from the
     trace itself* (e.g. carry them on the aggregate `RetrievalStep`, or hydrate contributing
     rows into `trace.evidence` via the T16 batch-fetch). Option 2 keeps ADR-12's "the UI
     reads the trace" literally true and is the cleaner long-term shape.
- **Recommendation:** option 2 (put the IDs in the trace) so the persisted trace stays the
  single source of truth for the glass box; `citable_ids()` then becomes a trace-derived
  helper rather than a context-only one.
- **Doc home when unfrozen:** ADR-12 (evidence traces) + ADR-13.13 (honest citation scope);
  note the refinement in 03-memory-engine.md §6 (citation validation) and the T7 task spec
  in 11-implementation-tasks.md.
- **Depends on / blocked by:** must be settled **at the start of T7** (trace persistence +
  citation validation, Phase 6, Lane E), before the UI components consume the contract.

## A1 — Two-view evidence split: `trace.evidence` ⊇ `context.memories`

- **Status:** ACCEPTED decision, implemented in `engine/assembly.py`.
- **What:** `EvidenceTrace.evidence` carries **everything retrieved** (deduped across tools,
  all candidates, ordered by score); `ContextBlock.memories` carries only the
  diversity-capped, budget-limited subset handed to the narrator. The glass box therefore
  shows more than the model saw, and `EvidenceTrace.ranking` explains what was cut and why.
- **Why:** the docs specify a budgeted context block (06) and a trace of "memory IDs used"
  (03) but never say whether budget truncation applies to both. Keeping the trace complete
  makes "why the engine picked these and dropped those" fully inspectable — a transparency
  win, and it keeps the budget a narration concern rather than an evidence-hiding one.
- **Doc home when unfrozen:** 06-retrieval-strategy.md (ranking & assembly) + 03-memory-engine.md §5.

## A2 — `count_events`: a builder family beyond 06's enumerated closed set

- **Status:** ACCEPTED addition, implemented + tested (M2).
- **What:** a type-level event-count family ("how many workouts in June?") — counts rows of
  a type in a range, with contributing IDs. Distinct from the aggregation family's `count`
  agg, which counts rows where a *specific metric* is present. The distinction exists
  because the aggregate `count` was scoped to metric-present rows.
- **Why:** "how many X" is a natural, common question the enumerated families didn't cover
  cleanly; folding it into aggregation would have overloaded the `count` semantics.
- **Doc home when unfrozen:** 06-retrieval-strategy.md (query-construction: closed builder
  families) — add it to the enumerated set; note it in the engine-tools table in
  03-memory-engine.md if it becomes a distinct planner tool in M5.

## D1 — Recency ranks relative to the retrieved set, not "the question's window"

- **Status:** ACCEPTED **deviation** from the written design, implemented in ranking.
- **What:** 06 says "Recency / temporal proximity **to the question's window**." Assembly
  instead normalizes recency *within the candidate set* (newest retrieved → 1.0, oldest →
  0.0).
- **Why justified:** using the question's window would require assembly to know that window —
  i.e. parse the question (violates the no-language determinism boundary) or reconcile each
  tool's `date_range` into one reference window (undefined when tools disagree, absent for
  recall). Within-set normalization is deterministic, language-free, and sufficient for
  ordering *within* an answer, which is all ranking needs. Revisit only if a concrete
  ranking failure motivates a window-aware signal.
- **Doc home when unfrozen:** 06-retrieval-strategy.md (ranking axis #3) — amend the wording
  to "temporal proximity within the retrieved candidate set."

## D2 — `assemble()` is a pure function: aggregate rows are not hydrated into the trace

- **Status:** ACCEPTED **deviation** (strict reading of 03) + scoping boundary.
- **What:** 03's trace contract implies `evidence` = used memory IDs *with snapshot metadata*.
  For an aggregate's contributing IDs, assembly has the IDs but not the metadata and does
  **not** fetch it — `assemble()` touches no database.
- **Why justified:** the engineering plan scoped M3 assembly as a pure, fixture-testable
  function; hydrating contributing rows into full snapshots is exactly Phase 6's batch-fetch
  (T16). This is the mechanism behind A3 and should be documented alongside it.
- **Doc home when unfrozen:** 03-memory-engine.md §5–§6 (assembly/trace) + the T16 task spec;
  cross-reference A3.
