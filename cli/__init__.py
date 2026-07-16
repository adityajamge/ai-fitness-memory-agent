"""Command-line tools — seed replay (with extraction cache), embedding backfill.

Replay pushes reconstructed history through the production ingestion pipeline;
raw SQL seeding is banned (ADR-2).

Design: docs/office-hours/03-memory-engine.md#replay (implementation begins Phase 4).
"""
