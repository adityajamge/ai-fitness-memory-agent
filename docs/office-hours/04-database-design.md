# 04 — Database Design (CockroachDB)

> Part of the [office-hours canonical docs](README.md). Related: [03-memory-engine.md](03-memory-engine.md), [06-retrieval-strategy.md](06-retrieval-strategy.md).

## Design rule

**No rigid nutrition-specific columns.** Memories are typed events with flexible JSONB
payloads; new attributes (a new nutrient, a new metric) must never require a migration.
Structure lives in per-type payload conventions, not columns.

## Core table (design-level sketch — exact DDL locked at /plan-eng-review)

```sql
CREATE TABLE memories (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL,
  event_time    TIMESTAMPTZ NOT NULL,   -- when it happened (may be estimated)
  tz            TEXT NOT NULL,          -- user's timezone at event time
  type          TEXT NOT NULL,          -- 'meal' | 'workout' | 'sleep' | 'body_scan' | 'weight'
                                        -- | 'blood_report' | 'supplement' | 'note' | 'insight' | ...
  source        TEXT NOT NULL,          -- 'chat' | 'photo_upload' | 'file_upload' | 'replay' | ...
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

Adjacent tables ([ADR-13.14](09-decisions.md#adr-13)): `users` (email+password auth,
sessions — simple by design, ADR-13.15), `turns` (per-turn UI-rendering record, referenced
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

// type='insight'  (derived — tier 2)
{ "hypothesis": "protein ↑ + sleep ≥7.5h preceded body-fat decline (lag ≈ 3 wk)",
  "evidence_ids": ["mem_4102", "mem_4788", "mem_8842", "..."],
  "trigger": "on_ingest:body_scan",
  "pattern_strength": 0.82,            // documented heuristic (ADR-13.12), not a probability
  "retraction_condition": {            // typed object (ADR-13.11), evaluated deterministically
    "metric": "body_fat_pct", "direction": "rising",
    "window_days": 30, "min_count": 3 } }
```

New nutrient tomorrow? Add a key inside `payload.nutrition`. No migration.

## Retraction / supersession model

- Retraction **never deletes**: `status='retracted'`. The engine's history of being wrong is
  itself memory (and demo material).
- Supersession chains via `superseded_by` (an improved insight replaces an older one).
- Open mechanics (who evaluates retraction conditions, on-ingest vs. on-read):
  [OQ7](10-open-questions.md).
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
