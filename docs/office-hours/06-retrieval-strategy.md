# 06 — Retrieval Strategy (Hybrid SQL + Vector)

> Part of the [office-hours canonical docs](README.md). Related: [03-memory-engine.md](03-memory-engine.md), [04-database-design.md](04-database-design.md).

## The core insight (session eureka — this is the "why not Mem0" answer)

Off-the-shelf memory frameworks assume memories are conversational facts: extract → embed →
vector-retrieve. But health memory is mostly **typed quantitative events**, and the questions
users actually ask are *computations*:

> "Show my protein intake during June" is `SUM(payload->>'protein_g') ... GROUP BY week`,
> **not** a similarity search. No vector store can compute it; no memory framework ships it
> as a memory operation.

The Memory Engine therefore treats **SQL aggregation as a first-class memory operation**,
alongside vector search — in one transactionally consistent CockroachDB store, so computed
facts and semantic recall never disagree.

## <a name="query-planning"></a>The query-planning boundary (where NL understanding ends)

There is exactly one place in the system that understands natural language: the **LangGraph
planner node** ([05-agent-architecture.md](05-agent-architecture.md)). Everything below the
tool-call boundary is deterministic.

```
Natural language ("What changed before my body fat started dropping?")
        │
        ▼
┌─ Agent (LLM) ────────────────────────────────────────────────┐
│  Planner: classify turn (ingest / query / both),             │
│  select tools, fill TYPED parameter slots                    │
└──────────────────────────────────────────────────────────────┘
        │  structured tool calls only, e.g.
        │  analyze_series(metric="body_fat_pct")
        │  aggregate_memories(metric="protein_g", agg="avg",
        │                     group_by="week", date_range=…)
        ▼
┌─ Memory Engine (deterministic) ──────────────────────────────┐
│  Query builders → parameterized SQL / vector search          │
│  → evidence + ranking → context block + EvidenceTrace        │
└──────────────────────────────────────────────────────────────┘
        │                                   │
        ▼                                   ▼
   LLM narrates (prose + citations)    Glass-box UI (renders trace)
```

Boundary contract:

- The planner's entire output is **structured tool calls with typed slots** (validated like
  any payload). "Mixed retrieval" is the planner issuing several tool calls; assembly merges
  them into one ranked evidence set with one trace — it is not an engine mode.
- The engine **never interprets language**. The `question` field in the trace is carried for
  display, not parsed. Swapping the LLM provider changes who fills the slots, never what
  executes ([ADR-13](09-decisions.md#adr-13) model-independence).

## <a name="query-construction"></a>Query construction: closed builder families, never free-form

The engine contains a **closed set of parameterized query builders** — aggregation
(sum/avg/count over a payload field, grouped by period), last/first-event lookup, timeline
slice, vector K-NN, insight lookup. A tool call selects a builder and fills its typed slots;
the builder composes SQL from vetted fragments with **bound parameters only**. Dynamic in
the narrow sense (filters/grouping assembled per request from a finite vocabulary), but:

- **No LLM-generated SQL, ever.** The model can only choose a builder and fill typed slots.
- **Zero injection surface by construction** — no query string ever interpolates user content.
- **The executed-SQL panel stays testable** — every builder family has fixture tests, so the
  glass box displays queries from a known, verified family.

Food/item filters (e.g. "when did I last eat chicken?") have two paths: structured
containment over extracted payload items (inverted index) and vector search over `summary`
as the fuzzy fallback. The planner may request either; the trace records which ran.

## Question → retrieval-path mapping

| Question shape | Path | Example |
|---|---|---|
| Quantitative over time | **SQL aggregation** over typed payloads | "Average protein on weekends vs weekdays?" |
| Point lookup / timeline | **SQL** (indexed `user_id, type, event_time`) | "When did I last eat chicken?" |
| Narrative / semantic | **Vector search** over `summary` embeddings | "When did I complain about my knee?" |
| Causal / cross-series | **Derived insights first** (already computed), else `analyze_series` on demand | "What changed before my body fat dropped?" |
| Mixed | Both + assembly | "How's my recovery been since I started creatine?" |

Routing is the retrieval-planning step of the agent graph
([05-agent-architecture.md](05-agent-architecture.md)); the engine owns query construction.

## Ranking & assembly (design-level)

Candidate evidence (aggregates, timeline slices, semantic hits, insights) is ranked by:

1. **Relevance** to the question (semantic score / filter match)
2. **Confidence** (reconstructed low-confidence memories rank below live ones; the answer can
   hedge: "around early May — estimated")
3. **Recency / temporal proximity** to the question's window
4. **Tier** — a high-confidence derived insight that already answers the question outranks
   re-deriving from raw events

Assembly preserves memory IDs end-to-end so citations survive into the UI. Context
optimization enforces a token budget: aggregates are compact by nature; raw-event inclusion
is capped and summarized beyond the cap.

Every assembly also **emits a deterministic `EvidenceTrace`** — executed queries (SQL +
vector, with parameters), the selected evidence set, participating insights with lineage,
and the ranking scores that justified selection. The trace, not the model's output, drives
the glass-box UI ([ADR-12](09-decisions.md#adr-12),
[03-memory-engine.md](03-memory-engine.md#6-evidence-trace-builder-adr-12)). This is also
what makes the consistency argument below *verifiable* rather than asserted: the queries
shown on screen are the queries that ran.

## Consistency property worth stating

Because events, embeddings, and derived insights live in **one CockroachDB store with
transactional writes**, an ingested meal is immediately visible to *both* the aggregation
path and (once embedded) the semantic path — there is no window where the agent's "computed"
and "remembered" views of the user diverge. This is the architectural argument judges can
verify in the glass box (queries shown on screen).

## Non-goals

- No knowledge-graph runtime (Graphiti's Neo4j model rejected — [ADR-5](09-decisions.md#adr-5)).
- No re-ranking model / learned retriever for the hackathon — heuristic ranking above is
  enough; revisit post-hackathon.
