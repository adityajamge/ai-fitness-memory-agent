-- Phase 2 schema — the full memory store + auth + UI-truth tables.
-- Applied idempotently by engine/db.py::setup_schema (app startup + cli/migrate.py).
-- Design source of truth: docs/office-hours/04-database-design.md.
--
-- All statements are idempotent: CREATE TABLE IF NOT EXISTS carries its inline indexes
-- on first creation; anything that can't be inline is a separate CREATE INDEX IF NOT
-- EXISTS. The VECTOR-index feature flag is set best-effort by setup_schema before this
-- file runs (some CockroachDB versions have vector indexes on by default).

-- ── memories: typed episodic events + derived insights (two-tier, one table) ──────────
-- No rigid nutrition columns (04 design rule): structure lives in the JSONB payload,
-- validated at ingestion by engine/types.py. Bi-temporal: event_time (when it happened,
-- possibly estimated) vs created_at (when we learned it).
CREATE TABLE IF NOT EXISTS memories (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL,
    event_time    TIMESTAMPTZ NOT NULL,
    tz            TEXT NOT NULL,
    type          TEXT NOT NULL,
    source        TEXT NOT NULL,
    provenance    TEXT NOT NULL,
    confidence    FLOAT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    superseded_by UUID,
    summary       TEXT,
    payload       JSONB NOT NULL,
    embedding     VECTOR(512),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Distributed vector index (hackathon tool evidence #1) — semantic recall path.
    VECTOR INDEX memories_embedding_idx (embedding),
    -- Inverted index for JSONB payload filtering/containment (migration-free queries).
    INVERTED INDEX memories_payload_idx (payload),
    -- Timeline + aggregation scans (06 retrieval strategy).
    INDEX memories_user_type_time_idx (user_id, type, event_time)
);

-- Cheap scan for the embedding-backfill worker (T15); rows are transient here.
CREATE INDEX IF NOT EXISTS memories_unembedded_idx ON memories (user_id)
    WHERE embedding IS NULL;

-- ── users + sessions: simple email+password auth (ADR-13.15) ──────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL UNIQUE,
    password_hash BYTES NOT NULL,   -- hashlib.scrypt output (D2)
    salt          BYTES NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,           -- opaque random token (HttpOnly cookie)
    user_id    UUID NOT NULL REFERENCES users (id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id);

-- ── user_profile: goals, allergies, injuries, preferences ─────────────────────────────
CREATE TABLE IF NOT EXISTS user_profile (
    user_id     UUID PRIMARY KEY REFERENCES users (id),
    goals       JSONB,
    allergies   JSONB,
    injuries    JSONB,
    preferences JSONB,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── turns + evidence_traces: source of truth for UI rendering (ADR-13.14) ──────────────
-- Created now so the schema is complete, but NOT written by Phase 2. T7 (Phase 6) writes
-- both in the SAME transaction as the turn's memories — see
-- docs/engineering/ingestion-transaction-boundaries.md §12.
CREATE TABLE IF NOT EXISTS turns (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL,
    thread_id  TEXT,
    role       TEXT,           -- 'user' | 'assistant'
    content    TEXT,
    memory_ids UUID[],         -- memories created/referenced by this turn
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS evidence_traces (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    turn_id    UUID,
    user_id    UUID NOT NULL,
    trace      JSONB NOT NULL, -- persisted EvidenceTrace; references memory IDs, never copies payloads
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS evidence_traces_turn_idx ON evidence_traces (turn_id);
