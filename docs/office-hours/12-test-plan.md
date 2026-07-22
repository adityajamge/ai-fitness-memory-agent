# 12 — Test Plan (from /plan-eng-review, 2026-07-12)

> Part of the [office-hours canonical docs](README.md). Decisions: tests run against **real
> single-node CockroachDB Docker** locally and in CI (8A / [ADR-13.8](09-decisions.md#adr-13));
> Bedrock is mocked behind the injected model interface everywhere **except** the live-model
> eval lane (9A / ADR-13.9). Frameworks: **pytest** (backend), **Vitest** (web components),
> **Playwright** (E2E). 100% of the paths below ship WITH their feature, not after.

## Coverage map (all paths are planned-GAPs until implemented)

> **Implemented as of 2026-07-22 (Phase 2):** the `engine/ingestion` block except photo/S3
> (Phase 5), the whole `engine/types` block, both canaries, the signup/scoping user flows,
> the Bedrock-provider empty-result contract (D1), the CLI backfill, and the reprocess
> endpoint (D2) — **56 Phase-2 tests + 2 canaries = 58 collected**. Everything else below
> is still a planned gap.

```
CODE PATHS                                               USER FLOWS
[+] engine/ingestion                                     [+] Signup → first log
  ├── text → typed events (meal/workout/sleep/            ├── [→E2E] signup → log meal → receipt
  │        scan/weight/supplement/note routing)           │        → memory visible in engine pane
  ├── 16A: extraction fails → note persists,              ├── empty-account states (timeline, stats,
  │        receipt "saved — parsing incomplete"           │        insights, engine pane) — T11
  ├── retry succeeds → typed events supersede note        [+] Mature-account flows (builder data)
  ├── photo → S3 → vision extraction                      ├── [→E2E] money question → cited answer
  ├── S3 failure → turn still persists                    │        → chips resolve → trace panel
  ├── embedding fails → NULL embedding row                ├── "protein in June" → aggregate matches
  └── backfill (next-ingest + CLI) re-embeds ✓            │        known account numbers
[+] engine/types (6A registry)                            [+] Interaction edges
  ├── per-type validation accepts extra keys              ├── double-submit same meal (deliberate,
  └── typed hot fields coerce/reject (drift canary)       │        defined behavior)
[+] engine/retrieval                                      ├── [→E2E] slow Bedrock (10s) → UI state
  ├── aggregate: sum/avg, day/week grouping,              ├── session expiry mid-conversation
  │        type+date filters, EMPTY RESULT, tz edges      └── [→E2E] user A cannot read user B's
  ├── recall: vector top-k, status='active' filter,               memories or traces (SECURITY)
  │        NULL-embedding rows excluded
  └── timeline: ordered slice, range edges
[+] engine/consolidation (1A sync + budget, 17A analytics)
  ├── fixture series WITH changepoint → insight row, correct evidence_ids
  ├── series WITHOUT changepoint → no insight
  ├── budget exceeded → defers cleanly, ingestion still succeeds
  ├── typed retraction condition met → status='retracted' (never deleted)
  ├── supersession chains via superseded_by
  └── pattern-strength formula: documented, deterministic on fixtures
[+] engine/trace (ADR-12)
  ├── PROPERTY: no assembled context without a persisted trace
  ├── trace fields complete (queries, evidence, insights, ranking)
  ├── citation validation: valid / invalid-ID paths (honest scope per ADR-13.13)
  └── trace fetchable by trace_id, user-scoped
[+] agent graph
  ├── routing: ingest / query / both turns
  └── [→EVAL] narrator citation compliance
[+] canaries (permanent)
  ├── VECTOR(512) index + K-NN ordering on normalized vectors (T1)
  └── PostgresSaver on CockroachDB checkpoint round-trip (T2)
[+] cli/replay
  ├── small-batch inserts (vector-index footgun guard)
  ├── extraction cache: second run makes zero model calls
  └── idempotent resume after interrupt
[+] LLM extraction: [→EVAL] golden set, tolerance ranges

TOTAL: 33 paths  |  E2E: 4 (Playwright)  |  EVAL: 2 (live-model lane)
```

## Evals (live model — separate lane from mocked CI; manual trigger + pre-demo checklist)

- `evals/extraction.py`: ~30 golden logging messages → tolerance-range assertions on macros,
  meal type, absolute AND relative timestamps ("yesterday", "this morning")
- `evals/citation.py`: ~15 question turns → every factual claim cites a valid trace memory ID

## Failure modes (each new codepath: one realistic production failure)

| Codepath | Failure | Test? | Handled? | User sees |
|---|---|---|---|---|
| Extraction | Bedrock throttles mid-turn | yes | 16A note-fallback | "saved — parsing incomplete" |
| Embedding | Bedrock embed call fails | yes | NULL + backfill | receipt notes pending embedding |
| Photo upload | S3 failure | yes | turn persists without photo | clear partial-save message |
| Consolidation | scan exceeds budget | yes | defer to on-demand | nothing (by design; insight arrives later) |
| Trace persistence | turn-commit failure | yes | single transaction (13.14) | turn retriable, never half-recorded |
| Citation | model cites bad ID | yes | validation flag | visible flag in UI |
| Scoping | cross-user access attempt | yes (security) | denied at query layer | 404 (indistinguishable from "not found" — existence is not probeable) |
| Replay | interrupt mid-run | yes | idempotent resume | resume command |
| Budget | hostile usage exhausts Bedrock spend | no | none (deferred, ADR-13.15) | model-call errors → note-fallback (non-silent) |

**Critical gaps (no test AND no handling AND silent): 0.** The budget row is a deliberate,
documented acceptance (TODOS.md), and its failure mode is non-silent thanks to 16A.

## Consumed by

`/qa` and `/qa-only` read the sibling artifact at
`~/.gstack/projects/Cockroach-db-hackathon/adity-nogit-eng-review-test-plan-20260712.md`;
this doc is the repo-canonical copy.
