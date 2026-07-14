# 01 — Product Vision

> Part of the [office-hours canonical docs](README.md). Related: [02-architecture-overview.md](02-architecture-overview.md), [07-glass-box-ui.md](07-glass-box-ui.md), [09-decisions.md](09-decisions.md).

## Problem

AI health assistants can analyze a meal photo or generate a workout plan, but they treat every
conversation independently. No structured, lifelong understanding of a person's health journey
exists, so personalization stays shallow and temporary.

Two evidence anchors from the design session's landscape research:

- Fitness apps retain ~3–4% of users at day 30; **manual logging friction** is the most-cited
  killer. Conversational capture attacks this directly.
- Agent-memory frameworks (Mem0, Zep, Letta, Cognee, LangMem, Graphiti) are commoditized in
  2026 — but they all treat memories as *conversational facts* for vector retrieval. None
  treats `SUM(protein) GROUP BY week` as a memory operation. Health memory is mostly **typed
  quantitative events that need computation, not just recall** (the session's eureka insight).

## Vision

A personal health coach that never forgets. The user talks naturally — sends a meal photo,
mentions a workout, uploads a blood report — and the system automatically infers date and
context, extracts structure, and stores permanent, evidence-grade memories. Months or years
later, the agent reasons across the full history:

- "Show my protein intake during June." *(SQL aggregation)*
- "When did I last complain about my knee?" *(semantic vector search)*
- "What habits changed before my body fat started decreasing?" *(cross-series causal analysis
  over derived insights)*

**Memory is the product.** The LLM is a replaceable narrator on top of the Memory Engine's
intelligence — the builder's own framing, recorded as the project's architectural centerpiece
(see [09-decisions.md → ADR-1](09-decisions.md#adr-1)).

## The demo "whoa" moment (everything designs backwards from this)

**Time-travel insight.** On camera, the user asks:

> "What changed before my body fat started dropping?"

The agent answers with dated, memory-ID-cited evidence:

> "Protein rose ~96→142g/day from **May 12**; sleep crossed 7.5h/night after **May 19**;
> body fat began falling **Jun 2** — the drop lags the protein change by ~3 weeks. This
> pattern is flagged in your history as a derived insight."

Three properties make this the money shot:

1. **Provably impossible for a stateless assistant** — it requires months of structured history.
2. **The insight already existed before the question was asked** (event-driven consolidation,
   [03-memory-engine.md](03-memory-engine.md)). Framing is honestly bi-temporal
   ([ADR-13.10](09-decisions.md#adr-13)): insights over reconstructed history use event-time
   language; the **"flagged the moment it happened"** beat is delivered live on camera — log a
   real workout, watch the insight appear with a truthful `created_at = now`.
3. **Every claim is clickable** down to raw memory rows and the queries that produced them
   (glass-box UI, [07-glass-box-ui.md](07-glass-box-ui.md)).

The demo video is told **first-person**: the seed data is the builder's real (sanitized)
6–12-month health history, so the story is "my real body-fat drop, and the agent found the
real cause."

## Judging-criteria mapping

| Criterion | How this project scores it |
|---|---|
| Agentic Memory Design | Two-tier memory (episodic events + derived insights with lineage/retraction); confidence + provenance first-class; memory that computes, not just recalls |
| Technical Implementation | Custom Memory Engine on CockroachDB (vector index + JSONB + SQL aggregation in one consistent store); 3 CockroachDB tools evidenced |
| Real-World Impact | Conversational capture attacks the #1 fitness-app churn cause; lifelong health memory is a real unmet need |
| Production Readiness | Deploy-early, standard multi-user model with per-user isolation, never-lose-input ingestion, observability + failure story, sanitized repo dataset |
| Creativity & Originality | "The agent already knew" demo; glass-box evidence UI; the why-not-Mem0 answer rendered on screen |

## Non-goals (for the hackathon)

- Wearable/API integrations (Apple Health, Garmin, etc.) — listed as future ideas only.
- General-purpose OSS memory library (Approach C) — deferred; see
  [09-decisions.md → ADR-6](09-decisions.md#adr-6).
- Deep OCR of arbitrary medical reports — structured manual entry + one parsed example is the
  fallback ([10-open-questions.md → OQ6](10-open-questions.md)).
- Multi-user product polish beyond the judge sandbox — this is a demo, not a launch.
