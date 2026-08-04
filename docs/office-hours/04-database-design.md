# 04 — Database Design (CockroachDB)

> Part of the [office-hours canonical docs](README.md). Related: [03-memory-engine.md](03-memory-engine.md), [06-retrieval-strategy.md](06-retrieval-strategy.md).

## Design rule

**No rigid nutrition-specific columns.** Memories are typed events with flexible JSONB
payloads; new attributes (a new nutrient, a new metric) must never require a migration.
Structure lives in per-type payload conventions, not columns.

## Core table (design-level sketch)

> **The DDL now lives in [`engine/schema.sql`](../../engine/schema.sql)** (applied
> idempotently by `engine/db.py::setup_schema` at app startup and via `python -m cli.migrate`,
> landed with Phase 2). The sketch below is the design intent; the schema file is what runs.
> They currently agree — if they ever diverge, the schema file wins and this section is the
> bug.

```sql
CREATE TABLE memories (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL,
  event_time    TIMESTAMPTZ NOT NULL,   -- when it happened (may be estimated)
  tz            TEXT NOT NULL,          -- user's timezone at event time
  type          TEXT NOT NULL,          -- 'meal' | 'workout' | 'sleep' | 'body_scan' | 'weight'
                                        -- | 'blood_report' | 'supplement' | 'note' | 'insight' | ...
  source        TEXT NOT NULL,          -- 'chat' | 'photo_upload' | 'file_upload' | 'replay'
                                        -- | 'consolidation' (derived rows) | ...
  provenance    TEXT NOT NULL,          -- 'live' | 'reconstructed'
  confidence    FLOAT NOT NULL,         -- 0..1; 1.0 for directly observed live data
  status        TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'retracted' | 'superseded'
  superseded_by UUID,                   -- chain to the replacing memory (insights)
  summary       TEXT,                   -- short natural-language rendering (embedded)
  payload       JSONB NOT NULL,         -- typed, per-type conventions below
  embedding     VECTOR(512),            -- nullable; Titan V2 normalized (L2 ≡ cosine) — ADR-13.2
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()  -- when we learned it (bi-temporal)
);
-- Distributed vector index on embedding  (hackathon tool evidence #1 — verify day one)
-- Inverted index on payload              (JSONB filtering/aggregation)
-- Secondary index on (user_id, type, event_time) for timeline + aggregation scans
```

Adjacent tables ([ADR-13.14](09-decisions.md#adr-13)): `users` + `sessions` (email+password
auth with opaque session tokens — simple by design, ADR-13.15), `turns` (per-turn UI-rendering record, referenced
memory IDs) + `evidence_traces` (the **persisted `EvidenceTrace`** per answer/ingestion turn,
JSONB referencing memory IDs, never copying payloads — written in the same transaction as the
turn; see [03-memory-engine.md](03-memory-engine.md#6-evidence-trace-builder-adr-12)),
`user_profile` (goals, allergies, injuries, preferences). LangGraph's PostgresSaver
checkpoint tables also live here but hold **graph execution state only** — the UI never
renders from them.

## Bi-temporality

`event_time` = when it happened (estimated for reconstructed memories, flagged by
`confidence` < 1 and `provenance='reconstructed'`). `created_at` = when the system learned
it. Both matter: reconstructed history is learned *now* about *then*. (Concept borrowed from
Graphiti's temporal model — see [09-decisions.md → ADR-5](09-decisions.md#adr-5).)

## Payload conventions (examples, not exhaustive)

```jsonc
// type='meal'
{ "meal_type": "lunch", "items": [ {"name": "curd", "qty_g": 250}, {"name": "eggs", "qty": 3} ],
  "nutrition": { "protein_g": 46, "kcal": 610, "estimated": true }, "photo_s3_key": "..." }

// type='body_scan'
{ "body_fat_pct": 21.4, "weight_kg": 71.2, "method": "scale_scan" }

// type='insight'  (derived — tier 2).  As implemented in Phase 5; source='consolidation'.
// event_time = window_end (what the claim is about); created_at stays truthful (ADR-13.10).
{ "kind": "level_shift",               // closed vocabulary (I-1): level_shift | intervention_outcome
  "hypothesis": "protein rose from ~45 to ~83 g/day starting 2026-06-23",
  "series_metric": "protein_g", "series_kind": "behavioural",
  "window_start": "2026-06-15T00:00:00+05:30",   // extent of the evidence, NOT the identity
  "window_end":   "2026-06-30T00:00:00+05:30",
  "pre_value": 45.0, "post_value": 83.0,          // the claim's values; post_value is the
                                                  // reference a direction-only retraction uses
  "evidence_ids": ["…"],               // boundary-anchored, capped at 24 (I-5)
  "evidence_count": 16,                // the TRUE total — what keeps the cap honest
  "effect": 0.844, "coverage": 1.0, "specificity": 1.0,
  "pattern_strength": 0.844,           // = effect × coverage × specificity, enforced (I-19);
                                       // a documented heuristic (ADR-13.12), never a probability
  "fingerprint": "…",                  // identity of the CLAIM (kind, series, claim dates,
                                       // values, interventions) — not of its evidence window
  "retraction_condition": {            // typed object (ADR-13.11), evaluated deterministically
    "metric": "protein_g", "direction": "falling", "threshold": 45.0,
    "window_days": 30, "min_count": 3 } }
```

New nutrient tomorrow? Add a key inside `payload.nutrition`. No migration.

## Retraction / supersession model

- Retraction **never deletes**: `status='retracted'`. The engine's history of being wrong is
  itself memory (and demo material).
- Supersession chains via `superseded_by` (an improved insight replaces an older one).
- Mechanics (who evaluates retraction conditions, on-ingest vs. on-read) were settled by
  [ADR-13.11](09-decisions.md#adr-13) (typed conditions, deterministic evaluator) and
  [ADR-13.1](09-decisions.md#adr-13) (evaluated in the sync consolidation pass that rides
  ingestion); [OQ7](10-open-questions.md) is closed. Implemented in Phase 5 (T5/T6).
- **Retraction and supersession are different writes, and stay distinguishable in the data.**
  Retraction flips `status='retracted'` and leaves `superseded_by` NULL — nothing replaced the
  claim, the evidence was simply against it. Supersession sets both, because a replacement exists.
  Neither ever rewrites a payload, so what the engine claimed and the rule it agreed to be judged
  by both survive (Phase 5 invariants I-13/I-20).
- Default reads filter `status='active'`; the glass-box UI can surface retracted insights
  deliberately.

## Why CockroachDB is load-bearing (not a checkbox)

- **One consistent store** for typed-event SQL aggregation *and* vector search — no separate
  vector DB, no sync gap between "what the agent computes" and "what it semantically recalls."
- Distributed **vector indexing** keeps semantic recall fast as memory grows lifelong.
- **Inverted JSONB indexes** make the migration-free payload model queryable at speed.
- Postgres wire compatibility: standard drivers/tooling just work.
- The sponsor pitch ("memory that never goes down") is the same property that makes lifelong
  health memory trustworthy.

## Security notes (judges will poke)

- All engine queries are parameterized; the agent has no raw SQL path
  ([03-memory-engine.md](03-memory-engine.md#engine-exposed-tools-the-agents-only-db-access)).
- **Per-user row scoping is a security boundary** and is tested as one (user A can never read
  user B's memories or traces) — the standard multi-user model, no sandbox machinery
  ([ADR-13.4](09-decisions.md#adr-13)).
- The hosted production DB holds real user accounts behind auth; the sanitized-derivative
  rule ([ADR-7](09-decisions.md#adr-7), as narrowed by ADR-13.4) applies to the repo-shipped
  replay dataset and video review.
- Production abuse/spend controls are explicitly out of scope this iteration
  ([ADR-13.15](09-decisions.md#adr-13)).
