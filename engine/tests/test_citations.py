"""Mechanical citation validation (M2, glass-box-architecture.md §4.4).

Pure: no database, no model, no clock. A failure here names a contract, never an environment.
"""

from __future__ import annotations

from uuid import uuid4

from engine.citations import validate_citations


def test_every_marker_resolving_is_valid() -> None:
    a, b = uuid4(), uuid4()
    report = validate_citations(f"You averaged 46g [{a}] and slept 7h [{b}].", {a, b})

    assert report.status == "valid"
    assert report.cited == (a, b)
    assert report.invalid == ()


def test_an_unresolvable_marker_is_invalid_and_named() -> None:
    """The UI flags these in place, so the marker itself has to survive the report."""
    real, hallucinated = uuid4(), uuid4()
    report = validate_citations(f"Real [{real}] and invented [{hallucinated}].", {real})

    assert report.status == "invalid"
    assert report.cited == (real,), "the valid citation still resolves"
    assert report.invalid == (str(hallucinated),)


def test_evidence_available_but_nothing_cited_is_uncited() -> None:
    report = validate_citations("You've been doing well lately.", {uuid4(), uuid4()})

    assert report.status == "uncited"
    assert report.cited == ()
    assert report.citable_count == 2


def test_no_markers_and_nothing_citable_is_valid_not_uncited() -> None:
    """The most common empty state must not read as a citation defect.

    "No logged data in that window" is the *correct* reply to an empty context. Reporting it
    as uncited would train the UI to cry wolf on every new account's first question.
    """
    report = validate_citations("I don't have anything logged for that window yet.", set())

    assert report.status == "valid"
    assert report.citable_count == 0


def test_citation_order_follows_the_prose_not_the_set() -> None:
    """The UI numbers chips from this, so a reader meets them in the order they appear."""
    first, second = uuid4(), uuid4()
    report = validate_citations(f"[{second}] came before [{first}] in the text.", {first, second})

    assert report.cited == (second, first)


def test_repeated_markers_are_reported_once() -> None:
    a = uuid4()
    report = validate_citations(f"[{a}] and again [{a}] and once more [{a}].", {a})

    assert report.cited == (a,)
    assert report.status == "valid"


def test_bracketed_prose_is_not_a_citation() -> None:
    """Only well-formed UUIDs are markers — otherwise ordinary prose would be reported as
    an invalid citation, and the flag would stop meaning anything."""
    a = uuid4()
    report = validate_citations(f"See below [see notes] for detail [{a}].", {a})

    assert report.status == "valid"
    assert report.invalid == ()


def test_validation_never_rewrites_or_interprets(monkeypatch) -> None:
    """I-27: deterministic, and a pure function of (answer, citable set).

    Same inputs, same verdict, every time — no clock, no model, no hidden state.
    """
    a = uuid4()
    answer = f"Protein averaged 46g [{a}]."
    first = validate_citations(answer, {a})
    second = validate_citations(answer, {a})

    assert first == second


def test_report_serializes_for_the_api() -> None:
    a, bad = uuid4(), uuid4()
    payload = validate_citations(f"[{a}] [{bad}]", {a}).to_json()

    assert payload == {
        "cited": [str(a)],
        "invalid": [str(bad)],
        "status": "invalid",
        "citable_count": 1,
    }
