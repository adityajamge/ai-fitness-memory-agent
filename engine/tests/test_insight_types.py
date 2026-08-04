"""Insight contracts (Phase 5 M1) — the drift canary for tier-2 memory.

Pure: no database, no model, no clock. Everything here is a function of its arguments, so a
failure names a contract rather than an environment.

The load-bearing assertions, in the order they matter:

* **I-9** — ``CONSOLIDATION_SERIES`` is a strict subset of ``METRICS``. Enforced here rather
  than by an import, because ``engine/insights.py`` deliberately does not depend on
  ``engine/retrieval.py`` (see that module's note on the one-way dependency).
* **I-19** — a published ``pattern_strength`` is explained by its three components.
* **§4.6** — the fingerprint is stable under rewording and moves on a value change. Both
  halves matter: the first is what stops every recompute superseding the dataset, the second
  is what stops a changed claim hiding behind an old identity.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from engine.insights import (
    CONSOLIDATION_SERIES,
    FINGERPRINT_PRECISION,
    SeriesKey,
    UnknownSeries,
    fingerprint,
    render_retraction_condition,
    validate_series,
)
from engine.retrieval import METRICS
from engine.types import (
    INSIGHT_KINDS,
    MAX_EVIDENCE_IDS,
    SERIES_KINDS,
    InsightPayload,
    RetractionCondition,
    payload_to_json,
    validate_payload,
)

W_START = datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc)
W_END = datetime(2026, 7, 3, 0, 0, tzinfo=timezone.utc)


def _insight(**overrides) -> dict:
    """A minimal valid insight payload; overrides let each test perturb exactly one thing."""
    base = {
        "kind": "level_shift",
        "hypothesis": "protein intake rose from ~45 to ~83 g/day on 2026-06-23",
        "series_metric": "protein_g",
        "series_kind": "behavioural",
        "window_start": W_START,
        "window_end": W_END,
        "pre_value": 45.0,
        "post_value": 83.0,
        "evidence_ids": [str(uuid4())],
        "evidence_count": 30,
        "effect": 0.5,
        "coverage": 0.8,
        "specificity": 0.5,
        "pattern_strength": 0.5 * 0.8 * 0.5,
        "fingerprint": "abc123",
    }
    base.update(overrides)
    return base


# ── I-9: the series vocabulary is a closed subset of the metric whitelist ──────────────
def test_consolidation_series_is_a_strict_subset_of_metrics():
    """I-9. A consolidatable series the engine cannot actually aggregate would be a metric the
    planner can name and the reader cannot fetch."""
    unknown = set(CONSOLIDATION_SERIES) - set(METRICS)
    assert not unknown, f"not in METRICS: {sorted(unknown)}"
    assert set(CONSOLIDATION_SERIES) < set(METRICS), "must be a STRICT subset, never equal"


def test_narrowing_keeps_a_meal_to_one_consolidatable_series():
    """The budget defence of §4.4/§4.8: a meal payload carries four metrics, but logging one
    must trigger a single series scan."""
    meal_metrics = {m for m, d in METRICS.items() if d.memory_type == "meal"}
    assert len(meal_metrics) == 4  # protein/carbs/fat/kcal — the aggregate vocabulary
    assert meal_metrics & set(CONSOLIDATION_SERIES) == {"protein_g"}


def test_every_series_declares_a_registered_kind_and_detector():
    """I-1 + §4.4: the vocabularies are closed on both axes."""
    for metric, definition in CONSOLIDATION_SERIES.items():
        assert definition.kind in SERIES_KINDS, metric
        assert definition.detector in INSIGHT_KINDS, metric
        assert definition.label and definition.unit, metric


def test_detector_follows_from_how_the_series_is_measured():
    """§4.1: behaviours are logged densely enough to have a level; outcomes are measured
    sparsely and only support a before/after statement."""
    for metric, definition in CONSOLIDATION_SERIES.items():
        expected = "level_shift" if definition.kind == "behavioural" else "intervention_outcome"
        assert definition.detector == expected, metric


def test_the_five_blood_markers_resolve_to_their_jsonb_paths():
    """§4.5: markers live under an open dict, so the curated entries must address it exactly —
    a wrong path silently aggregates nothing rather than failing."""
    for marker in (
        "vitamin_d_ng_ml",
        "vitamin_b12_pg_ml",
        "ferritin_ng_ml",
        "ldl_mg_dl",
        "hba1c_pct",
    ):
        assert METRICS[marker].memory_type == "blood_report"
        assert METRICS[marker].path == ("markers", marker)
        assert marker in CONSOLIDATION_SERIES


# ── SeriesKey ──────────────────────────────────────────────────────────────────────────
def test_series_key_derives_its_kind_from_the_registry():
    key = SeriesKey.for_metric("vitamin_d_ng_ml")
    assert (key.kind, key.metric) == ("outcome", "vitamin_d_ng_ml")
    assert key.detector == "intervention_outcome"


def test_series_key_string_form_is_stable():
    """M3 compares identities through this rendering; a change would orphan every stored
    insight into a duplicate."""
    assert str(SeriesKey.for_metric("protein_g")) == "behavioural:protein_g"


def test_series_key_is_hashable_and_comparable():
    """Half of the (user_id, kind, series_key) identity triple (I-10)."""
    assert SeriesKey.for_metric("protein_g") == SeriesKey.for_metric("protein_g")
    assert len({SeriesKey.for_metric("protein_g"), SeriesKey.for_metric("protein_g")}) == 1


def test_unknown_or_non_consolidatable_metric_is_rejected():
    with pytest.raises(UnknownSeries):
        SeriesKey.for_metric("horoscope_sign")
    # Aggregatable but deliberately NOT consolidatable — the narrowing must be enforced,
    # not merely documented.
    assert "carbs_g" in METRICS
    with pytest.raises(UnknownSeries):
        SeriesKey.for_metric("carbs_g")


def test_contradicting_the_registry_kind_is_rejected():
    with pytest.raises(UnknownSeries):
        SeriesKey(kind="outcome", metric="protein_g")
    with pytest.raises(UnknownSeries):
        validate_series("protein_g", kind="outcome")
    assert validate_series("protein_g", kind="behavioural").metric == "protein_g"


# ── fingerprint (§4.6) ─────────────────────────────────────────────────────────────────
def _fp(**overrides) -> str:
    base = {
        "kind": "level_shift",
        "series_metric": "protein_g",
        "dates": [W_START, W_END],
        "values": [45.0, 83.0],
        "intervention_ids": [],
    }
    base.update(overrides)
    return fingerprint(**base)


def test_fingerprint_is_deterministic():
    assert _fp() == _fp()


def test_fingerprint_ignores_prose():
    """The defining property: rewording a hypothesis must not supersede an unchanged claim —
    the content-keyed-id failure of replay-architecture §4.3, one layer up. Prose is not an
    input at all, which is the strongest form of this guarantee."""
    import inspect

    assert "hypothesis" not in inspect.signature(fingerprint).parameters


def test_fingerprint_changes_on_a_value_change():
    assert _fp(values=[45.0, 83.0]) != _fp(values=[45.0, 84.0])


def test_fingerprint_absorbs_float_noise_below_its_precision():
    """A recomputation that differs only in IEEE noise is the same claim."""
    noise = 10 ** -(FINGERPRINT_PRECISION + 3)
    assert _fp(values=[45.0]) == _fp(values=[45.0 + noise])


def test_fingerprint_normalizes_negative_zero():
    assert _fp(values=[0.0]) == _fp(values=[-0.0])


def test_fingerprint_is_timezone_normalized():
    """The same instant in two zones is one claim, not two."""
    ist = W_START.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert _fp(dates=[ist, W_END]) == _fp(dates=[W_START, W_END])


def test_fingerprint_changes_when_the_claim_moves_in_time():
    assert _fp() != _fp(dates=[W_START, W_END + timedelta(days=1)])


def test_fingerprint_keys_on_claim_dates_not_evidence_extent():
    """The M5 finding: a level shift is identified by its boundary, so accumulating another
    day of evidence at the same level must not mint a new claim (see
    ``analytics.Finding.claim_dates``)."""
    assert _fp(dates=[W_START]) == _fp(dates=[W_START])
    assert _fp(dates=[W_START]) != _fp(dates=[W_START, W_END])


def test_fingerprint_ignores_intervention_order_but_not_membership():
    a, b = str(uuid4()), str(uuid4())
    assert _fp(intervention_ids=[a, b]) == _fp(intervention_ids=[b, a])
    assert _fp(intervention_ids=[a]) != _fp(intervention_ids=[a, b])


def test_fingerprint_rejects_naive_timestamps_and_unknown_kinds():
    with pytest.raises(ValueError, match="timezone-aware"):
        _fp(dates=[W_START.replace(tzinfo=None)])
    with pytest.raises(ValueError, match="unknown insight kind"):
        _fp(kind="vibes")


# ── retraction conditions (§4.14) ──────────────────────────────────────────────────────
def test_condition_round_trips_through_the_payload_registry():
    payload = _insight(
        retraction_condition={
            "metric": "protein_g",
            "direction": "falling",
            "threshold": 45.0,
            "window_days": 30,
            "min_count": 3,
        }
    )
    insight = validate_payload("insight", payload)
    assert insight.retraction_condition is not None
    assert insight.retraction_condition.threshold == 45.0

    dumped = payload_to_json(insight)
    again = validate_payload("insight", dumped)
    assert again.retraction_condition == insight.retraction_condition


def test_threshold_is_optional_so_direction_alone_is_a_valid_condition():
    """ADR-13.11's "comparator/direction" resolved: the comparator exists when there is
    something absolute to compare against."""
    condition = RetractionCondition(
        metric="protein_g", direction="falling", window_days=30, min_count=3
    )
    assert condition.threshold is None


def test_condition_rejects_a_nonsense_direction_or_bounds():
    with pytest.raises(ValidationError):
        RetractionCondition(
            metric="protein_g", direction="sideways", window_days=30, min_count=3
        )
    with pytest.raises(ValidationError):
        RetractionCondition(metric="protein_g", direction="falling", window_days=0, min_count=3)
    with pytest.raises(ValidationError):
        RetractionCondition(metric="protein_g", direction="falling", window_days=30, min_count=0)


def test_prose_is_rendered_from_the_object_and_is_deterministic():
    condition = RetractionCondition(
        metric="protein_g", direction="falling", threshold=45.0, window_days=30, min_count=3
    )
    rendered = render_retraction_condition(condition)
    assert rendered == (
        "I'll withdraw this if protein drops below 45 g/day on 3 or more days "
        "in any 30-day window."
    )
    assert render_retraction_condition(condition) == rendered  # pure


def test_prose_is_never_stored_on_the_condition():
    """ADR-13.11: a stored sentence can drift from its condition; a rendered one cannot."""
    fields = set(RetractionCondition.model_fields)
    assert not fields & {"prose", "text", "description", "rendered"}


def test_prose_covers_every_direction_and_the_thresholdless_form():
    rising = RetractionCondition(
        metric="ldl_mg_dl", direction="rising", threshold=130.0, window_days=90, min_count=2
    )
    assert "LDL cholesterol rises above 130 mg/dL" in render_retraction_condition(rising)

    relative = RetractionCondition(
        metric="protein_g", direction="falling", window_days=14, min_count=1
    )
    assert render_retraction_condition(relative) == (
        "I'll withdraw this if protein falls back below the level this is based on "
        "on any day in a 14-day window."
    )


def test_prose_renders_an_unregistered_metric_instead_of_raising():
    """Display code stays readable; refusing to *write* one is validate_series' job."""
    orphan = RetractionCondition(
        metric="mystery_metric", direction="rising", window_days=7, min_count=1
    )
    assert "mystery_metric" in render_retraction_condition(orphan)


# ── InsightPayload coherence ───────────────────────────────────────────────────────────
def test_insight_is_registered_and_validates():
    insight = validate_payload("insight", _insight())
    assert isinstance(insight, InsightPayload)
    assert insight.kind == "level_shift"


def test_the_claim_values_are_typed_and_required():
    """The retraction evaluator branches on ``post_value`` (§4.14), so it may not be an untyped
    extra that could arrive as a string — and the numbers must live somewhere a deterministic
    reader can reach, since ``hypothesis`` is prose no engine code may parse (I-8)."""
    insight = validate_payload("insight", _insight())
    assert (insight.pre_value, insight.post_value) == (45.0, 83.0)

    missing = _insight()
    del missing["post_value"]
    with pytest.raises(ValidationError):
        validate_payload("insight", missing)

    with pytest.raises(ValidationError):
        validate_payload("insight", _insight(post_value="eighty-three"))


def test_unregistered_kind_is_rejected():
    """I-1: adding a kind is a reviewed act, not a string a caller can invent."""
    with pytest.raises(ValidationError):
        validate_payload("insight", _insight(kind="vibes"))


def test_pattern_strength_must_equal_its_components():
    """I-19. A score its components do not explain is the unfalsifiable number ADR-13.12
    exists to prevent."""
    with pytest.raises(ValidationError, match="does not equal"):
        validate_payload("insight", _insight(pattern_strength=0.95))


def test_strength_components_are_required():
    """I-19: 'published with its components' is a contract, not a convention."""
    payload = _insight()
    del payload["coverage"]
    with pytest.raises(ValidationError):
        validate_payload("insight", payload)


@pytest.mark.parametrize("field", ["effect", "coverage", "specificity", "pattern_strength"])
def test_strength_factors_stay_within_the_unit_interval(field):
    with pytest.raises(ValidationError, match="within"):
        validate_payload("insight", _insight(**{field: 1.5}))


def test_evidence_count_may_not_undercut_the_cited_ids():
    """§4.2: evidence_count is the TRUE total, which is what keeps the lineage cap honest."""
    ids = [str(uuid4()) for _ in range(3)]
    with pytest.raises(ValidationError, match="true total"):
        validate_payload("insight", _insight(evidence_ids=ids, evidence_count=2))
    assert validate_payload("insight", _insight(evidence_ids=ids, evidence_count=3))


def test_lineage_cap_is_enforced():
    ids = [str(uuid4()) for _ in range(MAX_EVIDENCE_IDS + 1)]
    with pytest.raises(ValidationError, match="lineage cap"):
        validate_payload("insight", _insight(evidence_ids=ids, evidence_count=999))


def test_an_insight_must_cite_something():
    with pytest.raises(ValidationError):
        validate_payload("insight", _insight(evidence_ids=[]))


def test_window_must_not_run_backwards():
    with pytest.raises(ValidationError, match="window_end"):
        validate_payload("insight", _insight(window_start=W_END, window_end=W_START))


def test_unknown_keys_still_survive_on_an_insight():
    """The migration-free rule (ADR-13.6) holds for tier 2 as well — M2/M3 add fields without
    a schema change."""
    insight = validate_payload("insight", _insight(interventions=[{"at": "2026-03-28"}]))
    dumped = payload_to_json(insight)
    assert dumped["interventions"] == [{"at": "2026-03-28"}]


def test_insight_round_trips_through_json():
    """The payload is stored as JSONB and read back; datetimes must survive that trip."""
    insight = validate_payload("insight", _insight())
    dumped = payload_to_json(insight)
    assert isinstance(dumped["window_end"], str)
    assert validate_payload("insight", dumped).window_end == insight.window_end
