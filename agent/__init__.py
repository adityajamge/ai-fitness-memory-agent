"""LangGraph agent — model-agnostic orchestration.

The planner node is the system's only natural-language-understanding layer; it emits
typed tool calls executed by the Memory Engine. Bedrock is the default provider.

Design: docs/office-hours/05-agent-architecture.md (implementation begins Phase 3).
"""
