# 03 — Memory Engine Design (the centerpiece)

> Part of the [office-hours canonical docs](README.md). Related: [04-database-design.md](04-database-design.md), [06-retrieval-strategy.md](06-retrieval-strategy.md), [09-decisions.md](09-decisions.md).

**Premise 1 (builder's words, revised during the session):** *"The core differentiator is a
custom Memory Engine that transforms raw memories into structured evidence, historical
context, and reasoning for the LLM. The LLM should remain replaceable and model-agnostic,
while the Memory Engine becomes the core of the application's intelligence."*

The engine is an internal package with a clean boundary (so it could be extracted as a
library post-hackathon — see [ADR-6](09-decisions.md#adr-6)) and **no LLM-provider
dependence**. Model calls it needs (extraction, embeddings) go through an injected interface.

## Two-tier memory model

| Tier | What | Examples | Written by |
|---|---|---|---|
| **Episodic events** | Typed, timestamped facts | meal, workout, sleep, body_scan, blood_report, weight, note/conversation | Ingestion |
| **Derived insights** | Hypotheses computed *from* events, with lineage | "protein ↑ + sleep ↑ preceded body-fat ↓ (conf 0.82)" | Consolidation |

Derived insights are ordinary rows (`type='insight'`) whose payload carries: hypothesis text,
**evidence event IDs (provenance lineage)**, confidence, and a **retraction condition**
(e.g. "retract if 3+ counterexamples in rolling 30d"). Retraction never deletes — it flips
`status='retracted'`; *the engine's history of being wrong is itself memory.*

Design donor (concepts only, time-boxed reading — not the runtime): Graphiti's bi-temporal
model — when it happened vs. when we learned it — and fact-invalidation semantics. Directly
useful because reconstructed memories have exactly this split (event_time estimated, learned
now).

## Modules

### 1. Ingestion
- Input: free text, meal photos (via S3), report files, structured updates — all through
  conversation.
- Bedrock (via the injected model interface) extracts **typed events**: infers date/time and
  timezone, meal type, quantities, nutrition estimates.
- Every event gets: `source`, `provenance` (`live` | `reconstructed`), `confidence`,
  `summary` (for embedding), `payload` (typed JSONB), embedding where text is meaningful.
- Emits a **memory receipt** (what was created) for the UI.

### <a name="replay"></a>2. Seed replay (reconstruction)
- The builder's real 6–12-month history (chats, gym logs, diet records, reports) is
  LLM-assisted-reconstructed into structured events. **No invented facts**: uncertain details
  carry lowered `confidence` and estimated timestamps; provenance is `reconstructed`.
- The replay CLI pushes these through the **production ingestion pipeline** — proving the
  pipeline and producing identical-shape memories. Raw SQL seeding is banned.
- The repo ships only the **sanitized derivative** dataset ([ADR-7](09-decisions.md#adr-7)).

### 3. Hybrid retrieval
See [06-retrieval-strategy.md](06-retrieval-strategy.md). Summary: quantitative questions
compile to **SQL aggregation over typed payloads**; narrative/semantic questions use
**vector search over embeddings**; complex questions combine both plus existing derived
insights. One consistent store means no sync gap between the two.

### 4. Event-driven consolidation (NOT scheduled)
Builder decision ([ADR-3](09-decisions.md#adr-3)): consolidation runs
- **on ingest** — scoped to the series the new event touches (log a body scan → re-scan the
  body-fat series for changepoints and lagged correlations against behavior series);
- **on demand** — when a query needs analysis fresher than existing insights.

No nightly job, no scheduler. This also demos live: an insight can appear on camera the
moment a workout is logged. Analytics scope (which changepoint/correlation methods) is
[OQ4](10-open-questions.md); the bar is *honest and simple*, not a stats research project.

### 5. Context assembly + ranking
Mixes SQL aggregates, timeline slices, semantic hits, and derived insights into one
structured, budgeted context block for the LLM — with memory IDs preserved so the narrator
can cite. Ranking considers relevance, recency, confidence, and provenance. Context
optimization keeps the block inside the model's effective budget.

### 6. Timeline reconstruction
Ordered, typed views over any date range — powers the UI timeline strip and "what was I
doing in May" queries.

## Engine-exposed tools (the agent's only DB access)

| Tool | Contract (design-level) |
|---|---|
| `log_memory` | Ingest a user turn (text/photo/file) → typed events + receipt |
| `aggregate_memories` | Parameterized SQL aggregation (sums, averages, group-by period, filters by type/date) |
| `recall_memories` | Vector search over summaries/narrative, filtered by type/date/status |
| `get_timeline` | Ordered event slice for a date range |
| `analyze_series` | On-demand consolidation: changepoint/correlation scan → may write new derived insights |

The LangGraph agent never issues raw SQL; the engine owns all query construction
(parameterized — judges will poke the sandbox).
