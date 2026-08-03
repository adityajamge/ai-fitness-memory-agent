"""Sanitized real-data fixtures for the analytics kernel (Phase 5 M2, §9).

**Why real data.** Detectors validated only against synthetic curves prove the detector, not
the product — the same argument that made Phase 4 replay the *real* history rather than a
persona (ADR-4). These two series are the ones the demo rests on, so the kernel is pinned
against them directly: if a change to §4.13's arithmetic would break the money question, a test
here fails rather than a walkthrough.

**What is and is not in here (ADR-7).** Only the two series the repo's own committed documents
already publish: the protein levels of the four diet phases and the Vitamin D pair recorded in
[ADR-15](../../docs/office-hours/09-decisions.md) and
[OQ5](../../docs/office-hours/10-open-questions.md). No clinic names, no identifiers, no other
blood markers, no report text. The raw reconstruction and the payload table stay local and
gitignored; nothing here discloses anything the repository does not already state in prose.

The shapes matter more than the numbers, and both are the *reason* the architecture looks the
way it does:

* ``protein_series`` — 97 materialized daily rows carrying **four** distinct values, produced
  by three reviewed payload-table entries (one of which changes partway through via
  ``segments``). This is the series §4.1 refused to run a changepoint detector on, and it is
  what ``collapse`` must reduce to four observations.
* ``vitamin_d_measurements`` — a two-point outcome series 100 days apart. Two points is all a
  blood marker ever gives you, which is why §4.1 built a detector that needs exactly two.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from engine.analytics import Intervention, MetricSample

TZ = "Asia/Kolkata"
_IST = timezone(timedelta(hours=5, minutes=30))

#: Deterministic ids, so a failing assertion names a row rather than a random UUID.
_NAMESPACE = "00000000-0000-4000-8000-{:012d}"


def _mid(n: int) -> UUID:
    return UUID(_NAMESPACE.format(n))


def _at(day: str, hour: int = 8) -> datetime:
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00").replace(tzinfo=_IST)


def _days(start: str, end: str) -> list[str]:
    """Inclusive ISO date range."""
    first = datetime.fromisoformat(start).date()
    last = datetime.fromisoformat(end).date()
    return [
        (first + timedelta(days=n)).isoformat() for n in range((last - first).days + 1)
    ]


# ── the protein series (§3.2) ──────────────────────────────────────────────────────────
#: (composition key, assertion text, first day, last day, g/day). Three compositions, four
#: levels — the June phase changes partway through, which is exactly why ``collapse`` keys on
#: composition **and** value rather than composition alone.
PROTEIN_PHASES: tuple[tuple[str, str, str, str, float], ...] = (
    (
        "meal-pattern.2026-03-26.2026-04-24",
        "4 eggs + 200g dahi daily",
        "2026-03-26",
        "2026-04-24",
        31.0,
    ),
    (
        "meal-pattern.2026-04-25.2026-06-14",
        "6 eggs + 200g dahi daily",
        "2026-04-25",
        "2026-06-14",
        36.0,
    ),
    (
        "meal-pattern.2026-06-15.2026-07-05",
        "3 eggs + 100g paneer + 250g dahi daily",
        "2026-06-15",
        "2026-06-22",
        45.0,
    ),
    (
        "meal-pattern.2026-06-15.2026-07-05",
        "3 eggs + 100g paneer + 250g dahi + 100-150g chicken daily",
        "2026-06-23",
        "2026-06-30",
        83.0,
    ),
)


def protein_series() -> list[MetricSample]:
    """97 materialized daily samples across four levels — the account as replay committed it."""
    samples: list[MetricSample] = []
    counter = 0
    for composition, assertion, first, last, value in PROTEIN_PHASES:
        for day in _days(first, last):
            counter += 1
            samples.append(
                MetricSample(
                    memory_id=_mid(counter),
                    event_time=_at(day, hour=13),
                    value=value,
                    composition=composition,
                    assertion=assertion,
                )
            )
    return samples


def protein_days() -> set:
    """The local dates protein was logged on — the behavioural coverage of the interval the
    Vitamin D claim spans."""
    covered = set()
    for _, _, first, last, _ in PROTEIN_PHASES:
        covered.update(datetime.fromisoformat(d).date() for d in _days(first, last))
    return covered


# ── the Vitamin D pair (ADR-15 / OQ5) ──────────────────────────────────────────────────
VITAMIN_D_BASELINE = ("2026-03-25", 6.2)
VITAMIN_D_FOLLOW_UP = ("2026-07-03", 38.4)


def vitamin_d_measurements() -> list[MetricSample]:
    """Two blood panels, 100 days apart. Point events: no composition, no expansion."""
    return [
        MetricSample(memory_id=_mid(1001), event_time=_at(VITAMIN_D_BASELINE[0]),
                     value=VITAMIN_D_BASELINE[1]),
        MetricSample(memory_id=_mid(1002), event_time=_at(VITAMIN_D_FOLLOW_UP[0]),
                     value=VITAMIN_D_FOLLOW_UP[1]),
    ]


#: The structurally detectable changes inside the Vitamin D interval (§4.4): supplement and
#: workout series onsets, plus the protein level shifts the other detector finds. Dates only —
#: no note prose, which is the point (**I-8**).
VITAMIN_D_INTERVENTION_DATES: tuple[tuple[str, str, str], ...] = (
    ("series_onset:supplement:vitamin_d:2026-03-28", "2026-03-28", "series_onset"),
    ("series_onset:supplement:vitamin_b12:2026-03-28", "2026-03-28", "series_onset"),
    ("level_shift:protein_g:2026-04-25", "2026-04-25", "level_shift"),
    ("series_onset:supplement:oral_minoxidil:2026-04-25", "2026-04-25", "series_onset"),
    ("level_shift:protein_g:2026-06-15", "2026-06-15", "level_shift"),
    ("series_onset:workout:2026-06-16", "2026-06-16", "series_onset"),
    ("level_shift:protein_g:2026-06-23", "2026-06-23", "level_shift"),
    ("series_onset:supplement:mintop:2026-06-24", "2026-06-24", "series_onset"),
)


def vitamin_d_interventions() -> list[Intervention]:
    return [
        Intervention(ident=ident, at=_at(day), kind=kind, label="", memory_ids=(_mid(2000 + n),),
                     n_memories=1)
        for n, (ident, day, kind) in enumerate(VITAMIN_D_INTERVENTION_DATES)
    ]
