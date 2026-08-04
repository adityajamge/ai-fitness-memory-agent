"""The analytics kernel (Phase 5 M2) — observation collapse and the two detectors.

Locked architecture: ``docs/engineering/consolidation-architecture.md`` §4.1 (why these two
detectors and not changepoint detection), §4.2 (collapse), §4.13 (the strength factors),
§4.15 (the thresholds that make the engine refuse to speak).

**Pure (I-7).** No database, no model, no network, no filesystem, no clock, no global state.
Every function is a function of its arguments, which is what lets the two claims this phase
actually makes be pinned against the real series in a fixture rather than against a cluster.
Two honest boundaries on "pure": ``zoneinfo`` reads the IANA database (deterministic given a
tzdata version — the same boundary ``engine/retrieval.py`` already draws, and the same shape
as replay-architecture's Unicode-version caveat), and refusals are emitted to a ``logging``
debug channel. Neither is I/O in the sense I-7 protects: nothing is read that could differ
between two runs on one machine, and nothing outside the process is mutated.

**The engine never interprets language (I-8).** ``MetricSample`` and ``Intervention`` carry no
summary, no note text, and no free prose the arithmetic reads. ``assertion`` and ``label`` ride
along for the glass box and are never touched by a computation — asserted directly in the test
suite, because the reconstruction's notes read so much like structured data that mining them is
the single most tempting boundary violation available in this phase.

**Nothing here is causal (I-2).** A ``Finding`` is a labeled heuristic: a level moved, or a
marker changed while these structurally-detected things happened. ``specificity`` is the factor
that says so out loud — it falls as the number of competing explanations rises, so a claim with
seven overlapping interventions scores itself down instead of asserting one of them.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from engine.types import MAX_EVIDENCE_IDS, EffectScale

logger = logging.getLogger(__name__)

__all__ = [
    "MetricSample",
    "Observation",
    "Intervention",
    "Finding",
    "collapse",
    "detect_level_shifts",
    "detect_intervention_outcomes",
    "pattern_strength",
    "EffectScale",
    "MIN_SPAN_DAYS",
    "MIN_INTERVAL_DAYS",
    "MIN_MEASUREMENTS",
    "MIN_INTERVENTIONS",
    "CONCURRENCY_DAYS",
    "MIN_INTERVENTION_GAP_DAYS",
]

# ── thresholds (§4.15) — the engine refuses to speak below these (I-22) ────────────────
# Constants, not per-user or adaptive: a threshold nobody can predict is a threshold nobody
# can audit, and "why did it say that" has to be answerable from this file alone.

# There is deliberately no global effect floor. How big a change has to be is a property of
# the *series*, declared as an ``EffectScale`` in ``engine/insights.py`` and passed in: relative
# change is the wrong yardstick for a physiologically bounded quantity, and no single constant
# serves both a marker that moves 5x and a body-fat percentage that never will (§4.13).

#: Days each compared side of a level shift must span. A one-day observation has no *level*,
#: which is why day-to-day noise in live logging can never produce a shift.
MIN_SPAN_DAYS = 7
#: Days two measurements must be apart before a before/after statement means anything.
MIN_INTERVAL_DAYS = 14
#: An outcome series needs at least this many measurements to be compared at all.
MIN_MEASUREMENTS = 2
#: With nothing structurally detectable inside the interval there is no hypothesis to state.
MIN_INTERVENTIONS = 1

#: Two level shifts closer together than this are "the same moment" for specificity purposes.
CONCURRENCY_DAYS = 3
#: Interventions closer together than this merge into one competing explanation — a diet change
#: and a supplement started the same week are one decision, and counting them twice would
#: understate attribution twice over.
MIN_INTERVENTION_GAP_DAYS = 3

#: Value equality when collapsing. Absorbs float noise without merging genuinely different
#: levels — the values compared here come from one payload table, so they are normally
#: bit-identical anyway.
_VALUE_PRECISION = 6


# ── inputs ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class MetricSample:
    """One memory row carrying a value for a series — what M3 will read and hand over.

    Deliberately **payload-free and prose-free** (I-8): the id, the instant, the number, and
    the two ``expanded_from`` fields the collapse needs. There is no summary and no note text
    here, so no detector can read one even by accident.
    """

    memory_id: UUID
    event_time: datetime
    value: float
    #: ``expanded_from.composition`` — the parent assertion's key. ``None`` for an observed
    #: point event, which is exactly what distinguishes the two in §4.2.
    composition: str | None = None
    #: ``expanded_from.assertion`` — the human sentence the period came from. Carried for the
    #: glass box, **never** read by arithmetic.
    assertion: str | None = None


@dataclass(frozen=True, slots=True)
class Observation:
    """What the analytics believes it observed (§4.2, **I-4**).

    One asserted fact is *one* observation, however many days the converter materialized for
    it. Treating 30 identical expanded rows as 30 independent observations would read
    ADR-15.4's ``expanded_from`` honesty signal and then throw it away — and would let a
    detector rediscover the converter's own segment boundaries at maximal effect size.
    """

    value: float
    start: datetime
    end: datetime
    #: Boundary-anchored lineage (§4.2): the first and last contributing row, or the single row
    #: for a point event. ``n_materialized`` is the true total.
    memory_ids: tuple[UUID, ...]
    n_materialized: int
    #: Distinct local days on which a contributing memory exists. Feeds ``coverage`` — and is
    #: why a sparse live-logged stretch scores below a densely covered one.
    covered_days: int
    assertion: str | None = None

    def span_days(self, tz: ZoneInfo) -> int:
        """Inclusive local-day span. A point event spans 1 day, never 0."""
        return (_local_date(self.end, tz) - _local_date(self.start, tz)).days + 1


@dataclass(frozen=True, slots=True)
class Intervention:
    """A structurally detected change inside an interval (§4.4) — a series onset, or a level
    shift the other detector already found.

    Never derived from prose (**I-8**). ``label`` is display text and is never read by the
    arithmetic; ``ident`` is what enters a claim's fingerprint, so it must be stable across
    runs — derive it from coordinates (kind, series, date), never from wording.
    """

    ident: str
    at: datetime
    kind: str
    label: str = ""
    memory_ids: tuple[UUID, ...] = ()
    n_memories: int = 0

    @classmethod
    def from_level_shift(cls, finding: Finding) -> Intervention:
        """A level shift *is* an intervention for the outcome detector (§4.4). The bridge lives
        here because both detectors are this module's, and the conversion is pure."""
        return cls(
            ident=f"level_shift:{finding.series_metric}:{finding.boundary.date().isoformat()}",
            at=finding.boundary,
            kind="level_shift",
            label=f"{finding.series_metric} {finding.pre_value:g} → {finding.post_value:g}",
            memory_ids=finding.evidence_ids,
            n_memories=finding.evidence_count,
        )


@dataclass(frozen=True, slots=True)
class Finding:
    """One candidate claim, with the arithmetic that justifies it already attached.

    Carries its three factors **and** their product (**I-19**): a score whose components are
    not published alongside it is an assertion, not a heuristic. M3 turns a Finding into an
    ``InsightPayload``, whose validator re-checks the product — so a detector that ever
    disagreed with itself could not be persisted.
    """

    kind: str
    series_metric: str
    window_start: datetime
    window_end: datetime
    #: When the change is dated: the first day of the new level, or the later measurement.
    boundary: datetime
    pre_value: float
    post_value: float
    effect: float
    coverage: float
    specificity: float
    pattern_strength: float
    evidence_ids: tuple[UUID, ...]
    evidence_count: int
    intervention_ids: tuple[str, ...] = ()
    assertions: tuple[str, ...] = ()

    @property
    def values(self) -> tuple[float, float]:
        """The value pair a fingerprint is taken over (§4.6)."""
        return (self.pre_value, self.post_value)

    @property
    def claim_dates(self) -> tuple[datetime, ...]:
        """The dates that identify the *claim*, as opposed to the extent of its evidence (§4.6).

        For a ``level_shift`` that is the boundary alone: the claim is "moved from A to B
        starting here", and ``window_start``/``window_end`` merely bound the observations that
        support it — they grow every time another day at the same level is logged, which would
        otherwise make an unchanged claim supersede itself daily.

        For an ``intervention_outcome`` the window *is* the claim ("went from A to B between
        these two measurements"), and it moves only when a genuinely new measurement arrives.
        """
        if self.kind == "level_shift":
            return (self.boundary,)
        return (self.window_start, self.window_end)


# ── helpers ────────────────────────────────────────────────────────────────────────────
def _zone(tz: str) -> ZoneInfo:
    return tz if isinstance(tz, ZoneInfo) else ZoneInfo(tz)


def _local_date(moment: datetime, tz: ZoneInfo) -> date:
    if moment.tzinfo is None:
        raise ValueError("analytics requires timezone-aware timestamps")
    return moment.astimezone(tz).date()


def _refuse(detector: str, metric: str, reason: str) -> None:
    """Record why nothing was emitted (**I-22**). Silence with a reason is a *result*; silence
    without one is indistinguishable from a bug."""
    logger.debug("%s refused for %s: %s", detector, metric, reason)


def pattern_strength(effect: float, coverage: float, specificity: float) -> float:
    """``effect × coverage × specificity`` (§4.13) — one formula, three published factors.

    A product rather than a weighted sum on purpose: a sum lets a strong factor mask a weak
    one, while a product makes "one of these is bad" visible in the score itself. The result is
    a labeled heuristic and **never** a probability or a confidence (**I-2**, ADR-13.12).
    """
    for name, value in (("effect", effect), ("coverage", coverage), ("specificity", specificity)):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be within [0, 1], got {value}")
    return effect * coverage * specificity


def _effect(pre: float, post: float, scale: EffectScale) -> float:
    """The share of a full-size change this move represents (§4.13).

    Against the series' own ``full_delta`` rather than the baseline, so the number means the
    same thing in every series: a 3.2-point body-fat drop and a 32 ng/mL vitamin D rise are
    both real, and a relative denominator would score the first at 0.08 and the second at 1.0.
    """
    return scale.effect(post - pre)


# ── collapse (§4.2) ────────────────────────────────────────────────────────────────────
def collapse(samples: Sequence[MetricSample], *, tz: str) -> list[Observation]:
    """Turn memory rows into what was actually *observed* (**I-4**).

    Contiguous samples sharing an ``expanded_from.composition`` **and** the same value become
    one observation. Both halves of that test are load-bearing: a single composition can carry
    more than one level (the real dataset's June diet phase changes partway through via
    ``segments``), so grouping by composition alone would merge a 45 g/day stretch with an
    83 g/day one and erase the very change worth reporting.

    Samples without a composition are observed point events and collapse with nothing.

    **Gaps stay missing (I-6).** No day is invented between samples, and no value is carried
    forward or interpolated across one — a day with no logged meal is a day the engine has
    nothing to say about, which is the whole reason ``coverage`` exists as a factor.
    """
    zone = _zone(tz)
    ordered = sorted(samples, key=lambda s: (s.event_time, str(s.memory_id)))

    observations: list[Observation] = []
    group: list[MetricSample] = []

    def flush() -> None:
        if not group:
            return
        days = {_local_date(s.event_time, zone) for s in group}
        first, last = group[0], group[-1]
        ids = (first.memory_id,) if first is last else (first.memory_id, last.memory_id)
        observations.append(
            Observation(
                value=group[0].value,
                start=first.event_time,
                end=last.event_time,
                memory_ids=ids,
                n_materialized=len(group),
                covered_days=len(days),
                assertion=group[0].assertion,
            )
        )
        group.clear()

    for sample in ordered:
        if group and _continues(group[-1], sample):
            group.append(sample)
            continue
        flush()
        group.append(sample)
    flush()

    return observations


def _continues(previous: MetricSample, current: MetricSample) -> bool:
    if previous.composition is None or current.composition is None:
        return False
    if previous.composition != current.composition:
        return False
    return round(previous.value, _VALUE_PRECISION) == round(current.value, _VALUE_PRECISION)


# ── detector 1: level shift (§4.1) ─────────────────────────────────────────────────────
def detect_level_shifts(
    observations: Sequence[Observation],
    *,
    metric: str,
    tz: str,
    scale: EffectScale,
    min_span_days: int = MIN_SPAN_DAYS,
) -> list[Finding]:
    """Where a metric's level moved between adjacent observations, and stayed moved.

    ``scale`` is required, not defaulted: how big a change has to be is a property of the
    series, and a detector that guessed it would be inventing the one judgment this argument
    exists to make explicit.

    Each compared side must span ``min_span_days`` (**I-22**). That is what keeps this honest
    on live data: a single logged day has no level, so day-to-day variation can never produce a
    claim, and the detector simply says nothing until a stretch is long enough to have one.

    ``specificity`` is scoped to *this* series: it asks whether the shift is attributable to
    one moment, counting other shifts found in the same run within ``CONCURRENCY_DAYS``.
    Attribution *across* series is the other detector's question, and is where the score
    correctly collapses when several things changed at once.
    """
    zone = _zone(tz)
    if len(observations) < 2:
        _refuse("level_shift", metric, f"{len(observations)} observation(s); need 2")
        return []

    ordered = sorted(observations, key=lambda o: o.start)
    candidates: list[tuple[Observation, Observation, float]] = []
    for pre, post in zip(ordered, ordered[1:], strict=False):
        pre_span, post_span = pre.span_days(zone), post.span_days(zone)
        if pre_span < min_span_days or post_span < min_span_days:
            _refuse(
                "level_shift",
                metric,
                f"span {pre_span}/{post_span}d at {post.start.date()} below {min_span_days}d",
            )
            continue
        delta = post.value - pre.value
        if not scale.gates(delta):
            _refuse(
                "level_shift",
                metric,
                f"change {abs(delta):g} at {post.start.date()} below the "
                f"{scale.min_delta:g} noise floor",
            )
            continue
        candidates.append((pre, post, _effect(pre.value, post.value, scale)))

    boundaries = [post.start for _, post, _ in candidates]
    findings: list[Finding] = []
    for pre, post, effect in candidates:
        pre_span, post_span = pre.span_days(zone), post.span_days(zone)
        coverage = (pre.covered_days + post.covered_days) / (pre_span + post_span)
        coverage = min(1.0, coverage)
        specificity = _specificity_from_concurrency(post.start, boundaries, zone)
        findings.append(
            Finding(
                kind="level_shift",
                series_metric=metric,
                window_start=pre.start,
                window_end=post.end,
                boundary=post.start,
                pre_value=pre.value,
                post_value=post.value,
                effect=effect,
                coverage=coverage,
                specificity=specificity,
                pattern_strength=pattern_strength(effect, coverage, specificity),
                evidence_ids=_cap(pre.memory_ids + post.memory_ids),
                evidence_count=pre.n_materialized + post.n_materialized,
                assertions=tuple(a for a in (pre.assertion, post.assertion) if a),
            )
        )
    return findings


def _specificity_from_concurrency(
    boundary: datetime, boundaries: Sequence[datetime], zone: ZoneInfo
) -> float:
    """``1 / (1 + others)`` — 1.0 when a shift stands alone, halved when one other change sits
    within ``CONCURRENCY_DAYS``, and so on."""
    day = _local_date(boundary, zone)
    others = sum(
        1
        for other in boundaries
        if other != boundary and abs((_local_date(other, zone) - day).days) <= CONCURRENCY_DAYS
    )
    return 1.0 / (1 + others)


# ── detector 2: intervention outcome (§4.1) ────────────────────────────────────────────
def detect_intervention_outcomes(
    measurements: Sequence[Observation],
    interventions: Sequence[Intervention],
    *,
    metric: str,
    tz: str,
    scale: EffectScale,
    behavioural_dates: Collection[date] = (),
    min_interval_days: int = MIN_INTERVAL_DAYS,
    min_measurements: int = MIN_MEASUREMENTS,
    min_interventions: int = MIN_INTERVENTIONS,
) -> list[Finding]:
    """A sparsely measured marker changed, and these structurally detected things happened
    in between.

    **The compared pair is the two most recent measurements**, not first-to-last. The
    architecture says a claim arises when a marker "gains a second (or later) measurement"
    (§4.1); comparing each new measurement against its predecessor keeps the interval — and so
    the set of interventions inside it — local and defensible. First-to-last would sweep every
    intervention the account has ever seen into one claim and drive specificity toward zero as
    a user's history grows, which is the wrong answer getting more wrong over time.

    ``scale`` is required for the same reason as on the other detector: a marker's noise
    floor is a property of how it is measured, and body composition and a blood marker do not
    share one.

    ``coverage`` asks whether the engine was actually watching during the interval: the
    fraction of its days on which any behavioural memory exists. A marker that moved while the
    user logged nothing is a weaker claim than one that moved while they logged daily, and this
    is the factor that says so.

    Returns at most one finding. Emits nothing — with a recorded reason (**I-22**) — when there
    are too few measurements, too short an interval, too small a change, or nothing structurally
    detectable inside it. That last refusal is the important one: without an intervention there
    is no hypothesis, only a number that moved.
    """
    zone = _zone(tz)
    if len(measurements) < min_measurements:
        _refuse("intervention_outcome", metric, f"{len(measurements)} measurement(s)")
        return []

    ordered = sorted(measurements, key=lambda o: o.start)
    earlier, later = ordered[-2], ordered[-1]

    interval_days = (_local_date(later.start, zone) - _local_date(earlier.end, zone)).days
    if interval_days < min_interval_days:
        _refuse(
            "intervention_outcome", metric, f"interval {interval_days}d below {min_interval_days}d"
        )
        return []

    delta = later.value - earlier.value
    if not scale.gates(delta):
        _refuse(
            "intervention_outcome",
            metric,
            f"change {abs(delta):g} below the {scale.min_delta:g} noise floor",
        )
        return []
    effect = _effect(earlier.value, later.value, scale)

    inside = [i for i in interventions if earlier.end < i.at <= later.start]
    merged = _merge_interventions(inside, zone)
    if len(merged) < min_interventions:
        _refuse("intervention_outcome", metric, "no intervention inside the interval")
        return []

    coverage = _interval_coverage(earlier.end, later.start, behavioural_dates, zone)
    specificity = 1.0 / len(merged)

    intervention_ids = tuple(sorted(i.ident for i in inside))
    evidence = later.memory_ids + earlier.memory_ids
    for group in merged:
        for i in group:
            evidence += i.memory_ids

    return [
        Finding(
            kind="intervention_outcome",
            series_metric=metric,
            window_start=earlier.start,
            window_end=later.end,
            boundary=later.start,
            pre_value=earlier.value,
            post_value=later.value,
            effect=effect,
            coverage=coverage,
            specificity=specificity,
            pattern_strength=pattern_strength(effect, coverage, specificity),
            evidence_ids=_cap(evidence),
            evidence_count=(
                earlier.n_materialized
                + later.n_materialized
                + sum(i.n_memories for i in inside)
            ),
            intervention_ids=intervention_ids,
            assertions=tuple(a for a in (earlier.assertion, later.assertion) if a),
        )
    ]


def _merge_interventions(
    interventions: Sequence[Intervention], zone: ZoneInfo
) -> list[list[Intervention]]:
    """Cluster interventions closer together than ``MIN_INTERVENTION_GAP_DAYS``.

    A diet change and a supplement started the same week are one decision. Counting them as two
    competing explanations would halve specificity for what is really a single act — punishing a
    claim for the granularity of its own logging."""
    if not interventions:
        return []
    ordered = sorted(interventions, key=lambda i: i.at)
    clusters: list[list[Intervention]] = [[ordered[0]]]
    for intervention in ordered[1:]:
        previous = clusters[-1][-1]
        gap = (_local_date(intervention.at, zone) - _local_date(previous.at, zone)).days
        if gap < MIN_INTERVENTION_GAP_DAYS:
            clusters[-1].append(intervention)
        else:
            clusters.append([intervention])
    return clusters


def _interval_coverage(
    start: datetime, end: datetime, behavioural_dates: Collection[date], zone: ZoneInfo
) -> float:
    """Fraction of the interval's days on which any behavioural memory exists (§4.13).

    Zero known behavioural days yields 0.0, which drives ``pattern_strength`` to 0 — honest:
    a marker that moved while the engine was watching nothing supports no hypothesis about why.
    """
    first, last = _local_date(start, zone), _local_date(end, zone)
    total = (last - first).days
    if total <= 0:
        return 0.0
    covered = sum(1 for day in set(behavioural_dates) if first < day <= last)
    return min(1.0, covered / total)


def _cap(ids: Sequence[UUID]) -> tuple[UUID, ...]:
    """Deduplicate, preserve order, and honor the lineage cap (§4.2, **I-5**). The true total
    always travels separately as ``evidence_count``."""
    seen: list[UUID] = []
    for memory_id in ids:
        if memory_id not in seen:
            seen.append(memory_id)
    return tuple(seen[:MAX_EVIDENCE_IDS])
