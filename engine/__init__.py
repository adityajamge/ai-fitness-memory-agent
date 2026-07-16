"""Memory Engine — the architectural centerpiece.

Deterministic memory layer on CockroachDB: ingestion, hybrid SQL+vector retrieval,
event-driven consolidation, context assembly, and EvidenceTrace construction.
No LLM-provider dependence; model calls arrive through an injected interface.

Design: docs/office-hours/03-memory-engine.md (implementation begins Phase 2).
"""
