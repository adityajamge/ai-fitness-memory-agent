# Office Hours — Canonical Design Documentation

> Generated from the `/office-hours` design session on **2026-07-10**.
> Source design doc: `~/.gstack/projects/Cockroach-db-hackathon/adity-nogit-design-20260710-021555.md`
> (Status: **APPROVED**, spec-review score 9/10 after 2 adversarial review rounds).
>
> These documents are the **project's source of truth** for future Claude sessions, planning
> (`/plan-eng-review`, `/autoplan`), implementation, and reviews. If code and these docs
> disagree, either the code is wrong or a decision changed — in the latter case, update the
> doc and record the change in [09-decisions.md](09-decisions.md).
>
> **Engineering review completed 2026-07-12** (`/plan-eng-review`): 14 decisions locked —
> stack, hosting, embeddings, multi-user model, failure policies, analytics honesty — see
> [ADR-12 and ADR-13](09-decisions.md#adr-12). Only OQ5 (causal story) and OQ6 (report
> parsing depth) remain open ([10-open-questions.md](10-open-questions.md)).

## The project in three sentences

An AI health companion for the **CockroachDB × AWS Agentic Memory Hackathon** (deadline
2026-08-19) where **persistent memory is the product**. Every meal, workout, body scan, blood
report, and conversation becomes typed, evidence-grade memory in CockroachDB; a custom
**Memory Engine** — not the LLM — is the application's intelligence, transforming raw memories
into structured evidence, historical context, and reasoning for a fully replaceable model.
The demo money shot: asked *"what changed before my body fat started dropping?"*, the agent
answers with dated, memory-ID-cited evidence spanning months — and reveals it had already
flagged the insight when the data arrived.

## Document map

| Doc | What it covers | Read when |
|---|---|---|
| [01-product-vision.md](01-product-vision.md) | Problem, vision, whoa moment, retention thesis, judging-criteria mapping | Starting any product/scope discussion |
| [02-architecture-overview.md](02-architecture-overview.md) | System components, data flow, AWS + CockroachDB tool usage, diagrams | Starting any technical work |
| [03-memory-engine.md](03-memory-engine.md) | The centerpiece: ingestion, two-tier memory, event-driven consolidation, context assembly | Building or changing the engine |
| [04-database-design.md](04-database-design.md) | `memories` schema, indexes, memory types, payload conventions, retraction model | Touching the database |
| [05-agent-architecture.md](05-agent-architecture.md) | LangGraph graph, engine-exposed tools, model independence | Building or changing the agent |
| [06-retrieval-strategy.md](06-retrieval-strategy.md) | Hybrid SQL + vector retrieval, when each path fires, ranking | Building retrieval or debugging answers |
| [07-glass-box-ui.md](07-glass-box-ui.md) | UI philosophy, approved wireframe v3, ranked sub-features + cut order | Building the UI |
| [08-roadmap.md](08-roadmap.md) | Milestones 1–4, the Assignment, degradation strategy, submission checklist | Planning any week |
| [09-decisions.md](09-decisions.md) | ADRs: decisions, trade-offs, assumptions, **rejected alternatives** (incl. ADR-12 evidence traces, ADR-13 eng-review lockdown) | Before re-opening any settled question |
| [10-open-questions.md](10-open-questions.md) | Open questions — 5 of 7 resolved 2026-07-12; OQ5/OQ6 remain | Before starting Milestone 1 |
| [11-implementation-tasks.md](11-implementation-tasks.md) | The 18 review-derived tasks, parallelization lanes, outside-voice dispositions | Planning any build session |
| [12-test-plan.md](12-test-plan.md) | Coverage map (33 paths), eval suites, failure-modes table, test infra | Writing any code or test |

Assets (approved wireframe v3 PNG + HTML) live in [assets/](assets/).

## Hard constraints (never violate)

- **Hackathon gates:** public OSS repo (MIT/Apache-2.0 visible), hosted demo URL, <3-min public
  video, **≥2 CockroachDB tools** demonstrably used, **≥1 AWS service**, submitted by 2026-08-19.
- **Privacy:** seed data is the builder's real health history. Everything judge-facing (repo,
  demo DB, replay dataset, video) ships only a **sanitized derivative**; raw reconstruction
  inputs stay local. See [09-decisions.md → ADR-7](09-decisions.md#adr-7).
- **Model independence:** the memory layer has no LLM/provider dependence. CockroachDB-native
  by design — that's the point, not lock-in.
- **No infrastructure built solely for completeness** (builder's explicit rule).
- **Budget:** ~$50–100 total for 40 days — re-derived line-item in Milestone 1. Abuse/spend
  controls are explicitly out of scope this iteration ([ADR-13.15](09-decisions.md#adr-13));
  accepted risk, monitored via billing alerts.
