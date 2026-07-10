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
