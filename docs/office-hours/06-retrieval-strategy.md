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

The engine contains a **closed set of parameterized query builders**. A tool call selects a
builder and fills its typed slots; the builder composes SQL from vetted fragments with
**bound parameters only**. Dynamic in the narrow sense (filters/grouping assembled per
request from a finite vocabulary), but:

- **No LLM-generated SQL, ever.** The model can only choose a builder and fill typed slots.
- **Zero injection surface by construction** — no query string ever interpolates user content.
  Even the JSONB path, the grouping period, and the timezone are bound parameters.
- **The executed-SQL panel stays testable** — every builder family has fixture tests, so the
  glass box displays queries from a known, verified family.

The families, as implemented ([ADR-14.4](09-decisions.md#adr-14)):

| Family | Answers | Notes |
|---|---|---|
| **aggregate** | "how much protein this month?" | sum/avg/count/min/max over a typed payload field, optional day/week bucketing; `count` here counts *logged values of that metric* |
| **recall** | "when did I complain about my knee?" | vector K-NN over `summary` embeddings |
| **timeline** | "what was I doing in May?" | ordered typed slice of a date range |
| **lookup** | "when did I last eat chicken?" | newest/oldest event of a type, optional exact item containment |
| **count** | "how many workouts in June?" | counts *events of a type*, regardless of which metrics they carry |
| **insight lookup** | "has this been flagged before?" | Phase 5 — structured, freshness-checked ([below](#insight-reuse)) |

Two rules govern the slots themselves:

- **Timezone is never a planner slot** ([ADR-14.10](09-decisions.md#adr-14)) — it belongs to
  the user, not the question, so the engine injects it. A model-chosen timezone would make
  day boundaries model-dependent.
- **Slots are strict; nothing is defaulted into existence** ([ADR-14.11](09-decisions.md#adr-14)).
  A missing date range is rejected rather than guessed — inventing a window answers a
  question the user did not ask. Every planner mistake fails above the database.

Metrics come from a **whitelist derived from the payload registry's typed hot fields**, and
memory types from the registry itself, so the tool schemas the planner sees cannot name a
metric or type the engine does not have — the constraint is in the schema, not in a prompt.

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
([05-agent-architecture.md](05-agent-architecture.md)); the engine owns query construction
([builder families above](#query-construction)).

## <a name="insight-reuse"></a>Insight reuse: structured match first, semantic second

"Return an existing insight instead of recomputing" is a **structured lookup, not a semantic
one**. Insight payloads carry the series/metric identifiers they are about (metric, correlated
series pair, lag window), so `analyze_series(metric="body_fat_pct")` first runs an indexed
query: `status='active'` insights whose payload references that metric (inverted index).

The match passes a **freshness rule**. **Amended in Phase 5 (§4.7): freshness is *derived*, never
stored.** There is no `last_evaluated_at` field — an insight is stale iff its series holds a memory
whose `created_at` is later than the insight's own. `created_at` already means "when we learned it"
(04's bi-temporal model), so this is the schema's own reading of the question; it needs no column
and no migration, and it keeps `memories` append-only apart from
`status`/`superseded_by`/`embedding` (invariant I-13). A stored field would be a second source of
truth for a derivable fact, and the two could disagree.

Since every relevant ingest re-touches affected series synchronously
([ADR-13.1](09-decisions.md#adr-13)), insights are current in normal operation. If the series has been added to since the claim was derived (possible
after a budget-overflow deferral), the engine recomputes on demand; otherwise it returns the
existing insight with its lineage.

Semantic recall over insight embeddings (insights are ordinary memories with embedded
hypothesis text) exists as the **secondary discovery path** — open-ended `recall_memories`
can surface insights the planner didn't map to a specific series.

## Ranking & assembly (design-level)

Ranking is **entirely deterministic** — a documented heuristic composite score, no LLM
anywhere in the ranking path (same determinism boundary as [ADR-12](09-decisions.md#adr-12),
same honesty posture as the pattern-strength score, ADR-13.12). Same inputs → same ranking,
and the per-candidate scores are recorded in `EvidenceTrace.ranking`, so "why the engine
picked these memories" is itself glass-box material. Candidate evidence (aggregates,
timeline slices, semantic hits, insights) is scored on:

1. **Relevance** to the question (vector distance for semantic candidates; exact match for
   structured ones — a filter match is certain by definition)
2. **Confidence** (reconstructed low-confidence memories rank below live ones; the answer can
   hedge: "around early May — estimated"). Provenance is folded into this axis, and the
   *effective* value — the number that actually drove the score — is what the trace records
3. **Recency** — temporal proximity **within the retrieved candidate set**, newest → 1.0,
   oldest → 0.0 ([ADR-14.5](09-decisions.md#adr-14) amends the original "to the question's
   window": assembly deliberately never learns the question's window, because reading it
   would mean parsing the question and breaking the determinism boundary)
4. **Tier** — a high-confidence derived insight that already answers the question outranks
   re-deriving from raw events

plus **diversity caps** (the budget must not fill with near-identical rows). Exact weights
are implementation detail; the commitments above are architectural.

Assembly preserves memory IDs end-to-end so citations survive into the UI, and **the same
memory surfaced by two tools is one candidate**, keeping the better relevance score — which
is how a structured lookup and a semantic recall of the same question merge into one honest
evidence set rather than double-counting.

Context optimization enforces a token budget over **raw events only** — aggregates are
compact by nature and always pass through. The budget applies to what the *narrator* sees:
`EvidenceTrace.evidence` keeps everything retrieved, so the glass box shows more than the
model did and `ranking` explains what was cut ([ADR-14.6](09-decisions.md#adr-14)).

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
