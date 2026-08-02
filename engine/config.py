"""Environment-driven settings for the app's composition roots (api/, cli/).

The Memory Engine's own logic (``engine/ingestion.py`` etc.) never imports this — it
takes plain injected parameters so it stays config-free and testable (ADR-6 clean
package boundary, ADR-1 model independence). This module is a convenience for the
*callers* that wire the engine together.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# 127.0.0.1, not localhost: psycopg tries ::1 first and Windows takes ~130s to give
# up when the node listens on IPv4 only (same reason as the canaries).
DEFAULT_DATABASE_URL = "postgresql://root@127.0.0.1:26257/defaultdb?sslmode=disable"

# Titan Text Embeddings V2, 512-dim, normalized (ADR-13.2 — unit vectors make the
# Euclidean C-SPANN index equivalent to cosine ranking).
DEFAULT_EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBED_DIMS = 512

# A capable default extraction/narration model on Bedrock; swappable by config only
# (model-independence contract, ADR-13 / 05-agent-architecture.md).
DEFAULT_EXTRACTION_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"

# Which provider implementation backs each role. 'bedrock' is the architecture's default
# (ADR-13). Provider selection is **per role** — see Settings.resolved_llm_provider /
# resolved_embedding_provider below — because the two concerns vary independently:
#
#   LLM (extract_events/plan/narrate)  cost, quality, vendor availability — freely swappable
#   embeddings (embed)                 pinned by VECTOR(512); NOT freely swappable once data
#                                      exists, since vectors from different models are not
#                                      comparable (changing it means re-embedding every row)
#
# `MODEL_PROVIDER` remains supported as the shorthand for "same provider for both roles".
DEFAULT_MODEL_PROVIDER = "bedrock"
DEFAULT_CLAUDE_API_MODEL_ID = "claude-opus-5"
# Extraction and planning are structured slot-filling; narration is short prose. Low effort
# keeps dev turns fast and cheap without touching the deterministic layer below.
DEFAULT_CLAUDE_API_EFFORT = "low"

# When the model cannot infer a timezone for an event, fall back to this and record
# lowered confidence (builder decision, Phase 2 planning).
DEFAULT_TZ = "Asia/Kolkata"


def _first_configured(*candidates: str | None) -> str:
    """First non-blank candidate, canonicalized; the default when all are blank.

    Blank (``""`` / whitespace) counts as *unset*, not invalid — otherwise a stray
    ``LLM_PROVIDER=`` line in a ``.env`` would hard-fail a run that ``MODEL_PROVIDER`` could
    have answered. Checking blankness before falling through matters because a whitespace-only
    string is truthy in Python and would otherwise shadow the next candidate.
    """
    for candidate in candidates:
        cleaned = (candidate or "").strip().lower()
        if cleaned:
            return cleaned
    return DEFAULT_MODEL_PROVIDER


@dataclass(frozen=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL
    aws_region: str = "us-east-1"
    extraction_model_id: str = DEFAULT_EXTRACTION_MODEL_ID
    embedding_model_id: str = DEFAULT_EMBEDDING_MODEL_ID
    embed_dims: int = EMBED_DIMS
    default_tz: str = DEFAULT_TZ
    session_ttl_seconds: int = 60 * 60 * 24 * 14  # 14 days
    backfill_batch: int = 32  # opportunistic embeddings backfilled per ingest turn (T15)
    model_provider: str = DEFAULT_MODEL_PROVIDER  # shorthand: both roles, when unset below
    #: Per-role overrides. ``None`` means "not configured" and defers to ``model_provider``,
    #: which is what keeps every pre-existing config (and ``Settings(model_provider=...)`` in
    #: tests) behaving exactly as before.
    llm_provider: str | None = None
    embedding_provider: str | None = None
    claude_api_model_id: str = DEFAULT_CLAUDE_API_MODEL_ID
    claude_api_effort: str = DEFAULT_CLAUDE_API_EFFORT

    @property
    def resolved_llm_provider(self) -> str:
        """Provider backing ``extract_events``/``plan``/``narrate``.

        Precedence: ``LLM_PROVIDER`` → ``MODEL_PROVIDER`` → the default. Resolved here rather
        than in ``load_settings`` so a directly-constructed ``Settings`` (tests, embedding
        callers) obeys the same rules as one built from the environment.
        """
        return _first_configured(self.llm_provider, self.model_provider)

    @property
    def resolved_embedding_provider(self) -> str:
        """Provider backing ``embed``. Precedence: ``EMBEDDING_PROVIDER`` → ``MODEL_PROVIDER``
        → the default.

        ⚠️ Effectively a one-way door once memories exist: embeddings from different models
        occupy different vector spaces, so changing this after data is written requires
        re-embedding every row (null the column, then ``python -m cli.backfill``).
        """
        return _first_configured(self.embedding_provider, self.model_provider)


def _load_dotenv_if_present() -> None:
    """Populate the environment from a local ``.env`` if python-dotenv is installed.

    Development convenience only, and deliberately best-effort: ``python-dotenv`` is a **dev**
    dependency, so in the deployed image this is a no-op and configuration comes from real
    environment variables (the ECS task definition) exactly as before.

    Existing variables always win over the file (``override=False``), so an explicit
    ``DATABASE_URL=... uvicorn ...`` still beats what is written in ``.env``. Documented in
    ``.env.example``; also called by the root ``conftest.py`` so tests that read the
    environment directly see the same configuration the app does.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # production image / minimal install
        return
    load_dotenv(override=False)


def load_settings() -> Settings:
    """Build Settings from the environment, falling back to the defaults above."""
    _load_dotenv_if_present()
    return Settings(
        database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        extraction_model_id=os.environ.get("EXTRACTION_MODEL_ID", DEFAULT_EXTRACTION_MODEL_ID),
        embedding_model_id=os.environ.get("EMBEDDING_MODEL_ID", DEFAULT_EMBEDDING_MODEL_ID),
        embed_dims=int(os.environ.get("EMBED_DIMS", EMBED_DIMS)),
        default_tz=os.environ.get("DEFAULT_TZ", DEFAULT_TZ),
        session_ttl_seconds=int(os.environ.get("SESSION_TTL_SECONDS", 60 * 60 * 24 * 14)),
        backfill_batch=int(os.environ.get("BACKFILL_BATCH", 32)),
        model_provider=os.environ.get("MODEL_PROVIDER", DEFAULT_MODEL_PROVIDER),
        # Absent (None) rather than defaulted, so the per-role properties can tell
        # "not configured" from "explicitly set", and defer to MODEL_PROVIDER.
        llm_provider=os.environ.get("LLM_PROVIDER"),
        embedding_provider=os.environ.get("EMBEDDING_PROVIDER"),
        claude_api_model_id=os.environ.get("CLAUDE_API_MODEL_ID", DEFAULT_CLAUDE_API_MODEL_ID),
        claude_api_effort=os.environ.get("CLAUDE_API_EFFORT", DEFAULT_CLAUDE_API_EFFORT),
    )
