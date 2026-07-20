# 08 — Roadmap & Milestones

> Part of the [office-hours canonical docs](README.md). Related: [09-decisions.md](09-decisions.md), [10-open-questions.md](10-open-questions.md).

**Deadline: 2026-08-19** (Devpost, 2:30am IST). Solo builder + Claude Code. Estimated effort
for the full approach: CC ~2–3 weeks inside ~40 days — real slack exists, protect it.

## Degradation strategy (why the order below is safe)

Milestone 1 produces a **complete, submittable entry** (spine + hosted URL). Every later
milestone only improves an already-valid submission. If anything slips, cut from the top of
the stack, not the bottom: lineage graph first ([07-glass-box-ui.md](07-glass-box-ui.md)),
then OCR depth, then timeline polish.

## The Assignment (before any code)

Mine the real records (chats, gym logs, reports) and **write down the ONE dated causal story
the demo will tell — actual dates and numbers**. If it doesn't exist, pick the fallback story
now. Everything designs backwards from this ([01-product-vision.md](01-product-vision.md));
it must also **survive sanitization** ([ADR-7](09-decisions.md#adr-7)).

## Milestone 1 — The Spine (weekend-scale)

- [x] `git init`, repo scaffold (monorepo `engine/ agent/ api/ web/ cli/`, one Dockerfile),
      MIT/Apache-2.0 license visible ✅ 2026-07-16
- [x] ccloud CLI: provision CockroachDB Cloud cluster — **screen-record it** (tool evidence)
      ✅ cluster provisioned 2026-07-17; recording still to be saved to the evidence folder
- [x] **Day-one canary #1: vector indexing** — `VECTOR(512)` index on the chosen tier, assert
      K-NN ordering on normalized vectors; verify tier/budget limits; canary becomes a
      permanent CI test (ADR-13.8) ✅ 2026-07-17 (green vs local + Cloud cluster)
- [x] **Day-one canary #2: LangGraph PostgresSaver on CockroachDB** — `.setup()`, write/read
      a checkpoint; fallback: thin hand-rolled checkpointer (ADR-13.8) ✅ 2026-07-17 — stock
      saver fails; thin subclass landed ([../engineering/cockroachdb-postgressaver.md](../engineering/cockroachdb-postgressaver.md))
- [ ] `memories` + `users`/`turns`/`evidence_traces` tables, vector/inverted/secondary
      indexes ([04-database-design.md](04-database-design.md))
- [ ] Pydantic payload registry `engine/types.py` (ADR-13.6)
- [ ] LangGraph ingestion node: text → typed events via Bedrock, 16A failure policy
      (success-direct, note-on-failure)
- [ ] Seed replay CLI **with extraction-output caching** (re-runs must not re-call Bedrock)
      and small-batch inserts (vector-index footgun); reconstruct ~3 months through the
      **production pipeline**
- [ ] **Verify the causal story exists in the data** (go/no-go; 13A event-time framing)
- [ ] Two tools: `aggregate_memories`, `recall_memories`
- [ ] Simple email+password auth + sessions (ADR-13.15; abuse/spend controls deferred → TODOS)
- [x] **Re-derive the budget line-item** (Fargate + ALB share — ADR-13.3 amendment, CockroachDB tier, cached replay
      Bedrock cost, live-eval lane) — update README constraint ✅ 2026-07-19: ≈$43–63,
      table in [README.md](README.md#budget-line-item-t13-re-derived-2026-07-19)
- [ ] Bare chat answering the money question
- [x] **Hosted deploy on Amazon ECS Express Mode** (orig. App Runner; ADR-13.3 amendment) (deploy-early — a submittable URL exists from
      Milestone 1 onward) ✅ 2026-07-19 — live, CI→ECR→Express pipeline verified end-to-end
      (URL in [../deploy.md](../deploy.md))

## Milestone 2 — The Engine

- [ ] Consolidation: synchronous scoped scans in-request under the ~300ms budget +
      `analyze_series` on demand; ruptures PELT + bounded lag scan; pattern-strength scoring
      (ADR-13.12); typed retraction-condition evaluation (ADR-13.11)
- [ ] Timeline, aggregation, context-assembly + ranking modules (hard token cap, tested)
- [ ] Photo ingestion: S3 + Bedrock vision → meal events
- [ ] Embedding backfill: opportunistic on next ingest + manual CLI command
- [ ] Full reconstruction replay (6–12 months) into the builder's account
- [ ] **End-to-end turn latency budget measured** (ingest turn, query turn, both-turn) —
      target: receipt < 3s perceived; document the profile

## Milestone 3 — The Glass Box

- [ ] Web UI per wireframe v3, built in ranked order (1→8; empty states are rank 4; lineage
      graph first-to-cut) ([07-glass-box-ui.md](07-glass-box-ui.md))
- [ ] Full app deployed to ECS Express Mode (image already flowing since M1)

## Milestone 4 — Submission

- [ ] Observability + failure-mode story (production-readiness criterion)
- [ ] README: setup/run, architecture diagram, **tools write-up** (what the agent did with
      MCP Server, ccloud CLI, vector indexing — with evidence links)
- [ ] MCP dev-session logs captured; ccloud recording edited in
- [ ] <3-min first-person video (YouTube, public): capture → receipt → money question →
      glass box → (stretch) live insight on ingest
- [ ] Devpost submission with days of slack before Aug 19

## Standing next step

~~Run `/plan-eng-review`~~ **Done 2026-07-12** — all M1-blocking questions locked
([ADR-13](09-decisions.md#adr-13)). Next: the Assignment (OQ5 — verify the causal story in
real data), then Milestone 1.

**Day-to-day execution** of these milestones lives in
[../implementation-roadmap.md](../implementation-roadmap.md) — 8 phases mapping T1–T18 onto
this document's milestone contract (Phases 1–4 ≈ M1, Phase 5 ≈ M2, Phase 6 ≈ M3, Phase 7 ≈ M4).
