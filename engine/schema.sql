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

-- ── user_profile: current-state cache + goal/target history via profile_change memories ──
-- (ADR-17, docs/office-hours/09-decisions.md#adr-17). Reshaped 2026-08-11 from the original
-- {goals, allergies, injuries, preferences} stub, which shipped in Phase 2 with no reader or
-- writer. Current weight is deliberately NOT a column here — it stays a first-class `weight`
-- memory (ADR-17.2); this table never duplicates it.
CREATE TABLE IF NOT EXISTS user_profile (
    user_id              UUID PRIMARY KEY REFERENCES users (id),
    display_name         TEXT,
    date_of_birth        DATE,
    sex                  TEXT,                            -- 'male' | 'female' | NULL (skipped)
    height_cm            FLOAT,
    units                TEXT NOT NULL DEFAULT 'metric',   -- 'metric' | 'imperial' — display only
    primary_goal         TEXT,     -- 'lose_fat' | 'build_muscle' | 'recomp' | 'maintain' | 'general_health'
    activity_level       TEXT,     -- 'sedentary' | 'light' | 'moderate' | 'very_active' | 'athlete'
    target_weight_kg     FLOAT,
    dietary_preference   TEXT,
    allergies            JSONB,    -- list[str]
    injuries             TEXT,
    protein_target_g     FLOAT,
    calorie_target_kcal  FLOAT,
    targets_are_custom   BOOLEAN NOT NULL DEFAULT false,   -- true once the user overrides the computed suggestion
    onboarded_at         TIMESTAMPTZ,                      -- NULL until the intake screen is submitted or skipped
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Idempotent reshape for a cluster where the table was already created with the old columns
-- (`CREATE TABLE IF NOT EXISTS` above is a no-op there). Safe because the table has never been
-- written to (T4/audit finding, 2026-08-11): no data migration, only a shape migration.
ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS display_name TEXT;
ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS date_of_birth DATE;
ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS sex TEXT;
ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS height_cm FLOAT;
ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS units TEXT NOT NULL DEFAULT 'metric';
ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS primary_goal TEXT;
ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS activity_level TEXT;
ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS target_weight_kg FLOAT;
ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS dietary_preference TEXT;
ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS protein_target_g FLOAT;
ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS calorie_target_kcal FLOAT;
ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS targets_are_custom BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS onboarded_at TIMESTAMPTZ;
-- injuries changes shape (JSONB -> TEXT free text, DESIGN.md §6.19); drop+re-add rather than
-- ALTER COLUMN TYPE since the column has never held data.
ALTER TABLE user_profile DROP COLUMN IF EXISTS injuries;
ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS injuries TEXT;
ALTER TABLE user_profile DROP COLUMN IF EXISTS goals;
ALTER TABLE user_profile DROP COLUMN IF EXISTS preferences;

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
