"""The consolidation service (Phase 5 M3) — series in, durable insights out.

Locked architecture: ``docs/engineering/consolidation-architecture.md`` §4.4 (series and
interventions), §4.6 (identity by fingerprint), §4.7 (derived freshness), §4.8 (the budget and
where this runs), §4.12 (an insight's own metadata).

It reads a user-scoped series, hands it to the pure kernel (``engine/analytics.py``), and
decides what — if anything — to write.

**One implementation, three callers** (M5): ingestion's stage (F₀) hook
(``consolidate_touched``), the ``analyze_series`` tool, and the retroactive
``python -m cli.consolidate`` sweep all enter through this class. The CLI deliberately does not
reimplement any of it — a second copy of the identity rule is a second place for duplicate
insights to be born, and the whole point of I-12 is that there is exactly one such place.

**The rule that matters (§4.6, I-12).** The engine has no write-side deduplication by design
(ADR-15.1/15.3 — correct for live chat), and consolidation runs on *every* ingest touching a
series. Without an identity rule ten logged meals would write ten copies of the same claim, and
the top-bar insight count would become a row count. So an insight is unique per
``(user_id, kind, series_metric)``, and a recomputation that reaches the same claim writes
**nothing at all** — not an update, not a no-op row. When the claim genuinely changes, the
replacement is inserted and the prior row is superseded in one transaction; ADR-9 holds, so
nothing is ever deleted and the chain of what the engine used to think stays readable.

**Transaction discipline.** No model call anywhere in this module — insights are written with
``embedding = NULL`` and picked up by the existing T15 backfill (**I-16**), which keeps a
Bedrock round trip off the ingest path entirely. Reads and writes are separate short
transactions, one insight at a time (**ADR-15.1**: row-at-a-time, never a batch — that is the
C-SPANN footgun the T1 canary guards). The write reuses ``reprocess_note``'s insert+supersede
shape rather than inventing a third (**I-11**).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from engine.analytics import (
    Finding,
    Intervention,
    MetricSample,
    collapse,
    detect_intervention_outcomes,
    detect_level_shifts,
)
from engine.db import Database
from engine.insights import CONSOLIDATION_SERIES, SeriesKey, fingerprint
from engine.memory import Memory
from engine.repository import (
    fetch_behavioural_times,
    fetch_retractable_insights,
    fetch_series,
    fetch_series_window,
    fetch_type_rows,
    find_active_insights,
    insert_memories,
    mark_retracted,
    mark_superseded,
    series_learned_at,
)
from engine.retrieval import METRICS
from engine.types import MAX_EVIDENCE_IDS, payload_to_json, validate_payload

logger = logging.getLogger(__name__)

__all__ = [
    "ConsolidationService",
    "ConsolidationOutcome",
    "SeriesOutcome",
    "RetractionOutcome",
    "count_counterexample_days",
    "DEFAULT_BUDGET_MS",
    "INSIGHT_SOURCE",
    "ONSET_SOURCES",
    "BEHAVIOURAL_TYPES",
]

#: The ~300 ms of [ADR-13.1](../docs/office-hours/09-decisions.md), as amended: provisional
#: until T12 measures the deployed cross-region path. Exceeding it defers the remaining series
#: cleanly; it never fails a turn (§4.8).
DEFAULT_BUDGET_MS = 300

#: ``memories.source`` for a derived row. A distinct value so the glass box — and any later
#: audit — can tell a claim the engine made from a fact the user reported.
INSIGHT_SOURCE = "consolidation"

#: Where structural ``series_onset`` interventions come from (§4.4). The value is the payload
#: key that distinguishes one series within the type, or ``None`` when the type itself is the
#: series ("the first workout ever").
#:
#: Supplements key on the **exact** ``name``: exact equality is engine-legal and needs no
#: language knowledge, which is the whole reason §4.4 defines interventions structurally. It is
#: also where §4.18's deferral of entity canonicalization is felt — two spellings of one
#: supplement would read as two onsets — a bounded, documented limitation.
ONSET_SOURCES: dict[str, str | None] = {"supplement": "name", "workout": None}

#: Types that record what the user *did*, as opposed to what they measured. Only these count
#: toward an ``intervention_outcome``'s coverage (§4.13): the question that factor asks is
#: "was the engine watching during this interval", and a second blood panel is not evidence
#: that anyone was logging their behaviour.
BEHAVIOURAL_TYPES = ("meal", "workout", "sleep", "supplement")


@dataclass(frozen=True, slots=True)
class SeriesOutcome:
    """What consolidating one series did. Exactly one of the first three is populated."""

    series: str
    created: UUID | None = None
    superseded: UUID | None = None
    unchanged: UUID | None = None
    #: Set when the detector cleared nothing (**I-22**) — silence with a reason, not a gap.
    refused: str | None = None
    #: A dry run reached the point of writing and stopped. There is no id because nothing was
    #: inserted — which is precisely what makes the flag trustworthy rather than decorative.
    would_create: bool = False


@dataclass(frozen=True, slots=True)
class RetractionOutcome:
    """The verdict on one insight's retraction condition, whether or not it fired.

    Non-retractions are returned too, with their counts: "2 of the 3 required" is the
    observable that makes a condition auditable instead of a black box, and it is what the
    glass box will eventually render beside the rendered prose."""

    insight_id: UUID
    series: str
    retracted: bool
    counterexample_days: int = 0
    required: int = 0
    #: Set when the condition could not be evaluated at all — never a silent pass.
    skipped: str | None = None


@dataclass(frozen=True, slots=True)
class ConsolidationOutcome:
    """The result of one pass. Deferrals are first-class: a budget overflow is a *result*,
    not an error (§4.8), and the caller reports it rather than retrying."""

    outcomes: list[SeriesOutcome] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)

    @property
    def created_ids(self) -> list[UUID]:
        return [o.created for o in self.outcomes if o.created]

    @property
    def superseded_ids(self) -> list[UUID]:
        return [o.superseded for o in self.outcomes if o.superseded]

    @property
    def would_create_count(self) -> int:
        """Insights a dry run would have written (§4.11)."""
        return sum(1 for o in self.outcomes if o.would_create)

    @property
    def wrote_nothing(self) -> bool:
        return not self.created_ids


class ConsolidationService:
    """Reads series, runs the kernel, and applies the identity rule.

    Dependencies are injected in the same composition-root style as ``IngestionService``, and
    ``clock`` is injectable so the budget is testable without sleeping.
    """

    def __init__(
        self,
        db: Database,
        *,
        default_tz: str,
        budget_ms: int = DEFAULT_BUDGET_MS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.db = db
        self.default_tz = default_tz
        self.budget_ms = budget_ms
        self.clock = clock

    # ── public API ────────────────────────────────────────────────────────────────────
    def consolidate(
        self,
        user_id: UUID,
        series: Sequence[SeriesKey] | None = None,
        *,
        tz: str | None = None,
        budget_ms: int | None = None,
    ) -> ConsolidationOutcome:
        """Consolidate the given series (default: all of them) under a time budget.

        The deadline is checked **between** series, never inside one: a half-evaluated series
        would either write a claim from partial evidence or leave the pass in a state the next
        run cannot distinguish from a fresh one. Stopping on a whole-series boundary keeps
        every outcome complete and the remainder honestly reported as deferred.
        """
        return self._pass(user_id, series, tz=tz, budget_ms=budget_ms, write=True)

    def analyze(
        self,
        user_id: UUID,
        series: Sequence[SeriesKey] | None = None,
        *,
        tz: str | None = None,
        budget_ms: int | None = None,
    ) -> ConsolidationOutcome:
        """What ``consolidate`` *would* do, without writing anything (§4.11's ``--dry-run``).

        The identical read → collapse → detect → identity-compare path, stopped one step before
        the insert. It is deliberately not a separate algorithm with a printing branch: a dry
        run that could disagree with the real run would be worse than no dry run at all, since
        an operator would trust it. ``SeriesOutcome.would_create`` carries the verdict, and
        carries no id, because nothing was written to have one.
        """
        return self._pass(user_id, series, tz=tz, budget_ms=budget_ms, write=False)

    def _pass(
        self,
        user_id: UUID,
        series: Sequence[SeriesKey] | None,
        *,
        tz: str | None,
        budget_ms: int | None,
        write: bool,
    ) -> ConsolidationOutcome:
        tz = tz or self.default_tz
        keys = list(series) if series is not None else [
            SeriesKey.for_metric(metric) for metric in sorted(CONSOLIDATION_SERIES)
        ]
        limit = self.budget_ms if budget_ms is None else budget_ms
        deadline = self.clock() + (limit / 1000.0)

        result = ConsolidationOutcome()
        for key in keys:
            if self.clock() >= deadline:
                result.deferred.append(str(key))
                continue
            result.outcomes.append(self.consolidate_series(user_id, key, tz=tz, write=write))

        if result.deferred:
            logger.info(
                "consolidation budget (%dms) reached for user %s; deferred %s",
                limit, user_id, result.deferred,
            )
        return result

    def consolidate_touched(
        self,
        user_id: UUID,
        memories: Sequence[Memory],
        *,
        tz: str | None = None,
        budget_ms: int | None = None,
    ) -> ConsolidationOutcome:
        """Consolidate only the series the just-committed memories actually touch (§4.4, §4.8).

        This is what stage (F₀) calls. Scoping to touched series is the *primary* structural
        defence of the time budget: a meal payload carries protein, carbs, fat and kcal, but
        only ``protein_g`` is consolidatable, so logging lunch costs one series scan rather
        than nine. A turn that touches nothing consolidatable does no work at all and opens no
        connection.
        """
        keys = series_touched_by(memories)
        if not keys:
            return ConsolidationOutcome()
        return self.consolidate(user_id, keys, tz=tz, budget_ms=budget_ms)

    def consolidate_series(
        self, user_id: UUID, key: SeriesKey, *, tz: str | None = None, write: bool = True
    ) -> SeriesOutcome:
        """Consolidate exactly one series and apply the identity rule to the result.

        ``write=False`` reports the verdict without inserting — the single switch behind
        ``analyze``/``--dry-run``, placed here so both paths share every step above it."""
        tz = tz or self.default_tz
        finding, row_meta = self._detect(user_id, key, tz=tz)
        if finding is None:
            # The detector emitted nothing. An existing insight stays **active**: silence is
            # the absence of a new claim, not evidence against the old one — that is what a
            # retraction condition is for (M4), and conflating the two would let a quiet week
            # withdraw a claim the data still supports.
            return SeriesOutcome(series=str(key), refused="detector emitted nothing")
        return self._apply(user_id, key, finding, row_meta, tz=tz, write=write)

    def is_stale(self, user_id: UUID, key: SeriesKey, insight_created_at: datetime) -> bool:
        """Whether the series has been added to since a claim was derived (§4.7).

        Derived, never stored: no ``last_evaluated_at`` field exists, so there is nothing to
        keep in sync and no way for the stored value and the truth to disagree.
        """
        metric = METRICS[key.metric]
        with self.db.transaction() as cur:
            learned_at = series_learned_at(cur, user_id, metric.memory_type, metric.path)
        return learned_at is not None and learned_at > insight_created_at

    # ── retraction (§4.14, T5) ────────────────────────────────────────────────────────
    def evaluate_retractions(
        self, user_id: UUID, *, now: datetime | None = None, tz: str | None = None
    ) -> list[RetractionOutcome]:
        """Judge every active insight against the condition it agreed to be judged by (§4.14).

        Rides the same (F₀) pass as consolidation in M5; here it is a standalone entry point.
        ``now`` is injected rather than read inside, so the whole evaluation is a function of
        its inputs — a trailing window silently anchored to a hidden clock is not something a
        test can pin, and ADR-13.11's entire premise is that this is deterministic.

        **No model, no language, no prose** (**I-21**): the decision is a comparison of typed
        floats against a typed condition. The insight's own ``hypothesis`` is never read, and
        neither is any note.
        """
        now = now or datetime.now(timezone.utc)
        tz = tz or self.default_tz
        zone = ZoneInfo(tz)

        with self.db.transaction() as cur:
            candidates = fetch_retractable_insights(cur, user_id)

        results: list[RetractionOutcome] = []
        for row in candidates:
            outcome = self._judge(user_id, row, now=now, zone=zone)
            results.append(outcome)
            if outcome.retracted:
                with self.db.transaction() as cur:
                    mark_retracted(cur, user_id, row["id"])
                logger.info(
                    "retracted insight %s: %d counterexample day(s) >= %d",
                    row["id"], outcome.counterexample_days, outcome.required,
                )
        return results

    def _judge(
        self, user_id: UUID, row: dict, *, now: datetime, zone: ZoneInfo
    ) -> RetractionOutcome:
        payload = row["payload"]
        condition = payload.get("retraction_condition") or {}
        series = payload.get("series_metric", "?")

        metric = METRICS.get(condition.get("metric"))
        if metric is None:
            return _skip(row, series, "condition names a metric the engine cannot read")

        reference, missing = _reference(payload, condition)
        if missing:
            return _skip(row, series, missing)

        window_days = int(condition["window_days"])
        required = int(condition["min_count"])
        start = now - timedelta(days=window_days)

        with self.db.transaction() as cur:
            rows = fetch_series_window(
                cur, user_id, metric.memory_type, metric.path, start, now
            )

        days = count_counterexample_days(
            rows, reference=reference, direction=condition["direction"], zone=zone
        )
        return RetractionOutcome(
            insight_id=row["id"],
            series=series,
            retracted=days >= required,
            counterexample_days=days,
            required=required,
        )

    # ── detection ─────────────────────────────────────────────────────────────────────
    def _detect(
        self, user_id: UUID, key: SeriesKey, *, tz: str
    ) -> tuple[Finding | None, dict[UUID, dict]]:
        """Read the series, run the matching detector, return at most one finding.

        Only one finding is kept per series because identity is ``(user, kind, series)``: a
        series that produced three level shifts holds **one** active claim, and the most recent
        boundary is the one that describes where the user is now. The earlier shifts are not
        lost — they remain derivable, and each is still available as an *intervention* for the
        outcome detector.
        """
        metric = METRICS[key.metric]
        with self.db.transaction() as cur:
            rows = fetch_series(cur, user_id, metric.memory_type, metric.path)
        row_meta = {row["id"]: row for row in rows}
        observations = collapse(_samples(rows), tz=tz)

        if key.detector == "level_shift":
            findings = detect_level_shifts(
                observations, metric=key.metric, tz=tz, scale=key.scale
            )
        else:
            interventions, intervention_meta = self._interventions(user_id, tz=tz)
            row_meta.update(intervention_meta)
            findings = detect_intervention_outcomes(
                observations,
                interventions,
                metric=key.metric,
                tz=tz,
                scale=key.scale,
                behavioural_dates=self._behavioural_dates(user_id, tz=tz),
            )
        return (findings[-1] if findings else None), row_meta

    def _interventions(
        self, user_id: UUID, *, tz: str
    ) -> tuple[list[Intervention], dict[UUID, dict]]:
        """Every structurally detected change in the account (§4.4) — onsets and level shifts.

        Structural means exactly that: a first occurrence, or an arithmetic result. Nothing
        here reads a note, a summary, or any other prose (**I-8**). The account's notes read
        remarkably like an intervention log, which is precisely why this function may not
        consult them.
        """
        interventions: list[Intervention] = []
        meta: dict[UUID, dict] = {}

        for memory_type, name_key in ONSET_SOURCES.items():
            with self.db.transaction() as cur:
                rows = fetch_type_rows(cur, user_id, memory_type)
            seen: set[str | None] = set()
            for row in rows:
                name = row[name_key] if name_key else None
                if name in seen:
                    continue
                seen.add(name)
                meta[row["id"]] = row
                slug = _slug(name) if name else memory_type
                interventions.append(
                    Intervention(
                        ident=f"series_onset:{memory_type}:{slug}",
                        at=row["event_time"],
                        kind="series_onset",
                        label=name or memory_type,
                        memory_ids=(row["id"],),
                        n_memories=1,
                    )
                )

        for metric, definition in sorted(CONSOLIDATION_SERIES.items()):
            if definition.detector != "level_shift":
                continue
            behavioural = SeriesKey.for_metric(metric)
            spec = METRICS[metric]
            with self.db.transaction() as cur:
                rows = fetch_series(cur, user_id, spec.memory_type, spec.path)
            meta.update({row["id"]: row for row in rows})
            for shift in detect_level_shifts(
                collapse(_samples(rows), tz=tz), metric=metric, tz=tz, scale=behavioural.scale
            ):
                interventions.append(Intervention.from_level_shift(shift))

        return interventions, meta

    def _behavioural_dates(self, user_id: UUID, *, tz: str) -> set:
        zone = ZoneInfo(tz)
        with self.db.transaction() as cur:
            times = fetch_behavioural_times(cur, user_id, list(BEHAVIOURAL_TYPES))
        return {moment.astimezone(zone).date() for moment in times}

    # ── the identity rule (§4.6) ──────────────────────────────────────────────────────
    def _apply(
        self,
        user_id: UUID,
        key: SeriesKey,
        finding: Finding,
        row_meta: dict[UUID, dict],
        *,
        tz: str,
        write: bool = True,
    ) -> SeriesOutcome:
        claim = fingerprint(
            kind=finding.kind,
            series_metric=finding.series_metric,
            dates=finding.claim_dates,
            values=finding.values,
            intervention_ids=finding.intervention_ids,
        )

        with self.db.transaction() as cur:
            active = find_active_insights(cur, user_id, finding.kind, finding.series_metric)

        if len(active) > 1:
            # I-10 says at most one. Surfacing a surplus rather than quietly picking the newest
            # is the point: a duplicate that nothing complains about is how the count drifts.
            logger.warning(
                "user %s has %d active insights for %s/%s; superseding all but the newest",
                user_id, len(active), finding.kind, finding.series_metric,
            )

        if active and active[0]["payload"].get("fingerprint") == claim and len(active) == 1:
            return SeriesOutcome(series=str(key), unchanged=active[0]["id"])

        # Build unconditionally: a dry run that skipped validation could report a claim the
        # real run would then reject, which is the one way this flag could mislead.
        memory = self._build(user_id, key, finding, claim, row_meta, tz=tz)
        superseded = [row["id"] for row in active]

        if not write:
            return SeriesOutcome(series=str(key), would_create=True)

        with self.db.transaction() as cur:
            (new_id,) = insert_memories(cur, [memory])
            for old_id in superseded:
                mark_superseded(cur, user_id, old_id, superseded_by=new_id)

        return SeriesOutcome(
            series=str(key), created=new_id, superseded=superseded[0] if superseded else None
        )

    def _build(
        self,
        user_id: UUID,
        key: SeriesKey,
        finding: Finding,
        claim: str,
        row_meta: dict[UUID, dict],
        *,
        tz: str,
    ) -> Memory:
        """Turn a finding into the row that will be written.

        ``event_time`` is the window's end and ``created_at`` is left to the database — an
        insight derived today about May is *about* May and was *learned* now, and back-dating
        it would be the replay clock ADR-13.10 rejected.

        ``confidence`` and ``provenance`` are inherited from the evidence the claim cites: an
        insight is exactly as trustworthy as the least trustworthy row under it, which is what
        keeps the glass box's confidence column meaning one thing across both memory tiers.
        """
        cited = [row_meta[i] for i in finding.evidence_ids if i in row_meta]
        confidence = min((row["confidence"] for row in cited), default=1.0)
        provenance = (
            "reconstructed"
            if any(row["provenance"] == "reconstructed" for row in cited)
            else "live"
        )

        payload = {
            "kind": finding.kind,
            "hypothesis": _hypothesis(finding, key),
            "series_metric": finding.series_metric,
            "series_kind": key.kind,
            "window_start": finding.window_start,
            "window_end": finding.window_end,
            "pre_value": finding.pre_value,
            "post_value": finding.post_value,
            "evidence_ids": [str(i) for i in finding.evidence_ids[:MAX_EVIDENCE_IDS]],
            "evidence_count": finding.evidence_count,
            "effect": finding.effect,
            "coverage": finding.coverage,
            "specificity": finding.specificity,
            "pattern_strength": finding.pattern_strength,
            "fingerprint": claim,
        }
        if finding.intervention_ids:
            payload["intervention_ids"] = list(finding.intervention_ids)
        if finding.assertions:
            payload["assertions"] = list(finding.assertions)

        validated = validate_payload("insight", payload)
        return Memory(
            user_id=user_id,
            event_time=finding.window_end,
            tz=tz,
            type="insight",
            source=INSIGHT_SOURCE,
            provenance=provenance,
            confidence=confidence,
            summary=payload["hypothesis"],
            payload=payload_to_json(validated),
            embedding=None,  # I-16 — the existing T15 backfill embeds it, off the hot path
        )


# ── helpers ────────────────────────────────────────────────────────────────────────────
def series_touched_by(memories: Sequence[Memory]) -> list[SeriesKey]:
    """Which consolidatable series a set of just-written memories affects (§4.4).

    Pure, and deliberately narrow: a memory touches a series only when its type matches and
    the metric's JSONB path actually resolves to a value. A meal logged without macros touches
    nothing, so it triggers nothing — the alternative, scanning every series on every ingest,
    is what the budget cannot afford.

    Order is deterministic (metric name) so a turn's consolidation is reproducible.
    """
    touched: set[str] = set()
    for memory in memories:
        for metric in CONSOLIDATION_SERIES:
            spec = METRICS[metric]
            if spec.memory_type != memory.type:
                continue
            if _payload_value(memory.payload, spec.path) is not None:
                touched.add(metric)
    return [SeriesKey.for_metric(metric) for metric in sorted(touched)]


def _payload_value(payload: dict, path: tuple[str, ...]) -> object | None:
    """Walk a metric's JSONB path in Python — the same path the SQL builders bind."""
    node: object = payload
    for segment in path:
        if not isinstance(node, dict):
            return None
        node = node.get(segment)
        if node is None:
            return None
    return node


def _samples(rows: Sequence[dict]) -> list[MetricSample]:
    """Repository rows → the kernel's prose-free input type (I-8)."""
    return [
        MetricSample(
            memory_id=row["id"],
            event_time=row["event_time"],
            value=float(row["value"]),
            composition=row["composition"],
            assertion=row["assertion"],
        )
        for row in rows
    ]


def count_counterexample_days(
    rows: Sequence[dict], *, reference: float, direction: str, zone: ZoneInfo
) -> int:
    """Distinct local days in the window on which the metric contradicted a claim (§4.14).

    A counterexample is a value that moved in ``direction`` past ``reference`` — below it for
    ``falling``, above it for ``rising``. ``reference`` is the condition's ``threshold`` when it
    has one and the insight's ``post_value`` otherwise; both arrive as typed floats, so this is
    arithmetic and nothing else (**I-21**).

    **Days, not rows.** Two meals on one day are one counterexample, which is what makes the
    count mean the same thing as the sentence the user was shown ("on 3 or more days in any
    30-day window") — ADR-13.11 requires the displayed rule and the evaluated rule to be the
    same rule. It also reads correctly for a materialized period: an assertion covering thirty
    days really does assert about each of them, which is the one place this deliberately counts
    days where the *detectors* count assertions (I-4). The distinction is intentional: I-4
    protects effect size from over-weighted evidence, while a retraction is asking how many
    days reality disagreed.

    Pure: no clock (the window is already applied by the caller's query), no I/O, no prose.
    """
    contradicts = (
        (lambda value: value < reference)
        if direction == "falling"
        else (lambda value: value > reference)
    )
    return len({
        row["event_time"].astimezone(zone).date()
        for row in rows
        if row["value"] is not None and contradicts(float(row["value"]))
    })


def _reference(payload: dict, condition: dict) -> tuple[float, str | None]:
    """The value a counterexample is measured against (§4.14).

    An explicit ``threshold`` wins; otherwise the claim's own ``post_value`` — the level it
    asserted the series had reached. A missing ``post_value`` is reported as unevaluatable
    rather than defaulted: guessing a reference would let the engine retract a claim on
    arithmetic the user never agreed to."""
    threshold = condition.get("threshold")
    if threshold is not None:
        return float(threshold), None
    post = payload.get("post_value")
    if post is None:
        return 0.0, "insight predates post_value; a direction-only condition needs it"
    return float(post), None


def _skip(row: dict, series: str, reason: str) -> RetractionOutcome:
    logger.info("retraction check skipped for insight %s: %s", row["id"], reason)
    return RetractionOutcome(
        insight_id=row["id"], series=series, retracted=False, skipped=reason
    )


def _slug(name: str) -> str:
    """A stable identifier fragment for a supplement name.

    Lowercase and non-alphanumerics collapsed — spelling hygiene only, never a meaning
    judgment, the same narrow posture as ``normalize_item`` (replay-architecture §4.13). It
    feeds an ``Intervention.ident``, which enters a claim's fingerprint, so it must be stable
    across runs.
    """
    return "".join(ch if ch.isalnum() else "_" for ch in name.strip().lower()).strip("_")


def _hypothesis(finding: Finding, key: SeriesKey) -> str:
    """The claim in words, rendered **from** the finding (never stored as the claim itself).

    Deliberately flat prose with no causal verb (**I-2**): a level "rose", a marker "changed
    while" things happened. The narrator may say it more naturally later; this is what the
    engine itself is willing to assert.
    """
    label, unit = key.definition.label, key.definition.unit
    when = finding.boundary.date().isoformat()
    if finding.kind == "level_shift":
        direction = "rose" if finding.post_value > finding.pre_value else "fell"
        return (
            f"{label} {direction} from ~{finding.pre_value:g} to ~{finding.post_value:g} "
            f"{unit} starting {when}"
        )
    n = len(finding.intervention_ids)
    changes = "1 logged change" if n == 1 else f"{n} logged changes"
    start = finding.window_start.date().isoformat()
    return (
        f"{label} went from {finding.pre_value:g} to {finding.post_value:g} {unit} "
        f"between {start} and {when}, with {changes} recorded in that window"
    )


def _utcnow() -> datetime:  # pragma: no cover — seam for future callers
    return datetime.now(timezone.utc)
