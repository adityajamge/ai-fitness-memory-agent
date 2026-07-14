# 10 — Open Questions

> Part of the [office-hours canonical docs](README.md). Updated by the **/plan-eng-review of
> 2026-07-12** — five of the original seven questions are now RESOLVED; full decisions in
> [09-decisions.md → ADR-13](09-decisions.md#adr-13). When a remaining one is resolved,
> record it there and check it off here.

## Resolved (2026-07-12 engineering review)

| # | Question | Resolution |
|---|---|---|
| ~~OQ1~~ | Embedding model + dimensionality | **Bedrock Titan Text Embeddings V2, 512-dim, normalized** (`VECTOR(512)`); unit vectors make L2 ≡ cosine on CockroachDB's Euclidean-only C-SPANN index |
| ~~OQ2~~ | Hosting target | **AWS App Runner**, single Docker image (FastAPI + built Vite/React SPA); deploys in Milestone 1 with the bare chat |
| ~~OQ3~~ | Judge sandbox isolation | **Superseded by the production multi-user model:** standard accounts, per-user row scoping, simple email+password auth; no sandbox, no sample-data onboarding. Judges sign up like any user (empty account); deep-history features are demonstrated via the builder's mature account (video / walkthrough) — accepted trade-off |
| ~~OQ4~~ | Consolidation analytics scope | **Labeled heuristic pattern flags:** daily bucketing (gaps stay missing — no interpolation), `ruptures` PELT changepoints, bounded lag scan (7–35d) over whitelisted series pairs, documented heuristic "pattern strength" score — presented as hypothesis, never causal inference |
| ~~OQ7~~ | Retraction mechanics | **Typed retraction-condition objects** in InsightPayload ({metric, comparator/direction, window_days, min_count}), evaluated deterministically in the synchronous consolidation pass; prose rendered FROM the object for the UI |

## Still open

| # | Question | Decide at | Notes |
|---|---|---|---|
| OQ5 | Does the real data yield the body-fat causal story, or a different one? | **The Assignment, before any code** | Go/no-go for the demo script; must survive sanitization (ADR-7 as revised by ADR-13); note 13A framing — insights over reconstructed history use event-time language |
| OQ6 | Blood-report parsing depth in 40 days | Milestone 2 | Fallback: structured manual entry + one parsed example |

## Advisory items carried from reviews (non-blocking)

- Milestone 1's public URL ships with only simple auth — production abuse/spend controls are
  **explicitly out of scope** this iteration (builder decision, 2026-07-12); captured in
  TODOS for a future iteration. Residual risk: unbounded Bedrock spend if abused.
- Replay/bootstrap is the dominant Bedrock cost line — the replay CLI caches extraction
  outputs so re-runs don't re-call the model (task T-list).
- Have an honest answer ready for "isn't the vector index decorative at one user's scale?" —
  yes at demo scale; it's the same store/index the architecture needs at lifelong/multi-user
  scale, and the README tools write-up says so plainly.
