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
- **Design review:** full alternatives/trade-offs in
  [docs/engineering/replay-architecture.md §4.6](engineering/replay-architecture.md#46-confidence-note-fallback-during-replay--needs-closing-tracked-in-todosmd)
  — recommends threading a per-record confidence hint through `ingest_text` rather than a flat
  reconstructed-note value.

## Planner tool pairing for ambiguous item follow-ups (surfaced 2026-07-29 manual validation)

- **What:** Improve planner tool-selection guidance so an ambiguously-worded item
  follow-up (e.g. "how many eggs did I have?" after logging "2 boiled eggs") pairs
  `lookup_events` with `recall_memories`, rather than selecting `recall_memories` alone.
- **Why:** `lookup_events`'s own description already recommends issuing both together when
  wording could differ ("grilled chicken" vs. "chicken"), but the planner didn't follow
  that for this phrasing. `lookup_events` needs no query embedding; `recall_memories` does
  — so on a provider that can't embed (the Claude API dev adapter), calling only the latter
  means an answerable question degrades to "nothing logged" when the former would have
  succeeded outright.
- **Pros:** Reduces unnecessary dependence on embeddings for questions the exact-match path
  could already answer; likely a small, scoped prompt-guidance change (`agent/tools.py`
  tool descriptions and/or `PLAN_SYSTEM` in `agent/providers/_prompts.py`).
- **Cons:** Needs live-model validation to confirm the tightened guidance actually changes
  tool selection rather than just reading better; risk of over-pairing (issuing both tools
  on every item question, adding latency/cost) if done too bluntly.
- **Context:** Found during the 2026-07-29 manual validation
  ([12-test-plan.md](docs/office-hours/12-test-plan.md#manual-end-to-end-validation-record--2026-07-29)),
  logging a meal with "2 boiled eggs" and asking "how many eggs did I have for breakfast?"
  immediately after. Not a bug — the degradation path it hit instead behaved correctly
  (honest 200, reported error, no hallucination).
- **Depends on / blocked by:** none; can be picked up any time.

## Clean up the shared CockroachDB Cloud dev cluster (surfaced 2026-07-29 manual validation)

- **What:** Remove accumulated historical test users, threads, and checkpoint rows
  (`users`, `sessions`, `memories`, `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`)
  from the shared CockroachDB Cloud development cluster that don't belong to current,
  active validation/demo data.
- **Why:** The cluster has accumulated ~300+ leftover threads/users across the project's
  history (test runs, manual validation sessions). This caused one flaky, non-reproducing
  failure in a full-suite run — `cli/tests/test_backfill.py::test_main_all_sweeps_users_with_gaps`
  sweeps *every* user in the cluster with a NULL-embedding gap, which took over 2 minutes
  and very likely hit a transient connection hiccup given the volume, rather than a code
  defect (it passed cleanly on an isolated rerun).
- **Pros:** Faster, more stable test runs against the real cluster; removes a source of
  noise when interpreting ad-hoc diagnostic queries during future manual validation (stray
  rows from unrelated historical sessions can otherwise look like current-code anomalies).
- **Cons:** Needs care not to delete anything still wanted for demo/portfolio purposes;
  a one-time manual cleanup, not automatable without deciding a retention policy first.
- **Context:** Found during the 2026-07-29 manual validation
  ([12-test-plan.md](docs/office-hours/12-test-plan.md#manual-end-to-end-validation-record--2026-07-29))
  while investigating a `checkpoint_blobs` channel scan that initially looked like it might
  indicate an M5-1 durability guard violation, before being traced to old data.
- **Depends on / blocked by:** none; can be picked up any time.

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

---

# Temporary Architecture Decision Log (post-documentation-freeze)

> **Read this section before starting any milestone.** It is the holding pen for architecture
> decisions **accepted after the office-hours documentation freeze** that are **not yet
> implemented** — decisions that gate upcoming work but have no code to describe yet. Each
> entry carries a **"doc home when implemented"** pointer; once the work lands, migrate the
> entry into its cited ADR/design doc and delete it here.
>
> **Migrated 2026-07-24 (Phase 3 documentation audit):** every entry describing *implemented*
> Phase 3 architecture now lives in [ADR-14](docs/office-hours/09-decisions.md#adr-14) —
> A1/A2/D1/D2 (assembly, builder families, ranking), M4-1/M4-2 (routing as tool selection, the
> empty-plan contract), and M5-1 (the graph-state durability boundary, whose investigation is
> written up in
> [docs/engineering/graph-state-durability.md](docs/engineering/graph-state-durability.md)).
> A3 (the citable-surface contract) is documented as ADR-14.8 and now tracked as a blocking
> decision on **T7** in
> [11-implementation-tasks.md](docs/office-hours/11-implementation-tasks.md).

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
- **Doc home when implemented:** a new ADR in
  [09-decisions.md](docs/office-hours/09-decisions.md) (it changes the extraction contract),
  plus the item-filter paths in [06-retrieval-strategy.md](docs/office-hours/06-retrieval-strategy.md).
- **Depends on / blocked by:** decide and land **with or just before T8** (Phase 4 replay),
  the first pipeline that produces reconstructed memories at scale.
- **Design review:** flagged as a scope gap against the roadmap/backlog (this decision is
  absent from Phase 4's deliverables and T8's estimate) in
  [docs/engineering/replay-architecture.md §5](engineering/replay-architecture.md#5-risk-analysis)
  and tracked as open question 3 in
  [§8](engineering/replay-architecture.md#8-open-questions) — needs an explicit
  in-scope-for-Phase-4 vs. deliberately-deferred call before T8 implementation starts.
