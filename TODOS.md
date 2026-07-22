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
