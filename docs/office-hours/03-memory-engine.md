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
  timezone, meal type, quantities, nutrition estimates. Payloads are validated through the
  **Pydantic type registry** (`engine/types.py`, one model per memory type, `extra="allow"` —
  [ADR-13.6](09-decisions.md#adr-13)).
- Every event gets: `source`, `provenance` (`live` | `reconstructed`), `confidence`,
  `summary` (for embedding), `payload` (typed JSONB), embedding where text is meaningful.
- **Failure policy ([ADR-13.5](09-decisions.md#adr-13)) — input is never lost:** extraction
  succeeds → typed events written directly in one transaction (no shadow rows); extraction
  fails → a `note` memory persists with a "saved — parsing incomplete" receipt and one inline
  retry; a later successful parse writes typed events and marks the note `superseded_by`.
  Embeddings are nullable; backfill runs opportunistically on the user's next ingest turn,
  plus a manual CLI command.
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

The engine's query layer is a **closed set of parameterized builder families**
([06 → query construction](06-retrieval-strategy.md#query-construction)) — tool calls fill
typed slots, builders compose SQL from vetted fragments with bound parameters. The engine
never interprets natural language; that ends at the agent's planner
([06 → query-planning boundary](06-retrieval-strategy.md#query-planning)). Insight reuse is
a structured, freshness-checked lookup
([06 → insight reuse](06-retrieval-strategy.md#insight-reuse)).

### 4. Event-driven consolidation (NOT scheduled)
Builder decision ([ADR-3](09-decisions.md#adr-3)): consolidation runs
- **on ingest** — scoped to the series the new event touches, **synchronously inside the
  ingestion request under a hard time budget (~300ms)**; overflow defers to on-demand
  ([ADR-13.1](09-decisions.md#adr-13)). Retraction-condition evaluation for affected insights
  rides the same pass.
- **on demand** — when a query needs analysis fresher than existing insights
  (`analyze_series`).

No nightly job, no scheduler, no queue. This also demos live: an insight can appear on
camera the moment a workout is logged — and per [ADR-13.10](09-decisions.md#adr-13) that
live moment is where "flagged the moment it happened" language belongs; insights derived
over reconstructed history use event-time framing with truthful `created_at`.

**Analytics = labeled heuristic pattern flags** ([ADR-13.12](09-decisions.md#adr-13)):
daily bucketing (gaps stay missing — health data is never interpolated) and a documented
"pattern strength" score, presented as hypothesis, never probability or causal inference.

**Amended in Phase 5 — the detector set.** `ruptures` PELT and the bounded 7–35 day lag scan
were removed: measured against the data the Phase 4 replay actually committed, neither had
anything it could honestly run on (the account's one daily series is a four-level step function
written by period expansion, and no valid series *pair* exists). They are replaced by two
deterministic detectors — **`level_shift`** (a metric's level moved between adjacent observations)
and **`intervention_outcome`** (a sparsely measured marker changed, with the structurally detected
changes inside that interval as its lineage). Consolidation observes **assertions, not materialized
period days**, and the strength formula's third factor is **specificity** (1 / competing changes)
rather than lag consistency. `effect` is measured against a **per-series scale in the metric's own
units**, because one relative floor cannot serve both a marker that moves 5× and a body-fat
percentage that never will. Full rationale, rejected alternatives, and the measured numbers:
[../engineering/consolidation-architecture.md](../engineering/consolidation-architecture.md).

Retraction conditions are **typed objects** (`{metric, direction, threshold?, window_days,
min_count}`) in InsightPayload, evaluated deterministically over distinct days; UI prose is
rendered **from** the object so the displayed rule and the evaluated rule cannot disagree
([ADR-13.11](09-decisions.md#adr-13)).

### 5. Context assembly + ranking
Mixes SQL aggregates, timeline slices, semantic hits, and derived insights into one
structured, budgeted context block for the LLM — with memory IDs preserved so the narrator
can cite. Ranking considers relevance, recency, confidence, and provenance. Context
optimization keeps the block inside the model's effective budget.

Assembly is a **pure function of its inputs** — it performs no I/O
([ADR-14.7](09-decisions.md#adr-14)). That is what makes ranking reproducible and
fixture-testable, and it has one consequence worth stating: an aggregate's contributing
memory IDs arrive without snapshot metadata and are not hydrated here (T16's batch-fetch does
that in Phase 6). Assembly returns the context and its trace **as a pair** — there is no code
path that produces one without the other.

Two views of the same evidence ([ADR-14.6](09-decisions.md#adr-14)): the context block holds
the budget-limited, diversity-capped subset the narrator sees, while the trace holds
everything retrieved. The glass box therefore shows more than the model did, and the ranking
scores explain what was cut.

### 6. Evidence trace builder ([ADR-12](09-decisions.md#adr-12))
Every context assembly **deterministically emits an `EvidenceTrace` as a byproduct** — not
an agent-callable tool, and never reconstructed after the fact. The trace is the *receipt*
of assembly: if context was assembled, a trace exists, by construction. The LLM is not in
the loop; the Glass-Box UI reads the trace via the app API.

`EvidenceTrace` contract (design-level):

| Field | Content |
|---|---|
| `trace_id` | Stable ID; persisted with the conversation turn |
| `question` | The user question (or ingestion turn) this trace answers |
| `retrieval_steps` | The executed queries — SQL aggregations and vector searches, with parameters |
| `evidence` | Memory IDs used + snapshot metadata (type, event_time, confidence, provenance, summary) |
| `insights` | Derived insights that participated, each with its own `evidence_ids` lineage |
| `timeline` | The relevant reconstructed timeline slice |
| `ranking` | Why these memories were selected (scores: relevance, confidence, recency, tier) |
| `assembled_at` | Timestamp |
| `citable_ids` | Every memory ID the narrator was permitted to cite this turn — **added Phase 6 M1** (ADR-14.8), so the persisted trace is self-contained |

**Citation validation:** the narrator may only cite memory IDs present in the turn's citable
set. After generation, the engine mechanically validates every citation in the answer;
invalid or unsupported citations are flagged to the UI. The honest-narrator contract
([05-agent-architecture.md](05-agent-architecture.md)) is an enforced property, not a prompt
instruction.

> **✅ Resolved 2026-08-06, Phase 6 M1 ([ADR-14.8](09-decisions.md#adr-14)):** the citable set
> is "budgeted memories ∪ every aggregate/count contributing ID ∪ each participating insight's
> own ID" (`ContextBlock.citable_ids()`), which is **wider** than `trace.evidence` — because
> assembly is pure, an aggregate's contributing rows are IDs without metadata and do not appear
> there. Validating against `trace.evidence` alone would reject valid citations of aggregated
> data. Resolved by **carrying the set into the persisted trace** as `citable_ids`, so "the UI
> reads the trace" is literally true and a validator has exactly one source.
>
> An insight's own `evidence_ids` are deliberately **not** citable: that lineage is *rendered*,
> not cited (open question Q1, resolved narrow — see
> [glass-box-architecture.md §4.1](../engineering/glass-box-architecture.md)).

**Ingestion receipts are the same artifact in miniature** — a trace of what was created
rather than what was retrieved. One type, two renderings.

### 7. Timeline reconstruction
Ordered, typed views over any date range — powers the UI timeline strip and "what was I
doing in May" queries.

## Engine-exposed tools (the agent's only DB access)

| Tool | Contract | Phase |
|---|---|---|
| `log_memory` | Ingest a user turn (text/photo/file) → typed events + receipt | 2 |
| `aggregate_memories` | Parameterized SQL aggregation (sums, averages, group-by period, filters by type/date) | 3 |
| `recall_memories` | Vector search over summaries/narrative, filtered by type/date/status | 3 |
| `get_timeline` | Ordered event slice for a date range | 3 |
| `lookup_events` | Newest/oldest event of a type, optional exact item containment | 3 |
| `count_events` | How many events of a type in a range ([ADR-14.4](09-decisions.md#adr-14)) | 3 |
| `lookup_insights` | Read derived insights **with their lineage**, by series/kind/status — read-only | 5 ✅ |
| `analyze_series` | On-demand consolidation for one series → may write a new derived insight. **Graph-dispatched like `log_memory`, not a query builder** (§4.9): it writes, and a write cannot ride the shared read transaction | 5 ✅ |

The LangGraph agent never issues raw SQL; the engine owns all query construction
(parameterized — judges will poke the sandbox).

**Offering `log_memory` in the same vocabulary is what makes routing tool selection**
([ADR-14.1](09-decisions.md#adr-14)): the planner choosing it *is* the ingest classification,
so no separate intent-routing step exists. Ingestion runs before retrieval within a turn, so
a memory logged now is visible to the same turn's aggregation
([ADR-14.3](09-decisions.md#adr-14)).

**Deliberately NOT a tool:** `build_evidence_trace`. Evidence traces are emitted by
assembly itself ([module 6](#6-evidence-trace-builder-adr-12), [ADR-12](09-decisions.md#adr-12));
exposing trace construction to the agent would let the model choose what to disclose and
allow the call to be skipped — both defeat the glass box. The UI fetches traces by
`trace_id` through the app API, not through the agent.
