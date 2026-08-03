"""The analytics kernel (Phase 5 M2) — collapse, both detectors, the strength arithmetic.

Pure: no database, no model, no clock. A failure here names a contract, never an environment.

The tests that carry the phase, in the order they matter:

* **the real series** — `collapse` must reduce the account's 97 materialized protein rows to
  **four** observations, and the detector must find the three shifts at their real boundaries
  with numerically pinned components. This is what makes the kernel a test of the product
  rather than of a curve someone drew to pass (§9).
* **I-4** — 30 expanded rows are one observation. Without it a detector rediscovers the
  converter's own segment boundaries at maximal effect size.
* **I-8** — no prose reaches a detector. Asserted structurally (the input types carry no text
  field the arithmetic reads) *and* behaviourally (perturbing every text field changes nothing).
* **I-22** — a detector that cannot clear its thresholds emits nothing, with a reason. Silence
  is a tested outcome, not an absence of one.
* **I-6** — gaps stay missing. Nothing is interpolated across a hole in the data.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from engine.analytics import (
    CONCURRENCY_DAYS,
    MIN_INTERVAL_DAYS,
    MIN_SPAN_DAYS,
    Finding,
    Intervention,
    MetricSample,
    Observation,
    collapse,
    detect_intervention_outcomes,
    detect_level_shifts,
    pattern_strength,
)
from engine.insights import CONSOLIDATION_SERIES
from engine.tests.fixtures import (
    TZ,
    protein_days,
    protein_series,
    vitamin_d_interventions,
    vitamin_d_measurements,
)
from engine.types import MAX_EVIDENCE_IDS, EffectScale

IST = ZoneInfo(TZ)
UTC = timezone.utc

# The real calibration, not a test-local invention: detectors are exercised with the scales
# the product actually ships (I-24).
PROTEIN = CONSOLIDATION_SERIES["protein_g"].scale
VITD = CONSOLIDATION_SERIES["vitamin_d_ng_ml"].scale
BODYFAT = CONSOLIDATION_SERIES["body_fat_pct"].scale


def _sample(day: str, value: float, *, composition=None, assertion=None, mid=None) -> MetricSample:
    return MetricSample(
        memory_id=mid or uuid4(),
        event_time=datetime.fromisoformat(f"{day}T12:00:00").replace(tzinfo=UTC),
        value=value,
        composition=composition,
        assertion=assertion,
    )


def _run(day: str, n: int, value: float, *, composition: str, assertion: str | None = None):
    start = datetime.fromisoformat(day).date()
    return [
        _sample(
            (start + timedelta(days=i)).isoformat(),
            value,
            composition=composition,
            assertion=assertion,
        )
        for i in range(n)
    ]


def _obs(
    day: str, span: int, value: float, *, covered: int | None = None, n: int = 1
) -> Observation:
    start = datetime.fromisoformat(f"{day}T12:00:00").replace(tzinfo=UTC)
    end = start + timedelta(days=span - 1)
    return Observation(
        value=value,
        start=start,
        end=end,
        memory_ids=(uuid4(),),
        n_materialized=n,
        covered_days=span if covered is None else covered,
    )


# ══ the real series (§3.2, §9) ═════════════════════════════════════════════════════════
def test_the_real_protein_series_collapses_to_four_observations():
    """I-4. 97 materialized rows, three payload-table entries, four levels — one asserted fact
    is one observation, however many days the converter wrote for it."""
    samples = protein_series()
    assert len(samples) == 97

    observations = collapse(samples, tz=TZ)

    assert [o.value for o in observations] == [31.0, 36.0, 45.0, 83.0]
    assert [o.n_materialized for o in observations] == [30, 51, 8, 8]
    assert [o.start.date().isoformat() for o in observations] == [
        "2026-03-26",
        "2026-04-25",
        "2026-06-15",
        "2026-06-23",
    ]


def test_one_composition_carrying_two_levels_is_two_observations():
    """The June diet phase changes partway through via `segments`. Grouping by composition
    alone would merge 45 with 83 and erase the largest real change in the account."""
    observations = collapse(protein_series(), tz=TZ)
    june = [o for o in observations if o.start.date().isoformat().startswith("2026-06")]
    assert [o.value for o in june] == [45.0, 83.0]


def test_the_real_series_yields_its_three_level_shifts_with_pinned_components():
    """The numeric contract. If §4.13's arithmetic changes, this fails before a walkthrough
    does."""
    findings = detect_level_shifts(
        collapse(protein_series(), tz=TZ), metric="protein_g", tz=TZ, scale=PROTEIN
    )

    assert [(f.pre_value, f.post_value) for f in findings] == [
        (31.0, 36.0),
        (36.0, 45.0),
        (45.0, 83.0),
    ]
    assert [f.boundary.date().isoformat() for f in findings] == [
        "2026-04-25",
        "2026-06-15",
        "2026-06-23",
    ]
    # Against protein's 30 g/day full-size step: 5 -> 0.167, 9 -> 0.300, and the 38 g/day
    # jump exceeds a whole dietary-strategy change, so it caps at 1.0.
    assert [round(f.effect, 3) for f in findings] == [0.167, 0.3, 1.0]
    # Every day of every compared window carries a row (the expansion materialized them), so
    # coverage is 1.0 — the honesty signal for expanded data is confidence + expanded_from
    # (ADR-15.4), deliberately not a second discount here.
    assert [f.coverage for f in findings] == [1.0, 1.0, 1.0]
    # The three boundaries are >3 days apart, so each shift stands alone in its own series.
    assert [f.specificity for f in findings] == [1.0, 1.0, 1.0]
    assert [round(f.pattern_strength, 3) for f in findings] == [0.167, 0.3, 1.0]


def test_the_real_vitamin_d_pair_yields_one_intervention_outcome():
    """The money question, as arithmetic: a 6.2 -> 38.4 move over 100 days with four distinct
    clusters of change inside it."""
    measurements = collapse(vitamin_d_measurements(), tz=TZ)
    findings = detect_intervention_outcomes(
        measurements,
        vitamin_d_interventions(),
        metric="vitamin_d_ng_ml",
        tz=TZ,
        scale=VITD,
        behavioural_dates=protein_days(),
    )

    assert len(findings) == 1
    finding = findings[0]
    assert (finding.pre_value, finding.post_value) == (6.2, 38.4)
    assert finding.effect == 1.0  # a >5x move, capped
    assert round(finding.coverage, 3) == 0.97  # 97 logged days of the 100-day interval
    # Eight interventions merge into four clusters (two pairs land on adjacent days), so the
    # engine claims one-in-four attribution rather than asserting a cause.
    assert len(finding.intervention_ids) == 8
    assert finding.specificity == 0.25
    assert round(finding.pattern_strength, 3) == 0.242


def test_the_real_vitamin_d_claim_stays_a_hypothesis():
    """I-2. The score must never read as confidence: a five-fold move with perfect coverage
    still lands below 0.25 because four things could explain it."""
    findings = detect_intervention_outcomes(
        collapse(vitamin_d_measurements(), tz=TZ),
        vitamin_d_interventions(),
        metric="vitamin_d_ng_ml",
        tz=TZ,
        scale=VITD,
        behavioural_dates=protein_days(),
    )
    assert findings[0].effect == 1.0
    assert findings[0].pattern_strength < 0.25


def _body_fat_beat(post: float) -> list[Finding]:
    """The live demo beat's shape (§4.1): the historical body scan plus one logged on camera,
    with exactly one change in between."""
    return detect_intervention_outcomes(
        collapse([_sample("2026-06-20", 39.2), _sample("2026-08-03", post)], tz=TZ),
        [Intervention(ident="level_shift:protein_g:2026-06-23",
                      at=datetime(2026, 6, 23, tzinfo=UTC), kind="level_shift")],
        metric="body_fat_pct",
        tz=TZ,
        scale=BODYFAT,
        behavioural_dates={datetime(2026, 6, 20).date() + timedelta(days=i) for i in range(44)},
    )


def test_a_single_clean_intervention_scores_high():
    """One change inside the interval means the claim can name it — specificity 1.0, honestly
    earned rather than assumed."""
    findings = _body_fat_beat(32.0)
    assert len(findings) == 1
    assert findings[0].specificity == 1.0
    assert findings[0].pattern_strength > 0.05


def test_a_real_body_fat_move_produces_an_insight_while_scale_noise_does_not():
    """The regression this scale exists for. Under the old single *relative* floor a 3.2-point
    body-fat drop scored 0.082 and was refused outright, while nothing stopped a 0.8-point BIA
    wobble from qualifying on a series that happened to sit near zero.

    Against body fat's own scale — 1.5 points of noise, 8 points of full-size change — a real
    six-week drop scores 0.40 and a hydration swing is silent."""
    real = _body_fat_beat(36.0)  # 3.2 points
    assert len(real) == 1
    assert round(real[0].effect, 3) == 0.4

    assert _body_fat_beat(38.4) == []  # 0.8 points — inside BIA's own variability


# ══ collapse (§4.2) ════════════════════════════════════════════════════════════════════
def test_point_events_never_collapse():
    """No composition means an observed event, and observed events stand alone."""
    observations = collapse([_sample("2026-05-01", 70.0), _sample("2026-05-02", 70.0)], tz=TZ)
    assert len(observations) == 2


def test_same_value_in_different_compositions_stays_separate():
    samples = _run("2026-05-01", 8, 40.0, composition="a")
    samples += _run("2026-05-09", 8, 40.0, composition="b")
    assert len(collapse(samples, tz=TZ)) == 2


def test_collapse_records_the_true_total_and_boundary_ids():
    """I-5: the lineage is anchored, the count is complete."""
    samples = _run("2026-05-01", 30, 31.0, composition="c")
    (observation,) = collapse(samples, tz=TZ)
    assert observation.n_materialized == 30
    assert observation.memory_ids == (samples[0].memory_id, samples[-1].memory_id)


def test_collapse_is_order_independent():
    samples = _run("2026-05-01", 10, 31.0, composition="c")
    assert collapse(samples, tz=TZ) == collapse(list(reversed(samples)), tz=TZ)


def test_collapse_leaves_gaps_missing():
    """I-6. A 20-day hole is not filled, averaged, or carried forward — the span widens and
    `covered_days` stays at what was actually logged, which is what makes coverage meaningful."""
    samples = _run("2026-05-01", 5, 31.0, composition="c") + _run(
        "2026-05-25", 5, 31.0, composition="c"
    )
    (observation,) = collapse(samples, tz=TZ)
    assert observation.n_materialized == 10
    assert observation.covered_days == 10  # NOT the 29-day span
    assert observation.span_days(IST) == 29


def test_collapse_requires_aware_timestamps():
    naive = MetricSample(memory_id=uuid4(), event_time=datetime(2026, 5, 1), value=1.0)
    with pytest.raises(ValueError, match="timezone-aware"):
        collapse([naive], tz=TZ)


def test_collapse_of_nothing_is_nothing():
    assert collapse([], tz=TZ) == []


# ══ level shift (§4.1) ═════════════════════════════════════════════════════════════════
def test_a_series_without_a_shift_yields_nothing():
    """The negative half of the contract — a flat series must produce silence."""
    observations = [_obs("2026-05-01", 30, 50.0), _obs("2026-06-01", 30, 50.5)]
    assert detect_level_shifts(observations, metric="protein_g", tz=TZ, scale=PROTEIN) == []


def test_a_change_below_the_noise_floor_is_refused_with_a_reason(caplog):
    """I-22. Silence with a recorded reason is a result; silence without one is a bug — and the
    reason is now stated in the metric's own units, which is what makes it explainable."""
    observations = [_obs("2026-05-01", 30, 100.0), _obs("2026-06-01", 30, 103.0)]  # 3 g/day
    with caplog.at_level(logging.DEBUG, logger="engine.analytics"):
        assert detect_level_shifts(observations, metric="protein_g", tz=TZ, scale=PROTEIN) == []
    assert "noise floor" in caplog.text


def test_short_spans_cannot_produce_a_level_shift(caplog):
    """A single logged day has no *level*, so day-to-day noise in live logging can never
    become a claim — the reason live data routes to the other detector."""
    observations = [_obs("2026-05-01", 1, 40.0), _obs("2026-05-02", 1, 90.0)]
    with caplog.at_level(logging.DEBUG, logger="engine.analytics"):
        assert detect_level_shifts(observations, metric="protein_g", tz=TZ, scale=PROTEIN) == []
    assert "span" in caplog.text


def test_span_threshold_is_applied_to_both_sides():
    long_side = _obs("2026-05-01", 30, 40.0)
    short_side = _obs("2026-06-01", MIN_SPAN_DAYS - 1, 90.0)
    assert (
        detect_level_shifts([long_side, short_side], metric="protein_g", tz=TZ, scale=PROTEIN)
        == []
    )
    ok = _obs("2026-06-01", MIN_SPAN_DAYS, 90.0)
    assert len(detect_level_shifts([long_side, ok], metric="protein_g", tz=TZ, scale=PROTEIN)) == 1


def test_a_single_observation_yields_nothing(caplog):
    with caplog.at_level(logging.DEBUG, logger="engine.analytics"):
        assert (
            detect_level_shifts(
                [_obs("2026-05-01", 30, 40.0)], metric="protein_g", tz=TZ, scale=PROTEIN
            )
            == []
        )
    assert "need 2" in caplog.text


def test_concurrent_shifts_reduce_each_other_specificity():
    """Two changes within CONCURRENCY_DAYS are one moment, and neither can claim it alone."""
    observations = [
        _obs("2026-05-01", 10, 40.0),
        _obs("2026-05-11", 10, 60.0),
        _obs("2026-05-21", 10, 90.0),
    ]
    findings = detect_level_shifts(
        observations, metric="protein_g", tz=TZ, scale=PROTEIN, min_span_days=CONCURRENCY_DAYS
    )
    assert [f.specificity for f in findings] == [1.0, 1.0]  # 10 days apart: independent

    tight = [
        _obs("2026-05-01", 10, 40.0), _obs("2026-05-11", 3, 60.0), _obs("2026-05-14", 10, 90.0)
    ]
    findings = detect_level_shifts(tight, metric="protein_g", tz=TZ, scale=PROTEIN, min_span_days=3)
    assert [f.specificity for f in findings] == [0.5, 0.5]


def test_sparse_coverage_lowers_the_score():
    """A stretch logged half the time supports half the claim."""
    dense = detect_level_shifts(
        [_obs("2026-05-01", 30, 40.0), _obs("2026-06-01", 30, 90.0)],
        metric="protein_g", tz=TZ, scale=PROTEIN,
    )[0]
    sparse = detect_level_shifts(
        [_obs("2026-05-01", 30, 40.0, covered=15), _obs("2026-06-01", 30, 90.0, covered=15)],
        metric="protein_g",
        tz=TZ,
        scale=PROTEIN,
    )[0]
    assert dense.coverage == 1.0
    assert sparse.coverage == 0.5
    assert sparse.pattern_strength < dense.pattern_strength


def test_a_drop_is_as_detectable_as_a_rise():
    findings = detect_level_shifts(
        [_obs("2026-05-01", 30, 90.0), _obs("2026-06-01", 30, 40.0)],
        metric="protein_g", tz=TZ, scale=PROTEIN,
    )
    assert len(findings) == 1
    assert findings[0].post_value < findings[0].pre_value


# ══ intervention outcome (§4.1) ════════════════════════════════════════════════════════
def _measurements(*pairs: tuple[str, float]) -> list[Observation]:
    return collapse([_sample(day, value) for day, value in pairs], tz=TZ)


def _intervention(day: str, ident: str = "i1") -> Intervention:
    return Intervention(
        ident=ident, at=datetime.fromisoformat(f"{day}T12:00:00").replace(tzinfo=UTC),
        kind="series_onset",
    )


def test_one_measurement_yields_nothing(caplog):
    with caplog.at_level(logging.DEBUG, logger="engine.analytics"):
        assert detect_intervention_outcomes(
            _measurements(("2026-05-01", 10.0)), [_intervention("2026-05-10")],
            metric="vitamin_d_ng_ml", tz=TZ, scale=VITD,
        ) == []
    assert "measurement" in caplog.text


def test_a_too_short_interval_yields_nothing(caplog):
    """A two-point series below MIN_INTERVAL_DAYS is not a before/after, it is two readings."""
    with caplog.at_level(logging.DEBUG, logger="engine.analytics"):
        assert detect_intervention_outcomes(
            _measurements(("2026-05-01", 10.0), ("2026-05-05", 30.0)),
            [_intervention("2026-05-02")],
            metric="vitamin_d_ng_ml", tz=TZ, scale=VITD,
        ) == []
    assert "interval" in caplog.text


def test_no_intervention_inside_the_interval_yields_nothing(caplog):
    """The important refusal: without a structurally detected change there is no hypothesis,
    only a number that moved."""
    with caplog.at_level(logging.DEBUG, logger="engine.analytics"):
        assert detect_intervention_outcomes(
            _measurements(("2026-03-25", 6.2), ("2026-07-03", 38.4)),
            [_intervention("2026-01-01")],  # outside
            metric="vitamin_d_ng_ml", tz=TZ, scale=VITD, behavioural_dates=protein_days(),
        ) == []
    assert "no intervention" in caplog.text


def test_the_compared_pair_is_the_two_most_recent_measurements():
    """§4.1 as implemented: each new measurement is compared with its predecessor, so a growing
    history does not sweep every intervention it ever saw into one claim."""
    findings = detect_intervention_outcomes(
        _measurements(("2026-01-01", 5.0), ("2026-03-25", 6.2), ("2026-07-03", 38.4)),
        [_intervention("2026-03-28")],
        metric="vitamin_d_ng_ml", tz=TZ, scale=VITD, behavioural_dates=protein_days(),
    )
    assert (findings[0].pre_value, findings[0].post_value) == (6.2, 38.4)


def test_interventions_on_adjacent_days_merge_into_one_explanation():
    """Two things logged a day apart are one decision; counting them twice would understate
    attribution twice over."""
    measurements = _measurements(("2026-03-25", 6.2), ("2026-07-03", 38.4))
    apart = [_intervention("2026-04-01", "a"), _intervention("2026-05-01", "b")]
    together = [_intervention("2026-04-01", "a"), _intervention("2026-04-02", "b")]

    assert detect_intervention_outcomes(measurements, apart, metric="vitamin_d_ng_ml",
                                        tz=TZ, scale=VITD,
                                        behavioural_dates=protein_days())[0].specificity == 0.5
    assert detect_intervention_outcomes(measurements, together, metric="vitamin_d_ng_ml",
                                        tz=TZ, scale=VITD,
                                        behavioural_dates=protein_days())[0].specificity == 1.0


def test_zero_behavioural_coverage_drives_the_score_to_zero():
    """A marker that moved while the engine was watching nothing supports no hypothesis."""
    findings = detect_intervention_outcomes(
        _measurements(("2026-03-25", 6.2), ("2026-07-03", 38.4)),
        [_intervention("2026-04-01")],
        metric="vitamin_d_ng_ml", tz=TZ, scale=VITD, behavioural_dates=set(),
    )
    assert findings[0].coverage == 0.0
    assert findings[0].pattern_strength == 0.0


def test_interventions_outside_the_interval_are_excluded():
    measurements = _measurements(("2026-03-25", 6.2), ("2026-07-03", 38.4))
    findings = detect_intervention_outcomes(
        measurements,
        [_intervention("2026-03-01", "before"), _intervention("2026-04-01", "inside"),
         _intervention("2026-08-01", "after")],
        metric="vitamin_d_ng_ml", tz=TZ, scale=VITD, behavioural_dates=protein_days(),
    )
    assert findings[0].intervention_ids == ("inside",)


def test_level_shift_findings_convert_into_interventions():
    """§4.4's bridge: a detected shift is a structural change the outcome detector can cite."""
    shift = detect_level_shifts(
        collapse(protein_series(), tz=TZ), metric="protein_g", tz=TZ, scale=PROTEIN
    )[-1]
    intervention = Intervention.from_level_shift(shift)
    assert intervention.kind == "level_shift"
    assert intervention.ident == "level_shift:protein_g:2026-06-23"
    assert intervention.at == shift.boundary


# ══ strength arithmetic (§4.13, I-19) ══════════════════════════════════════════════════
def test_pattern_strength_is_the_product_of_its_factors():
    assert pattern_strength(0.5, 0.8, 0.5) == pytest.approx(0.2)


def test_a_product_makes_one_bad_factor_visible():
    """Why not a weighted sum: a sum lets a strong factor mask a weak one."""
    assert pattern_strength(1.0, 1.0, 0.05) == pytest.approx(0.05)


def test_pattern_strength_rejects_out_of_range_factors():
    with pytest.raises(ValueError, match="within"):
        pattern_strength(1.5, 1.0, 1.0)


def test_every_finding_publishes_components_that_explain_its_score():
    """I-19, across everything the kernel can emit — the same identity M1's payload validator
    re-checks before anything is persisted."""
    findings: list[Finding] = detect_level_shifts(
        collapse(protein_series(), tz=TZ), metric="protein_g", tz=TZ, scale=PROTEIN
    )
    findings += detect_intervention_outcomes(
        collapse(vitamin_d_measurements(), tz=TZ), vitamin_d_interventions(),
        metric="vitamin_d_ng_ml", tz=TZ, scale=VITD, behavioural_dates=protein_days(),
    )
    assert findings
    for f in findings:
        assert f.pattern_strength == pytest.approx(f.effect * f.coverage * f.specificity)
        assert 0.0 <= f.pattern_strength <= 1.0


def test_effect_is_capped_and_survives_a_zero_baseline():
    findings = detect_level_shifts(
        [_obs("2026-05-01", 30, 0.0), _obs("2026-06-01", 30, 50.0)],
        metric="protein_g", tz=TZ, scale=PROTEIN,
    )
    assert findings[0].effect == 1.0


def test_lineage_is_capped_while_the_count_stays_true():
    """I-5. The cap bounds what is rendered; evidence_count is what keeps it honest."""
    interventions = [
        Intervention(ident=f"i{n}", at=datetime(2026, 4, 1, tzinfo=UTC) + timedelta(days=n * 3),
                     kind="series_onset", memory_ids=(uuid4(),), n_memories=1)
        for n in range(40)
    ]
    findings = detect_intervention_outcomes(
        _measurements(("2026-03-25", 6.2), ("2026-07-03", 38.4)), interventions,
        metric="vitamin_d_ng_ml", tz=TZ, scale=VITD, behavioural_dates=protein_days(),
    )
    assert len(findings[0].evidence_ids) == MAX_EVIDENCE_IDS
    assert findings[0].evidence_count > MAX_EVIDENCE_IDS


# ══ purity and the language boundary ═══════════════════════════════════════════════════
def test_detector_inputs_carry_no_prose_field():
    """I-8, structurally: there is no summary or note text on the way in, so no detector can
    read one even by accident."""
    for model in (MetricSample, Intervention):
        names = {f.name for f in dataclasses.fields(model)}
        assert not names & {"summary", "text", "note", "hypothesis", "payload"}


def test_perturbing_every_text_field_changes_no_number():
    """I-8, behaviourally. The reconstruction's notes read like structured data; this asserts
    the arithmetic cannot be influenced by any of it."""
    samples = protein_series()
    reworded = [
        dataclasses.replace(s, assertion="TOTALLY DIFFERENT PROSE " * 3) for s in samples
    ]
    baseline = detect_level_shifts(
        collapse(samples, tz=TZ), metric="protein_g", tz=TZ, scale=PROTEIN
    )
    perturbed = detect_level_shifts(
        collapse(reworded, tz=TZ), metric="protein_g", tz=TZ, scale=PROTEIN
    )

    def numeric(f):
        return (f.pre_value, f.post_value, f.effect, f.coverage, f.specificity,
                f.pattern_strength, f.boundary)

    assert [numeric(f) for f in baseline] == [numeric(f) for f in perturbed]

    labelled = [dataclasses.replace(i, label="started vitamin D because of the blood report")
                for i in vitamin_d_interventions()]
    measurements = collapse(vitamin_d_measurements(), tz=TZ)
    a = detect_intervention_outcomes(measurements, vitamin_d_interventions(),
                                     metric="vitamin_d_ng_ml", tz=TZ, scale=VITD,
                                     behavioural_dates=protein_days())
    b = detect_intervention_outcomes(measurements, labelled, metric="vitamin_d_ng_ml",
                                     tz=TZ, scale=VITD,
                                     behavioural_dates=protein_days())
    assert numeric(a[0]) == numeric(b[0])


def test_detectors_are_deterministic():
    """I-7: same inputs, same findings, every time."""
    observations = collapse(protein_series(), tz=TZ)
    first = detect_level_shifts(observations, metric="protein_g", tz=TZ, scale=PROTEIN)
    second = detect_level_shifts(observations, metric="protein_g", tz=TZ, scale=PROTEIN)
    assert first == second


def test_detectors_do_not_mutate_their_inputs():
    samples = protein_series()
    snapshot = list(samples)
    observations = collapse(samples, tz=TZ)
    detect_level_shifts(observations, metric="protein_g", tz=TZ, scale=PROTEIN)
    assert samples == snapshot


def test_bucketing_uses_the_supplied_timezone():
    """ADR-14.10: the zone is engine-injected, never a model's choice — and it decides which
    local day a late-evening event belongs to."""
    late = MetricSample(
        memory_id=UUID(int=1),
        event_time=datetime(2026, 5, 1, 20, 0, tzinfo=UTC),  # 01:30 next day in IST
        value=1.0,
    )
    (utc_obs,) = collapse([late], tz="UTC")
    (ist_obs,) = collapse([late], tz=TZ)
    assert utc_obs.covered_days == ist_obs.covered_days == 1
    assert late.event_time.astimezone(IST).date() != late.event_time.astimezone(UTC).date()


def test_thresholds_are_module_constants_not_magic():
    """A threshold nobody can look up is a threshold nobody can audit."""
    assert (MIN_SPAN_DAYS, MIN_INTERVAL_DAYS, CONCURRENCY_DAYS) == (7, 14, 3)


def test_there_is_no_global_effect_floor_left():
    """I-24. How big a change has to be is a property of the series, not of the module — the
    whole point of the scale. A reintroduced global constant would silently re-break body
    composition."""
    import engine.analytics as analytics

    assert not hasattr(analytics, "MIN_EFFECT")
    assert not hasattr(analytics, "EFFECT_EPSILON")


def test_every_series_ships_a_usable_scale():
    """I-24: a series with no declared scale cannot be consolidated at all."""
    for metric, definition in CONSOLIDATION_SERIES.items():
        assert isinstance(definition.scale, EffectScale), metric
        assert 0 < definition.scale.min_delta <= definition.scale.full_delta, metric


def test_a_scale_cannot_be_incoherent():
    with pytest.raises(ValueError, match="positive"):
        EffectScale(min_delta=0.0, full_delta=10.0)
    with pytest.raises(ValueError, match="below min_delta"):
        EffectScale(min_delta=10.0, full_delta=1.0)


def test_effect_is_comparable_across_series():
    """The property a relative denominator could not give: a real change in a bounded metric
    and a real change in one that moves multiples both score as real."""
    body_fat = BODYFAT.effect(-3.2)
    vitamin_d = VITD.effect(32.2)
    assert body_fat > 0.15  # would have been 0.082 under a relative denominator
    assert vitamin_d == 1.0
