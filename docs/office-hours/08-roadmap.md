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

- [ ] `git init`, repo scaffold, MIT/Apache-2.0 license visible
- [ ] ccloud CLI: provision CockroachDB Cloud cluster — **screen-record it** (tool evidence)
- [ ] **Day one: verify distributed vector indexing on the chosen tier** (toy table:
      dimensionality, index build, query latency) — hard-gate load-bearing; also verify
      tier/budget limits
- [ ] `memories` table + vector/inverted/secondary indexes ([04-database-design.md](04-database-design.md))
- [ ] LangGraph ingestion node: text → typed events via Bedrock
- [ ] Seed replay CLI; reconstruct ~3 months of history through the **production pipeline**
- [ ] **Verify the causal story exists in the data** (go/no-go on the demo script)
- [ ] Two tools: `aggregate_memories`, `recall_memories`
- [ ] Bare chat answering the money question
- [ ] **Minimal hosted deploy** of that bare chat (deploy-early), including **cost guards /
      request caps** before the URL is public

## Milestone 2 — The Engine

- [ ] Event-driven consolidation: on-ingest scoped scans + `analyze_series` on demand;
      derived insights with provenance + retraction ([03-memory-engine.md](03-memory-engine.md))
- [ ] Timeline, aggregation, context-assembly + ranking modules
- [ ] Photo ingestion: S3 + Bedrock vision → meal events
- [ ] Full reconstruction replay (6–12 months, sanitized derivative for the public DB)

## Milestone 3 — The Glass Box

- [ ] Web UI per wireframe v3, built in ranked order (1→7), lineage graph first-to-cut
      ([07-glass-box-ui.md](07-glass-box-ui.md))
- [ ] Judge sandbox: write-capable, isolated, pristine demo user protected ([OQ3](10-open-questions.md))
- [ ] Hosted deploy of the full app (target per [OQ2](10-open-questions.md))

## Milestone 4 — Submission

- [ ] Observability + failure-mode story (production-readiness criterion)
- [ ] README: setup/run, architecture diagram, **tools write-up** (what the agent did with
      MCP Server, ccloud CLI, vector indexing — with evidence links)
- [ ] MCP dev-session logs captured; ccloud recording edited in
- [ ] <3-min first-person video (YouTube, public): capture → receipt → money question →
      glass box → (stretch) live insight on ingest
- [ ] Devpost submission with days of slack before Aug 19

## Standing next step

Run `/plan-eng-review` on the design (it auto-discovers the approved design doc) before
Milestone 1 code — it locks [the open questions](10-open-questions.md) that block M1.
