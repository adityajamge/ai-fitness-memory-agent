"""FastAPI application — auth, turns, trace/evidence endpoints, SSE, static SPA serving.

Standard multi-user SaaS model: per-user row scoping is a security boundary (ADR-13.4).

Design: docs/office-hours/02-architecture-overview.md. Phase 1 ships only the
deploy-early hello app (main.py, T10); the real API begins Phase 2.
"""
