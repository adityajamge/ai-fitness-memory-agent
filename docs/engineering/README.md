# Engineering Deep Dives

Implementation-level documents for future maintainers (human or AI). These are neither
ADRs (decisions live in [office-hours/09-decisions.md](../office-hours/09-decisions.md))
nor user guides — each one teaches the reasoning behind a non-obvious piece of the
codebase: why it exists, how it was investigated, and how to modify it safely.

Conventions:

- One document per subsystem or investigation, written while the work is fresh.
- Documents here are the **canonical implementation reference** for their subject —
  ADRs, tasks, and code comments should link here instead of duplicating explanations.
- Each document ends with maintenance notes (when it can be removed/revisited, what not
  to touch casually) and a related-files table.

## Index

| Document | Subject |
|---|---|
| [cockroachdb-postgressaver.md](cockroachdb-postgressaver.md) | LangGraph checkpointing on CockroachDB: why stock `PostgresSaver` fails, the T2 canary investigation, and the `CockroachDBSaver` compatibility layer (`agent/checkpointer.py`) |
| [ingestion-transaction-boundaries.md](ingestion-transaction-boundaries.md) | The Phase 2 write path (`engine/ingestion.py`): transaction boundaries, the never-lose-input guarantee, and how partial extraction / validation / embedding failures interact with it (ADR-13.5) |
| [graph-state-durability.md](graph-state-durability.md) | Keeping heavyweight runtime objects out of the LangGraph checkpoint (ADR-14.9): why "clear it before END" fails, the carrier design, LangGraph's silent-drop behavior, and why the serde guard — not the state schema — is the guarantee |
| [vector-index-and-filtered-knn.md](vector-index-and-filtered-knn.md) | How semantic recall actually executes: the T1 canary proved *unfiltered* K-NN uses the vector index, but the product's user-scoped filtered query does not — the measurement, the decision to keep correctness, and the honest scale answer T17 needs |
| [replay-architecture.md](replay-architecture.md) | **Locked** architecture for Phase 4 / T8 (replay CLI): the dev-time/runtime split that makes replay zero-extraction, the idempotent resume ledger and why naive resume silently duplicates memories, the supersession-based correction workflow, period-expansion rules, the `normalize_item` contract, and the M1–M5 plan |
