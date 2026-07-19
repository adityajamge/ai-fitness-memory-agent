# 11 — Implementation Tasks (from /plan-eng-review, 2026-07-12)

> Part of the [office-hours canonical docs](README.md). Synthesized from the engineering
> review's findings — each task cites its source. Decisions behind them:
> [09-decisions.md → ADR-13](09-decisions.md#adr-13). Test details: [12-test-plan.md](12-test-plan.md).
> Priorities: P1 blocks ship · P2 lands same branch · P3 follow-up.
>
> **This is the backlog (what & why).** For day-to-day execution — phases, sequencing, demo
> checkpoints, commit milestones — use
> [../implementation-roadmap.md](../implementation-roadmap.md), which groups these tasks
> into 8 phases.

## Task list

- [x] **T1 (P1, human: ~0.5d / CC: ~1h)** — engine/db — Day-one canary #1: `VECTOR(512)` index + K-NN ordering on normalized vectors; becomes a permanent CI test ✅ 2026-07-17: green vs local single-node v26.2.4 AND the real Cloud cluster
  - Surfaced by: Step 0 — C-SPANN is preview, Euclidean-only; tier verification (ADR-13.2/13.8)
  - Files: `engine/tests/test_vector_canary.py` · Verify: pytest against single-node CockroachDB Docker AND once against the real cluster
- [x] **T2 (P1, ~0.5d / ~1h)** — agent — Day-one canary #2: LangGraph PostgresSaver on CockroachDB (`.setup()`, write/read a checkpoint); fallback = thin hand-rolled checkpointer ✅ 2026-07-17: stock saver FAILS (Postgres-only read SQL); fallback = thin subclass `agent/checkpointer.py`; green vs local AND Cloud cluster (ADR-13.8 outcome)
  - Surfaced by: Outside voice #10 — unverified compatibility bet (ADR-13.8)
  - Files: `agent/tests/test_checkpointer_canary.py` · Implementation details: [../engineering/cockroachdb-postgressaver.md](../engineering/cockroachdb-postgressaver.md)
- [ ] **T3 (P1, ~1d / ~1h)** — engine — Pydantic payload registry per memory type, `extra="allow"`, typed hot-field accessors
  - Surfaced by: Code quality issue 6 (6A) — key-drift prevention (ADR-13.6)
  - Files: `engine/types.py` · Verify: drift-canary test (unknown keys accepted, known keys typed)
- [ ] **T4 (P1, ~1d / ~1.5h)** — engine — Ingestion failure policy: success-direct, note-on-failure, supersede-on-retry; nullable embeddings
  - Surfaced by: Arch issue 5 (5A) + outside voice #6 (16A → ADR-13.5)
  - Files: `engine/ingestion.py` · Verify: extraction-failure fixture → note persists, receipt states "parsing incomplete"
- [ ] **T5 (P1, ~1.5d / ~2h)** — engine — Typed retraction-condition schema + deterministic evaluator in the consolidation pass
  - Surfaced by: Outside voice #5 (15A → ADR-13.11)
  - Files: `engine/types.py`, `engine/consolidation.py` · Verify: fixture where condition met → `status='retracted'`, never deleted
- [ ] **T6 (P1, ~3d / ~5h)** — engine — Sync consolidation under ~300ms budget: daily bucketing (gaps stay missing), ruptures PELT, bounded lag scan (7–35d), documented pattern-strength formula
  - Surfaced by: Arch issue 1 (1A) + outside voice #9 (17A → ADR-13.1/13.12)
  - Files: `engine/consolidation.py` · Verify: fixture series with/without changepoint; budget-exceeded → ingestion still succeeds
- [ ] **T7 (P1, ~2d / ~3h)** — engine/api — EvidenceTrace persistence (`evidence_traces` table, same transaction as the turn) + citation validation with honestly-scoped guarantee
  - Surfaced by: ADR-12 + outside voice #8/#11 (ADR-13.13/13.14)
  - Files: `engine/trace.py`, `api/turns.py` · Verify: property test — no assembled context without a persisted trace; invalid-citation fixture flagged
- [ ] **T8 (P1, ~2d / ~3h)** — cli — Replay CLI: extraction-output cache (re-runs never re-call Bedrock), small-batch inserts, idempotent resume
  - Surfaced by: Outside voice #7 (replay = dominant cost) + Step 0 vector-batch footgun
  - Files: `cli/replay.py` · Verify: interrupted run resumes without duplicates; second run makes zero model calls
- [ ] **T9 (P1, ~2d / ~3h)** — api — Simple email+password auth + sessions; per-user scoping enforced and tested as a security boundary
  - Surfaced by: D14 builder decision (ADR-13.15) + test gap "user A cannot read user B"
  - Files: `api/auth.py`, `api/tests/test_scoping.py`
- [ ] **T10 (P1, ~1d / ~2h)** — infra — Dockerfile (FastAPI + built Vite SPA) + App Runner deploy + GitHub Actions CI with single-node CockroachDB service 🔶 2026-07-19: code complete (`Dockerfile`, `.dockerignore`, `.github/workflows/ci.yml`, `api/main.py` hello app, [../deploy.md](../deploy.md)); tick when the one-time AWS setup is done and the live URL serves
  - Surfaced by: Arch issue 3 (3A) + test infra (8A); deploy-early rule
  - Files: `Dockerfile`, `.github/workflows/ci.yml` · Verify: live URL serves the bare chat in Milestone 1
- [ ] **T11 (P2, ~1.5d / ~2h)** — web — Empty-state design for new accounts (timeline, stats, insights, engine pane): inviting, not broken
  - Surfaced by: Outside voice #3 — consequence of the multi-user model (ADR-13.4); rank 4 in the [07 build order](07-glass-box-ui.md)
  - Files: `web/src/components/EmptyStates.tsx`
- [ ] **T12 (P2, ~1d / ~1.5h)** — api — Measure + document end-to-end turn latency (ingest / query / both); receipt < 3s perceived target
  - Surfaced by: Outside voice #13 — only consolidation was budgeted
  - Files: `docs/latency.md`
- [ ] **T13 (P2, ~0.5d / ~30min)** — docs — Re-derive the budget line-item (App Runner idle, CockroachDB tier, cached replay Bedrock cost, live eval lane)
  - Surfaced by: Outside voice #16 — $50–100 predates the locked choices
  - Files: `docs/office-hours/README.md`
- [ ] **T14 (P2, ~2d / ~3h)** — evals — Live-model eval lane: extraction golden set (~30) + citation compliance (~15), separate from mocked CI
  - Surfaced by: Test issue 9 (9A) + outside voice #14 — an eval against a mock tests the fixture
  - Files: `evals/extraction.py`, `evals/citation.py`
- [ ] **T15 (P2, ~0.5d / ~1h)** — engine — Embedding backfill trigger: opportunistic on next ingest + manual CLI command
  - Surfaced by: Outside voice #12 — backfill had no execution home (no scheduler exists by design)
  - Files: `engine/ingestion.py`, `cli/backfill.py`
- [ ] **T16 (P3, ~0.5d / ~1h)** — api/web — Batch-fetch evidence rows by ID (`WHERE id = ANY(...)`); in-process cache for timeline/stats queries
  - Surfaced by: Performance advisory notes
  - Files: `api/evidence.py`
- [ ] **T17 (P3, ~0.5d / ~30min)** — docs — README tools write-up: honest framing of the vector index's role at demo scale vs lifelong scale
  - Surfaced by: Outside voice #17
  - Files: `README.md`
- [ ] **T18 (P2, ~0.5d / ~30min)** — docs — Demo script rewrite for ADR-13.10: event-time framing for reconstructed insights; the live insight beat happens on camera
  - Surfaced by: Outside voice #1 (13A)
  - Files: `docs/demo-script.md`

## Parallelization lanes

| Lane | Steps | Modules | Depends on |
|---|---|---|---|
| A | T1/T2 canaries → T3 → T4 → T5/T6 → T7 | engine/ | — |
| B | T9 auth + api | api/ | — |
| C | T8 replay → reconstruction (the Assignment feeds it) | cli/ | Lane A ingestion (T4) |
| D | T10 Docker/CI/App Runner | infra | — |
| E | T11+ web UI | web/ | Trace/API contract from Lane A (T7) |

Launch **A + B + D in parallel**; C after T4; E after T7's contract stabilizes. No shared-module conflicts.

## Outside-voice disposition record (17 findings, 2026-07-12)

| OV# | Finding (short) | Disposition |
|---|---|---|
| 1 | Replay clock vs "flagged Jun 3" | Decision 13A → ADR-13.10 (honest framing) + T18 |
| 2 | Docs contradicted locked decisions | Reconciled 2026-07-12 (all docs updated) |
| 3 | Empty judge accounts / no empty states | Trade-off accepted (ADR-13.4); empty states → T11 |
| 4 | Abuse/spend surface | Builder deferral → ADR-13.15 + TODOS.md |
| 5 | Prose retraction conditions | Decision 15A → ADR-13.11 + T5 |
| 6 | Write-first contradiction | Decision 16A → ADR-13.5 + T4 |
| 7 | Replay cost/schedule unbudgeted | T8 (extraction cache) + T13 (budget) |
| 8 | Citation validation oversold | ADR-13.13 (honest scope) + docs updated |
| 9 | Analytics pseudo-rigor | Decision 17A → ADR-13.12 + T6 |
| 10 | PostgresSaver unverified | T2 canary (ADR-13.8) |
| 11 | Two conversation stores | ADR-13.14 (turns table = UI truth) + T7 |
| 12 | Backfill had no home | T15 |
| 13 | Turn latency unbudgeted | T12 |
| 14 | Eval tests the mock | T14 (live-model lane) |
| 15 | ADR-7 vs production model | ADR-13.4 narrows ADR-7; docs updated |
| 16 | Budget not line-itemized | T13 + assumption 4 updated |
| 17 | Vector index decorative at demo scale | T17 (honest README answer) |
