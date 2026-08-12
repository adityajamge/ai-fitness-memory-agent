"""The Today snapshot — everything the home screen renders, computed once, deterministically.

**No model call, ever.** Today is the one screen a user sees before they have asked anything,
and a screen that opens by paying for an LLM round trip is both slow and unfalsifiable. Every
number here comes out of a SQL statement this module can hand back verbatim (``steps``), which
is what lets the surface carry the same glass box the conversation does.

Composition, not new intelligence. Each field is an existing engine capability re-read for one
day:

===========================  ==========================================================
field                        source
===========================  ==========================================================
``memories``/``days``        ``glassbox.fetch_stats``
targets                      ``profile.get_profile`` + ``profile.compute_targets``
today / yesterday totals     ``retrieval.aggregate_memories`` (``protein_g``, ``kcal``)
``days_logged_last_7``       ``retrieval.aggregate_memories`` grouped by day
``latest_weight``            ``repository.latest_weight``
``insight``                  ``glassbox.fetch_latest_insight``
``recent``                   ``glassbox.fetch_recent_memories``
===========================  ==========================================================

**The one rule that shapes every type here: a metric with no logged rows is ``None``, never
0.0.** "You have eaten nothing today" and "you ate 0 g of protein today" are different claims,
and only the first one is true at 8 AM. Collapsing them would put a fabricated zero on the
most-seen screen in the product — the exact failure the honesty rules exist to prevent
(DESIGN.md §3 rule 7). ``MetricDay.has_data`` is how a caller tells them apart without
inspecting ``value``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from engine.glassbox import (
    RECENT_MEMORIES,
    fetch_latest_insight,
    fetch_recent_memories,
    fetch_stats,
)
from engine.profile import Profile, compute_targets, get_profile
from engine.repository import latest_weight
from engine.retrieval import AggregateSpec, aggregate_memories
from engine.trace import RetrievalStep

#: The two metrics Today renders as targets. Deliberately two and not the four a meal payload
#: carries: the research's read of the category is that a nutrition product ships four-plus and
#: gates the rest behind a paywall, and that density is right for a food diary and wrong for a
#: memory briefing. Both names are ``retrieval.METRICS`` keys.
PROTEIN = "protein_g"
KCAL = "kcal"

#: How far back ``days_logged_last_7`` looks. Seven because it is the window the user can still
#: remember living through, which is what makes the coverage number legible rather than trivia.
COVERAGE_DAYS = 7


@dataclass(frozen=True, slots=True)
class MetricDay:
    """One metric's total over one local day.

    ``value is None`` means **nothing was logged**, which is not the same as a logged zero and
    must not be rendered as one. ``n`` is the number of contributing rows; ``n_estimated`` how
    many of those carried a model-estimated (rather than user-stated) value, so the UI can say
    "approximately" from data instead of guessing.
    """

    metric: str
    day: date
    value: float | None
    n: int
    n_estimated: int
    evidence_ids: tuple[UUID, ...] = ()

    @property
    def has_data(self) -> bool:
        return self.n > 0


@dataclass(frozen=True, slots=True)
class TodaySnapshot:
    """Everything ``GET /api/today`` serves. Every field is derived; nothing is narrated."""

    day: date
    tz: str
    generated_at: datetime

    # ── account state (fetch_stats) ────────────────────────────────────────────────────
    memories: int
    days: int
    insights: int
    first_event: datetime | None

    # ── targets (profile) ──────────────────────────────────────────────────────────────
    protein_target_g: float | None
    calorie_target_kcal: float | None
    targets_are_custom: bool
    #: Why the targets are what they are — ``compute_targets``' own basis string, or ``None``
    #: when the user set them by hand (in which case there is no computation to explain).
    target_basis: str | None

    # ── the two days Today talks about ─────────────────────────────────────────────────
    today_protein: MetricDay
    today_kcal: MetricDay
    yesterday_protein: MetricDay
    yesterday_kcal: MetricDay

    #: Distinct local days with at least one logged meal in the trailing ``COVERAGE_DAYS``.
    #: This is the honest form of a streak: it is *coverage*, the same quantity that gates
    #: ``analytics.pattern_strength``, and it is presented as a statement about how much the
    #: averages can be trusted rather than as a score to protect.
    days_logged_last_7: int

    latest_weight: dict | None
    insight: dict | None
    recent: list[dict] = field(default_factory=list)

    #: The statements that produced the numbers above, for the glass box. Not decoration:
    #: Today asserts figures the user never asked for, so it owes the same "how this was
    #: retrieved" affordance the conversation gives (ADR-12).
    steps: tuple[RetrievalStep, ...] = ()


def _day_bounds(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """The half-open ``[start, end)`` UTC-aware instants of one local calendar day.

    Built from the *local* midnight rather than by adding 24 h to an instant: across a DST
    transition those differ by an hour, and the day a user means is the one their calendar
    shows.
    """
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return start, end


def _metric_day(
    cur: psycopg.Cursor,
    user_id: UUID,
    metric: str,
    day: date,
    zone: ZoneInfo,
    tz: str,
) -> tuple[MetricDay, RetrievalStep]:
    """Sum one metric over one local day, through the same aggregation path the agent uses.

    Going through ``aggregate_memories`` rather than writing a second SUM here is the point:
    a number Today shows and the number the agent would quote for the same question are then
    the same statement, not two implementations that can disagree.
    """
    start, end = _day_bounds(day, zone)
    spec = AggregateSpec(metric=metric, start=start, end=end, tz=tz, agg="sum")
    result, step = aggregate_memories(cur, user_id, spec)
    if result.is_empty:
        # The defined empty result — no contributing rows at all. `value=None` is what stops
        # this becoming a fabricated zero downstream.
        return MetricDay(metric=metric, day=day, value=None, n=0, n_estimated=0), step
    bucket = result.buckets[0]
    return (
        MetricDay(
            metric=metric,
            day=day,
            value=round(bucket.value, 1),
            n=bucket.n,
            n_estimated=bucket.n_estimated,
            evidence_ids=bucket.evidence_ids,
        ),
        step,
    )


def _coverage(
    cur: psycopg.Cursor, user_id: UUID, day: date, zone: ZoneInfo, tz: str
) -> tuple[int, RetrievalStep]:
    """How many of the trailing ``COVERAGE_DAYS`` local days carry at least one logged meal.

    Counted from the *day-grouped* aggregate rather than a bespoke ``count(DISTINCT ...)``:
    the buckets are days-with-data by construction, so the number cannot drift from the totals
    rendered beside it.
    """
    start, _ = _day_bounds(day - timedelta(days=COVERAGE_DAYS - 1), zone)
    _, end = _day_bounds(day, zone)
    spec = AggregateSpec(
        metric=PROTEIN, start=start, end=end, tz=tz, agg="sum", group_by="day"
    )
    result, step = aggregate_memories(cur, user_id, spec)
    return len(result.buckets), step


def _targets(profile: Profile, weight_row: dict | None, today: date) -> tuple[
    float | None, float | None, str | None
]:
    """The stored targets plus the basis that explains them.

    The stored value always wins — ``api/routers/profile.py`` keeps it current on every edit,
    and recomputing here would let Today and Profile show different numbers for the same
    account. ``compute_targets`` is re-run only to recover the *basis string*, which is not
    persisted, and only when the user has not overridden the targets (an overridden target has
    no computation to explain, and claiming one would be a lie about provenance).
    """
    if profile.targets_are_custom:
        return profile.protein_target_g, profile.calorie_target_kcal, None
    suggestion = compute_targets(
        weight_kg=weight_row["weight_kg"] if weight_row else None,
        height_cm=profile.height_cm,
        date_of_birth=profile.date_of_birth,
        sex=profile.sex,
        activity_level=profile.activity_level,
        primary_goal=profile.primary_goal,
        today=today,
    )
    return (
        profile.protein_target_g,
        profile.calorie_target_kcal,
        suggestion.basis if suggestion else None,
    )


def build_today(
    cur: psycopg.Cursor, user_id: UUID, *, now: datetime, tz: str
) -> TodaySnapshot:
    """Assemble the Today snapshot for one user, in their timezone.

    ``now`` is injected rather than read from the clock so the whole surface is testable at a
    fixed instant — the same posture ``engine/ingestion.py`` takes toward event time.
    """
    zone = ZoneInfo(tz)
    today = now.astimezone(zone).date()
    yesterday = today - timedelta(days=1)

    stats = fetch_stats(cur, user_id)
    profile = get_profile(cur, user_id)
    weight_row = latest_weight(cur, user_id)
    protein_target, calorie_target, basis = _targets(profile, weight_row, today)

    steps: list[RetrievalStep] = []
    today_protein, s1 = _metric_day(cur, user_id, PROTEIN, today, zone, tz)
    today_kcal, s2 = _metric_day(cur, user_id, KCAL, today, zone, tz)
    yesterday_protein, s3 = _metric_day(cur, user_id, PROTEIN, yesterday, zone, tz)
    yesterday_kcal, s4 = _metric_day(cur, user_id, KCAL, yesterday, zone, tz)
    days_logged, s5 = _coverage(cur, user_id, today, zone, tz)
    steps.extend((s1, s2, s3, s4, s5))

    return TodaySnapshot(
        day=today,
        tz=tz,
        generated_at=now,
        memories=stats["memories"],
        days=stats["days"],
        insights=stats["insights"],
        first_event=stats["first_event"],
        protein_target_g=protein_target,
        calorie_target_kcal=calorie_target,
        targets_are_custom=profile.targets_are_custom,
        target_basis=basis,
        today_protein=today_protein,
        today_kcal=today_kcal,
        yesterday_protein=yesterday_protein,
        yesterday_kcal=yesterday_kcal,
        days_logged_last_7=days_logged,
        latest_weight=weight_row,
        insight=fetch_latest_insight(cur, user_id),
        recent=fetch_recent_memories(cur, user_id, RECENT_MEMORIES),
        steps=tuple(steps),
    )
