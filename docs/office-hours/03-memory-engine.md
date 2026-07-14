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
daily bucketing (gaps stay missing — health data is never interpolated), `ruptures` PELT
for changepoints, bounded lag scan (7–35 days) over whitelisted series pairs, and a
documented "pattern strength" score (effect size × coverage × lag consistency). Presented
as hypothesis, never probability or causal inference. Retraction conditions are **typed
objects** ({metric, comparator/direction, window_days, min_count}) in InsightPayload,
evaluated deterministically; UI prose is rendered from the object
([ADR-13.11](09-decisions.md#adr-13)).

### 5. Context assembly + ranking
Mixes SQL aggregates, timeline slices, semantic hits, and derived insights into one
structured, budgeted context block for the LLM — with memory IDs preserved so the narrator
can cite. Ranking considers relevance, recency, confidence, and provenance. Context
optimization keeps the block inside the model's effective budget.

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

**Citation validation:** the narrator may only cite memory IDs present in the trace. After
generation, the engine mechanically validates every citation in the answer against the
trace; invalid or unsupported citations are flagged to the UI. The honest-narrator contract
([05-agent-architecture.md](05-agent-architecture.md)) is an enforced property, not a prompt
instruction.

**Ingestion receipts are the same artifact in miniature** — a trace of what was created
rather than what was retrieved. One type, two renderings.

### 7. Timeline reconstruction
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

**Deliberately NOT a tool:** `build_evidence_trace`. Evidence traces are emitted by
assembly itself ([module 6](#6-evidence-trace-builder-adr-12), [ADR-12](09-decisions.md#adr-12));
exposing trace construction to the agent would let the model choose what to disclose and
allow the call to be skipped — both defeat the glass box. The UI fetches traces by
`trace_id` through the app API, not through the agent.
