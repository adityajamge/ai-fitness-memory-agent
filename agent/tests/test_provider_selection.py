"""Per-role provider selection and CompositeProvider delegation.

The change this covers exists because a single MODEL_PROVIDER cannot express the deployment
we actually need: Claude API for reasoning, Bedrock/Titan for embeddings. Two properties are
load-bearing here:

* **Delegation is exact** — `embed` must never reach the LLM provider and vice versa. A
  mis-routed call would silently use the wrong vendor.
* **Identical roles return the concrete provider unwrapped** — that is the backward-
  compatibility guarantee that lets every pre-existing config, and the existing provider
  tests, keep working without edits.

Pure: no network, no database, no provider SDKs (both fakes are local).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent.providers import BEDROCK, CLAUDE_API, CompositeProvider, build_default_provider
from engine.config import Settings
from engine.model import EmbeddingError, ExtractedEvent, ExtractionError, ModelProvider

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
TZ = "Asia/Kolkata"


class _RecordingProvider:
    """Records which methods were called so routing can be asserted, not assumed."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[str] = []

    def extract_events(self, text: str, *, now: datetime, tz: str) -> list[ExtractedEvent]:
        self.calls.append("extract_events")
        return []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append("embed")
        return [[0.0] * 512 for _ in texts]

    def plan(self, question: str, tools, *, now: datetime, tz: str):
        self.calls.append("plan")
        return []

    def narrate(self, question: str, context) -> str:
        self.calls.append("narrate")
        return f"narrated by {self.label}"


def _composite() -> tuple[CompositeProvider, _RecordingProvider, _RecordingProvider]:
    llm, embedder = _RecordingProvider("llm"), _RecordingProvider("embedder")
    return CompositeProvider(llm=llm, embedder=embedder), llm, embedder


# ── the composite satisfies the protocol it replaces ────────────────────────────────────


def test_composite_structurally_satisfies_model_provider() -> None:
    """Why no engine signature changes: ModelProvider is a runtime_checkable Protocol, so a
    delegating object *is* a ModelProvider without inheritance or registration."""
    composite, _, _ = _composite()
    assert isinstance(composite, ModelProvider)


# ── delegation routing ──────────────────────────────────────────────────────────────────


def test_embed_goes_only_to_the_embedder() -> None:
    composite, llm, embedder = _composite()
    vectors = composite.embed(["a", "b"])

    assert embedder.calls == ["embed"]
    assert llm.calls == []  # the LLM must never see an embedding call
    assert len(vectors) == 2 and len(vectors[0]) == 512


@pytest.mark.parametrize("method", ["extract_events", "plan", "narrate"])
def test_llm_methods_go_only_to_the_llm(method: str) -> None:
    composite, llm, embedder = _composite()

    if method == "extract_events":
        composite.extract_events("250g curd", now=NOW, tz=TZ)
    elif method == "plan":
        composite.plan("how much protein?", [], now=NOW, tz=TZ)
    else:
        composite.narrate("how much protein?", None)

    assert llm.calls == [method]
    assert embedder.calls == []  # the embedder must never see a reasoning call


def test_narrate_returns_the_llm_result_unchanged() -> None:
    composite, _, _ = _composite()
    assert composite.narrate("q", None) == "narrated by llm"


# ── error propagation: the load-bearing contracts must survive delegation ───────────────


def test_embedding_error_propagates_unchanged() -> None:
    """EmbeddingError must still reach the engine so the row lands with a NULL embedding for
    later backfill — swallowing or translating it here would break that contract."""

    class _FailingEmbedder(_RecordingProvider):
        def embed(self, texts):
            raise EmbeddingError("titan unavailable")

    composite = CompositeProvider(llm=_RecordingProvider("llm"), embedder=_FailingEmbedder("e"))
    with pytest.raises(EmbeddingError, match="titan unavailable"):
        composite.embed(["x"])


def test_extraction_error_propagates_unchanged() -> None:
    """ExtractionError must still reach the engine so never-lose-input's note fallback fires."""

    class _FailingLLM(_RecordingProvider):
        def extract_events(self, text, *, now, tz):
            raise ExtractionError("model refused")

    composite = CompositeProvider(llm=_FailingLLM("llm"), embedder=_RecordingProvider("e"))
    with pytest.raises(ExtractionError, match="model refused"):
        composite.extract_events("x", now=NOW, tz=TZ)


# ── factory selection matrix ────────────────────────────────────────────────────────────


def test_mixed_roles_build_a_composite_wired_the_right_way_round() -> None:
    """The configuration this whole change exists for."""
    from agent.providers.bedrock import BedrockProvider
    from agent.providers.claude_api import ClaudeAPIProvider

    built = build_default_provider(
        Settings(llm_provider=CLAUDE_API, embedding_provider=BEDROCK)
    )
    assert isinstance(built, CompositeProvider)
    assert isinstance(built.llm, ClaudeAPIProvider)
    assert isinstance(built.embedder, BedrockProvider)


def test_identical_roles_return_the_concrete_provider_unwrapped() -> None:
    """Backward compatibility: pre-existing configs keep their exact object type, not merely
    equivalent behavior. This is why the existing provider tests need no edits."""
    from agent.providers.bedrock import BedrockProvider

    built = build_default_provider(Settings(model_provider=BEDROCK))
    assert isinstance(built, BedrockProvider)
    assert not isinstance(built, CompositeProvider)


def test_legacy_model_provider_claude_api_still_returns_claude_api() -> None:
    from agent.providers.claude_api import ClaudeAPIProvider

    built = build_default_provider(Settings(model_provider=CLAUDE_API))
    assert isinstance(built, ClaudeAPIProvider)
    assert not isinstance(built, CompositeProvider)


def test_explicitly_matching_per_role_vars_also_stay_unwrapped() -> None:
    from agent.providers.bedrock import BedrockProvider

    built = build_default_provider(
        Settings(llm_provider=BEDROCK, embedding_provider=BEDROCK)
    )
    assert isinstance(built, BedrockProvider)


def test_per_role_var_overrides_model_provider() -> None:
    from agent.providers.bedrock import BedrockProvider
    from agent.providers.claude_api import ClaudeAPIProvider

    built = build_default_provider(
        Settings(model_provider=BEDROCK, llm_provider=CLAUDE_API)
    )
    assert isinstance(built, CompositeProvider)
    assert isinstance(built.llm, ClaudeAPIProvider)
    assert isinstance(built.embedder, BedrockProvider)  # inherited from MODEL_PROVIDER


# ── unknown names ───────────────────────────────────────────────────────────────────────


def test_unknown_llm_provider_names_the_variable_to_fix() -> None:
    with pytest.raises(ValueError, match="unknown LLM_PROVIDER 'gpt'"):
        build_default_provider(Settings(llm_provider="gpt", embedding_provider=BEDROCK))


def test_unknown_embedding_provider_names_the_variable_to_fix() -> None:
    with pytest.raises(ValueError, match="unknown EMBEDDING_PROVIDER 'voyage'"):
        build_default_provider(Settings(llm_provider=BEDROCK, embedding_provider="voyage"))


def test_unknown_legacy_model_provider_still_names_model_provider() -> None:
    """Backward compatibility extends to the error message, not just the exception type —
    an operator using the legacy variable must be pointed at that variable."""
    with pytest.raises(ValueError, match="unknown MODEL_PROVIDER 'gpt'"):
        build_default_provider(Settings(model_provider="gpt"))


def test_matching_per_role_vars_report_the_per_role_variable() -> None:
    """Both roles agree, but the value came from LLM_PROVIDER — so say LLM_PROVIDER."""
    with pytest.raises(ValueError, match="unknown LLM_PROVIDER 'gpt'"):
        build_default_provider(Settings(llm_provider="gpt", embedding_provider="gpt"))
