# 07 — Glass-Box UI Philosophy

> Part of the [office-hours canonical docs](README.md). Related: [01-product-vision.md](01-product-vision.md), [05-agent-architecture.md](05-agent-architecture.md).

## Philosophy (builder's words, from the approval of wireframe v3)

> "The application should remain **conversation-first**, where chat is the primary interface
> for every interaction... Every interaction should be automatically transformed into
> structured memories by the Memory Engine. While the conversation remains the primary user
> experience, the Memory Engine should remain **continuously visible** through evidence
> panels, timelines, retrieved memories, confidence scores, and reasoning... The goal is not
> to replace chat with a memory dashboard, but to make the Memory Engine **transparently
> enhance every conversation**."

Two failed drafts define the boundaries: v1 (chat-dominant) made memory look like a sidebar
afterthought; v2 (memory-dashboard-dominant) demoted the conversation. v3 holds both truths.

## Approved wireframe (v3)

![Wireframe v3 — conversation-first, memory-transparent](assets/wireframe-v3-approved.png)

Editable source: [assets/wireframe-v3-approved.html](assets/wireframe-v3-approved.html).
This is **visual grammar, not visual design** — fonts/color/polish come in a later design pass
(`/design-consultation`).

## The grammar

| Element | Job |
|---|---|
| **Chat pane (primary, widest)** | Every interaction: questions, meal photos, workout updates, scans, reports. Composer says "Ask anything, or log it." |
| **Citation chips** | Every factual claim in an answer is a clickable chip (`Jun 2 · scan 21.4%`) resolving to evidence rows |
| **Inline memory receipts** | After each ingestion turn: "✦ 1 memory created: meal · lunch · 46g protein · conf 0.9 + embedding" — moment-to-moment proof that talking = logging |
| **Live engine pane** | Always visible, "following the conversation": evidence rows (provenance + confidence badges + memory IDs), reasoning lineage, retrieval queries |
| **Memory timeline strip** | Permanent, top: memory density over the full history, changepoint markers (`◆ May 12 protein ↑`), "now" marker |
| **Top-bar stats** | `4,182 memories · 312 days · 23 insights · CockroachDB ●●●` — says "memory system," not "chatbot," before a word is read |

## Build order (ranked — cut bottom-up if time compresses)

1. Chat with citation chips
2. Evidence rows with provenance/confidence badges
3. Inline memory receipts
4. Live engine-pane updates
5. Retrieval-query display ("how this was retrieved")
6. Timeline strip with changepoint markers
7. **Reasoning lineage graph — explicitly first-to-cut** (a visualization project hiding in a
   bullet; fallback: text lineage list)

Plus judge-sandbox auth ([OQ3](10-open-questions.md)).

## Why the glass box is load-bearing (not decoration)

- It makes "Agentic Memory Design" **scoreable** — judges see evidence rows and real queries,
  not claims.
- It renders the hybrid SQL+vector argument on screen (the why-not-Mem0 answer,
  [06-retrieval-strategy.md](06-retrieval-strategy.md)).
- It converts "an LLM said a thing" into "a database proved a thing."
- It keeps the narrator honest: uncited claims are visibly uncited
  ([05-agent-architecture.md](05-agent-architecture.md), answer contract).
