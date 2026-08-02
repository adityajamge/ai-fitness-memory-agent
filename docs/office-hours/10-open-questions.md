# 10 — Open Questions

> Part of the [office-hours canonical docs](README.md). Updated by the **/plan-eng-review of
> 2026-07-12** — five of the original seven questions are now RESOLVED; full decisions in
> [09-decisions.md → ADR-13](09-decisions.md#adr-13). When a remaining one is resolved,
> record it there and check it off here.

## Resolved (2026-07-12 engineering review)

| # | Question | Resolution |
|---|---|---|
| ~~OQ1~~ | Embedding model + dimensionality | **Bedrock Titan Text Embeddings V2, 512-dim, normalized** (`VECTOR(512)`); unit vectors make L2 ≡ cosine on CockroachDB's Euclidean-only C-SPANN index |
| ~~OQ2~~ | Hosting target | **AWS App Runner**, single Docker image (FastAPI + built Vite/React SPA); deploys in Milestone 1 with the bare chat. *Amended 2026-07-19: **Amazon ECS Express Mode** — App Runner closed to new customers 2026-04-30; see ADR-13.3 amendment* |
| ~~OQ3~~ | Judge sandbox isolation | **Superseded by the production multi-user model:** standard accounts, per-user row scoping, simple email+password auth; no sandbox, no sample-data onboarding. Judges sign up like any user (empty account); deep-history features are demonstrated via the builder's mature account (video / walkthrough) — accepted trade-off |
| ~~OQ4~~ | Consolidation analytics scope | **Labeled heuristic pattern flags:** daily bucketing (gaps stay missing — no interpolation), `ruptures` PELT changepoints, bounded lag scan (7–35d) over whitelisted series pairs, documented heuristic "pattern strength" score — presented as hypothesis, never causal inference |
| ~~OQ7~~ | Retraction mechanics | **Typed retraction-condition objects** in InsightPayload ({metric, comparator/direction, window_days, min_count}), evaluated deterministically in the synchronous consolidation pass; prose rendered FROM the object for the UI |

## Still open

| # | Question | Decide at | Notes |
|---|---|---|---|
| OQ6 | Blood-report parsing depth in 40 days | Milestone 2 | Fallback: structured manual entry + one parsed example |

**OQ5 — RESOLVED 2026-08-02 (Phase 4 M5): GO, and it is the *deficiency-correction* story, not
body fat.** The real data yields **Story A**: Vitamin D 6.20 → 38.4 ng/mL and B12 152 → 752
pg/mL between 2026-03-25 and 2026-07-03, with the causal chain (supplement start 2026-03-28,
dose reduction 2026-06-24, protein intervention) present and retrievable. Verified in the
database through the production retrieval path after replaying 424 records, not by reading the
reconstruction — `lookup_events` returns both blood reports; semantic recall on *"what changed
before my vitamin D recovered"* returns the intervention records; weekly protein aggregates trace
124 → 217 → 227 → 252 g. Story C (the body-scan fallback) is **not needed**. Sanitization for the
public repo (ADR-7) remains outstanding and is Phase 7 work — the raw reconstruction and payload
table are gitignored and stay local. See [ADR-15](09-decisions.md#adr-15).

## Advisory items carried from reviews (non-blocking)

- Milestone 1's public URL ships with only simple auth — production abuse/spend controls are
  **explicitly out of scope** this iteration (builder decision, 2026-07-12); captured in
  TODOS for a future iteration. Residual risk: unbounded Bedrock spend if abused.
- Replay/bootstrap is the dominant Bedrock cost line — the replay CLI caches extraction
  outputs so re-runs don't re-call the model (task T-list).
- Have an honest answer ready for "isn't the vector index decorative at one user's scale?" —
  yes at demo scale; it's the same store/index the architecture needs at lifelong/multi-user
  scale, and the README tools write-up says so plainly.
