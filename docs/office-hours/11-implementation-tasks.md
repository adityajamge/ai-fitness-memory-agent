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
- [x] **T3 (P1, ~1d / ~1h)** — engine — Pydantic payload registry per memory type, `extra="allow"`, typed hot fields ✅ 2026-07-20: 9 types registered; drift canary green (`engine/tests/test_types.py`)
  - Surfaced by: Code quality issue 6 (6A) — key-drift prevention (ADR-13.6)
  - Files: `engine/types.py` · Verify: drift-canary test (unknown keys accepted, known keys typed)
  - Note: hot fields are plain Pydantic attributes on the validated model — no separate accessor methods were needed
- [x] **T4 (P1, ~1d / ~1.5h)** — engine — Ingestion failure policy: success-direct, note-on-failure, supersede-on-retry; nullable embeddings ✅ 2026-07-20: failure matrix of [../engineering/ingestion-transaction-boundaries.md](../engineering/ingestion-transaction-boundaries.md) §9 implemented and tested; deviations recorded in that doc's §13 (all closed: D3+D1 2026-07-21, D2 2026-07-22)
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
  - ⚠️ **Blocking contract decision — settle before writing the validator** ([ADR-14.8](09-decisions.md#adr-14)): the citable set is `trace.evidence` **∪ aggregate/count contributing IDs** (`ContextBlock.citable_ids()`). Because assembly is pure ([ADR-14.7](09-decisions.md#adr-14)), an aggregate's contributing rows are IDs without metadata and are absent from `trace.evidence` — validating against it alone would flag *valid* citations of aggregated data as invalid. Either validate against the full citable set, or carry those IDs into the persisted trace (recommended, keeps "the UI reads the trace" literally true; pairs naturally with T16's batch-fetch hydration).
  - Note: the trace object and its by-construction emission already exist (Phase 3, M1/M3); T7 adds **persistence + validation**, not the artifact.
- [x] **T8 (P1, ~2.5d / ~4h)** — cli — Replay CLI: markdown→JSONL converter, direct-ingest, idempotent resume, supersession-based corrections
  - **Status 2026-08-02: COMPLETE — all five milestones. OQ5 resolved GO; decisions promoted to [ADR-15](09-decisions.md#adr-15).**
    M5 replayed 424 real records into the live account: 0 failed, 0 NULL embeddings, zero
    extraction calls, idempotent rerun (0 new / 424 skipped). **498 tests green.**
    M1 dataset contract + converter (`cli/replay_dataset.py`, `cli/convert.py`) · M2 resume ledger
    (`cli/replay_ledger.py`) · M3 engine support (`ingest_events` + shared persistence tail;
    `normalize_item` query-time stop-gap) · M4 orchestration (`cli/replay.py` — resume loop, §4.10
    halt + failure artifact, §4.12 correction workflow via `ingest_events_superseding`, §4.15 exit
    codes + advisory freshness check, `--rebuild-ledger`). Landed as 8 reviewed commits
    (`483cd79`…`791a99b`). **445 tests green** against a clean CockroachDB cluster.
  - Surfaced by: Outside voice #7 (replay = dominant cost) + Step 0 vector-batch footgun
  - Architecture (LOCKED 2026-07-30, amended 2026-08-02): [../engineering/replay-architecture.md](../engineering/replay-architecture.md) — 15 decisions, risk analysis, M1–M5 plan, test strategy
  - ⚠️ **Contract amended 2026-07-30 — the extraction cache is removed.** This entry originally
    specified "extraction-output cache (re-runs never re-call Bedrock)". Structuring the
    reconstruction moved to **dev-time** tooling ([ADR-10](09-decisions.md#adr-10)'s already-locked
    dev-time/runtime split), so every record takes the direct-ingest path and replay makes **zero
    extraction calls** — leaving nothing to cache. The guarantee the cache provided is now supplied
    *by construction* (re-runs are free unconditionally, enforced by an `extract_calls == 0`
    property test) rather than by mechanism. Keeping it would mean maintaining an unexercised path
    — the "no infrastructure built solely for completeness" rule. The trigger that would bring it
    back (a record-schema field requiring inference from free text) and the full re-add recipe are
    recorded in that doc's §8. **Not a trigger:** future users importing history — ADR-13.4 rules
    that out. Note-confidence threading also left scope (unreachable via replay); its TODOS.md
    entry stays open for Phase 5.
  - Files: `cli/convert.py`, `cli/replay_dataset.py`, `cli/replay.py`, `cli/replay_ledger.py`,
    `docs/evidence/compositions.json`, `engine/ingestion.py` (`ingest_events` + shared tail),
    `engine/retrieval.py` (`normalize_item` stop-gap)
  - Verify: full run makes **zero extraction calls** (property test); interrupted run resumes
    without duplicates and a forced double-run produces none (the review's top-severity risk);
    ledger rebuildable from DB state; converter is byte-deterministic
- [x] **T9 (P1, ~2d / ~3h)** — api — Simple email+password auth + sessions; per-user scoping enforced and tested as a security boundary ✅ 2026-07-20: scrypt hashing, opaque HttpOnly session cookie, scoping enforced in every `engine/repository.py` query
  - Surfaced by: D14 builder decision (ADR-13.15) + test gap "user A cannot read user B"
  - Files: `api/auth.py` (primitives), `api/routers/auth.py` (routes), `api/deps.py` (`get_current_user` boundary), `api/tests/test_scoping.py`
  - Note: a cross-user read returns **404**, not 403 — existence is not probeable (see [12-test-plan.md](12-test-plan.md) failure-modes table)
- [x] **T10 (P1, ~1d / ~2h)** — infra — Dockerfile (FastAPI + built Vite SPA) + **ECS Express Mode** deploy (orig. App Runner — closed to new customers; ADR-13.3 amendment) + GitHub Actions CI with single-node CockroachDB service ✅ 2026-07-19: live and verified — push→CI→ECR→Express pipeline exercised end-to-end; URL in [../deploy.md](../deploy.md) (Phase 1 serves the hello page; "bare chat" beat lands with Milestone 1)
  - Surfaced by: Arch issue 3 (3A) + test infra (8A); deploy-early rule
  - Files: `Dockerfile`, `.github/workflows/ci.yml` · Verify: live URL serves the bare chat in Milestone 1
- [ ] **T11 (P2, ~1.5d / ~2h)** — web — Empty-state design for new accounts (timeline, stats, insights, engine pane): inviting, not broken
  - Surfaced by: Outside voice #3 — consequence of the multi-user model (ADR-13.4); rank 4 in the [07 build order](07-glass-box-ui.md)
  - Files: `web/src/components/EmptyStates.tsx`
- [ ] **T12 (P2, ~1d / ~1.5h)** — api — Measure + document end-to-end turn latency (ingest / query / both); receipt < 3s perceived target
  - Surfaced by: Outside voice #13 — only consolidation was budgeted
  - Files: `docs/latency.md`
- [x] **T13 (P2, ~0.5d / ~30min)** — docs — Re-derive the budget line-item (Fargate + ALB share per ADR-13.3 amendment, CockroachDB tier, cached replay Bedrock cost, live eval lane) ✅ 2026-07-19: ≈$43–63 for the remaining window, inside the $50–100 envelope — table in [README.md → Budget line-item](README.md#budget-line-item-t13-re-derived-2026-07-19)
  - Surfaced by: Outside voice #16 — $50–100 predates the locked choices
  - Files: `docs/office-hours/README.md`
- [ ] **T14 (P2, ~2d / ~3h)** — evals — Live-model eval lane: extraction golden set (~30) + citation compliance (~15), separate from mocked CI
  - Surfaced by: Test issue 9 (9A) + outside voice #14 — an eval against a mock tests the fixture
  - Files: `evals/extraction.py`, `evals/citation.py`
- [x] **T15 (P2, ~0.5d / ~1h)** — engine — Embedding backfill trigger: opportunistic on next ingest + manual CLI command ✅ 2026-07-20: `IngestionService.backfill_embeddings` + post-commit opportunistic sweep + `python -m cli.backfill`
  - Surfaced by: Outside voice #12 — backfill had no execution home (no scheduler exists by design)
  - Files: `engine/ingestion.py`, `cli/backfill.py`
  - Test gap closed 2026-07-22: opportunistic half in `engine/tests/test_ingestion.py`, CLI entry point in `cli/tests/test_backfill.py` (page draining, idempotency, embed-outage termination, user discovery, `--user`/`--all`/argparse)
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
| D | T10 Docker/CI/ECS Express | infra | — |
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
