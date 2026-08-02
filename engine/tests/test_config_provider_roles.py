"""Per-role provider resolution in Settings (LLM vs embeddings).

Provider selection is per role because the two concerns vary independently: the LLM is
freely swappable, while the embedding provider is pinned by `VECTOR(512)` and effectively a
one-way door once memories exist. `MODEL_PROVIDER` stays supported as the shorthand meaning
"same provider for both roles" — every assertion about it below is a backward-compatibility
guarantee, not merely current behavior.

Pure: no network, no database, no provider SDKs.
"""

from __future__ import annotations

import pytest

from engine.config import DEFAULT_MODEL_PROVIDER, Settings, load_settings

PROVIDER_ENV = ("MODEL_PROVIDER", "LLM_PROVIDER", "EMBEDDING_PROVIDER")


@pytest.fixture()
def clean_env(monkeypatch):
    """Hermetic environment for load_settings().

    Two things are needed, and the second is easy to miss: clearing the variables is not
    enough, because ``load_settings`` calls ``_load_dotenv_if_present()`` and would repopulate
    them straight from the developer's real ``.env``. Stubbing the loader is what makes these
    assertions about resolution logic rather than about whatever is on this machine.
    """
    for name in PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("engine.config._load_dotenv_if_present", lambda: None)
    return monkeypatch


# ── backward compatibility: MODEL_PROVIDER alone drives both roles ──────────────────────


def test_model_provider_alone_sets_both_roles() -> None:
    s = Settings(model_provider="claude_api")
    assert s.resolved_llm_provider == "claude_api"
    assert s.resolved_embedding_provider == "claude_api"


def test_default_settings_resolve_to_the_architecture_default() -> None:
    s = Settings()
    assert s.resolved_llm_provider == DEFAULT_MODEL_PROVIDER == "bedrock"
    assert s.resolved_embedding_provider == DEFAULT_MODEL_PROVIDER


# ── per-role overrides ──────────────────────────────────────────────────────────────────


def test_roles_can_differ_the_whole_point_of_the_split() -> None:
    """The configuration this change exists for: Claude API reasoning, Bedrock embeddings."""
    s = Settings(llm_provider="claude_api", embedding_provider="bedrock")
    assert s.resolved_llm_provider == "claude_api"
    assert s.resolved_embedding_provider == "bedrock"


def test_llm_provider_overrides_model_provider_for_its_role_only() -> None:
    s = Settings(model_provider="bedrock", llm_provider="claude_api")
    assert s.resolved_llm_provider == "claude_api"
    assert s.resolved_embedding_provider == "bedrock"  # untouched by the LLM override


def test_embedding_provider_overrides_model_provider_for_its_role_only() -> None:
    s = Settings(model_provider="claude_api", embedding_provider="bedrock")
    assert s.resolved_embedding_provider == "bedrock"
    assert s.resolved_llm_provider == "claude_api"


# ── normalization ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["BEDROCK", "  bedrock  ", "Bedrock"])
def test_provider_names_are_case_and_whitespace_insensitive(raw: str) -> None:
    assert Settings(llm_provider=raw).resolved_llm_provider == "bedrock"


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_role_falls_through_to_model_provider(blank: str) -> None:
    """An empty env var is 'unset', not 'invalid' — otherwise `LLM_PROVIDER=` in a .env
    would hard-fail a run that MODEL_PROVIDER could have answered."""
    s = Settings(model_provider="claude_api", llm_provider=blank)
    assert s.resolved_llm_provider == "claude_api"


def test_blank_everything_falls_back_to_the_default() -> None:
    assert Settings(model_provider="", llm_provider="").resolved_llm_provider == "bedrock"


# ── environment wiring ──────────────────────────────────────────────────────────────────


def test_load_settings_reads_the_per_role_variables(clean_env) -> None:
    clean_env.setenv("LLM_PROVIDER", "claude_api")
    clean_env.setenv("EMBEDDING_PROVIDER", "bedrock")
    s = load_settings()
    assert s.resolved_llm_provider == "claude_api"
    assert s.resolved_embedding_provider == "bedrock"


def test_load_settings_without_per_role_vars_keeps_legacy_behavior(clean_env) -> None:
    clean_env.setenv("MODEL_PROVIDER", "claude_api")
    s = load_settings()
    assert s.llm_provider is None  # absent, not defaulted -- that is what enables fallthrough
    assert s.embedding_provider is None
    assert s.resolved_llm_provider == "claude_api"
    assert s.resolved_embedding_provider == "claude_api"


def test_load_settings_with_nothing_set_uses_the_default(clean_env) -> None:
    s = load_settings()
    assert s.resolved_llm_provider == "bedrock"
    assert s.resolved_embedding_provider == "bedrock"
