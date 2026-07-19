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
