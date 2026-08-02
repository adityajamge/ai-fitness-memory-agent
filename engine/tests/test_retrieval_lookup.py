"""normalize_item() contract tests + the item-match half of the lookup builder (T8 M3,
docs/engineering/replay-architecture.md §4.13). Split from test_retrieval_timeline.py
(which keeps the pre-existing last/first-event and non-item lookup tests) because this
file's subject is the normalization stop-gap specifically, not the lookup family at large.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from engine.db import Database
from engine.memory import Memory
from engine.repository import insert_memories
from engine.retrieval import LookupSpec, RetrievalSpecError, lookup_events, normalize_item

UTC = timezone.utc
T0 = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


def _meal(user_id: UUID, event_time: datetime, item_name: str) -> Memory:
    return Memory(
        user_id=user_id,
        event_time=event_time,
        tz="Asia/Kolkata",
        type="meal",
        source="chat",
        provenance="live",
        confidence=0.9,
        payload={"items": [{"name": item_name}], "meal_type": "lunch"},
        summary=f"meal: {item_name}",
    )


def _seed(db: Database, memories: list[Memory]) -> list[UUID]:
    with db.transaction() as cur:
        return insert_memories(cur, memories)


def _lookup(db: Database, user_id: UUID, spec: LookupSpec):
    with db.transaction() as cur:
        return lookup_events(cur, user_id, spec)


# ── normalize_item: worked examples (§4.13 table) ──────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Chicken", "chicken"),
        ("chicken,", "chicken"),
        ("(chicken)", "chicken"),
        (" grilled  chicken ", "grilled chicken"),
        ("low-fat", "low-fat"),
        ("Omega-3", "omega-3"),
        ("purée", "purée"),
        ("eggs", "eggs"),
    ],
)
def test_normalize_item_worked_examples(raw: str, expected: str) -> None:
    assert normalize_item(raw) == expected


def test_normalize_item_ellipsis_normalizes_to_empty_and_raises() -> None:
    with pytest.raises(RetrievalSpecError):
        normalize_item("...")


# ── normalize_item: contract properties ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "Chicken",
        "  Grilled Chicken!! ",
        "low-fat",
        "purée",
        "MURGH",
        "ǰ",  # LATIN SMALL LETTER J WITH CARON — see the step-3 test below for why
    ],
)
def test_normalize_item_is_idempotent(raw: str) -> None:
    once = normalize_item(raw)
    assert normalize_item(once) == once


def test_normalize_item_is_deterministic_and_pure() -> None:
    raw = "  Grilled Chicken!! "
    assert normalize_item(raw) == normalize_item(raw)  # same input -> same output, twice
    assert raw == "  Grilled Chicken!! "  # the argument itself is untouched


def test_normalize_item_step_three_guard_casefold_denormalizes() -> None:
    """The step-3 guard (§4.13): casefolding can emit a sequence that is no longer NFC.

    U+01F0 (LATIN SMALL LETTER J WITH CARON, 'ǰ') is a real, verifiable instance: its
    casefold decomposes it into 'j' + COMBINING CARON (U+006A U+030C) — two codepoints,
    not in NFC form. Skipping the second NFC pass would leave normalize_item's output in
    that decomposed shape; the second pass recomposes it back to the single codepoint
    U+01F0, which is what makes normalize_item(normalize_item(x)) == normalize_item(x)
    actually hold for this input rather than merely being asserted.
    """
    s = "ǰ"

    casefold_only = unicodedata.normalize("NFC", s).casefold()
    # step 3 has real work to do here — casefold_only is not itself NFC-normalized
    assert unicodedata.normalize("NFC", casefold_only) != casefold_only

    out = normalize_item(s)
    assert unicodedata.normalize("NFC", out) == out  # normalize_item's output IS NFC
    assert out == s  # recomposed back to the single codepoint
    assert normalize_item(out) == out  # idempotent


# ── normalize_item: explicit non-goals (§4.13) — each must NOT happen ──────────────────


def test_normalize_item_does_not_stem() -> None:
    assert normalize_item("running") == "running"


def test_normalize_item_does_not_singularize() -> None:
    assert normalize_item("eggs") != normalize_item("egg")


def test_normalize_item_does_not_strip_accents() -> None:
    assert normalize_item("purée") != "puree"


def test_normalize_item_does_not_tokenize() -> None:
    assert normalize_item("grilled chicken") == "grilled chicken"  # stays one string


def test_normalize_item_does_not_expand_synonyms() -> None:
    assert normalize_item("murgh") != normalize_item("chicken")


def test_normalize_item_does_not_fuzzy_match() -> None:
    assert normalize_item("chiken") != normalize_item("chicken")


def test_normalize_item_does_not_spell_correct() -> None:
    assert normalize_item("chikcen") != normalize_item("chicken")


def test_normalize_item_does_not_transliterate() -> None:
    assert normalize_item("dahi") != normalize_item("curd")


def test_normalize_item_does_not_apply_nfkc_compat_folding() -> None:
    assert normalize_item("①") == "①"  # NFKC would fold this to "1"; NFC does not


def test_normalize_item_preserves_internal_punctuation() -> None:
    assert normalize_item("low-fat") == "low-fat"
    assert normalize_item("sugar-free") == "sugar-free"
    assert normalize_item("B-12") == "b-12"


# ── lookup_events: item match integration ───────────────────────────────────────────────


def test_lookup_item_match_is_case_whitespace_punctuation_insensitive(db, user_id) -> None:
    _seed(db, [_meal(user_id, T0, "Grilled Chicken")])
    result, _ = _lookup(db, user_id, LookupSpec(type="meal", item=" grilled  chicken! "))
    assert len(result.entries) == 1


def test_lookup_item_internal_punctuation_is_not_ignored(db, user_id) -> None:
    _seed(db, [_meal(user_id, T0, "low-fat")])
    result, _ = _lookup(db, user_id, LookupSpec(type="meal", item="lowfat"))
    assert result.is_empty


def test_lookup_item_accents_are_not_stripped(db, user_id) -> None:
    _seed(db, [_meal(user_id, T0, "purée")])
    result, _ = _lookup(db, user_id, LookupSpec(type="meal", item="puree"))
    assert result.is_empty


def test_lookup_item_substring_is_not_a_match(db, user_id) -> None:
    """§4.13's other named non-goal: 'grilled chicken' does not match a search for
    'chicken' -- substring containment would blur lookup_events from exact to fuzzy."""
    _seed(db, [_meal(user_id, T0, "grilled chicken")])
    result, _ = _lookup(db, user_id, LookupSpec(type="meal", item="chicken"))
    assert result.is_empty


def test_lookup_item_normalizes_to_empty_raises_at_query_time(db, user_id) -> None:
    """LookupSpec's own str.strip() check lets '...' through (non-empty, non-whitespace);
    normalize_item's stricter punctuation-aware emptiness check catches it inside
    lookup_events instead. Two different checks, two different layers -- both real."""
    spec = LookupSpec(type="meal", item="...")  # construction succeeds
    with pytest.raises(RetrievalSpecError):
        _lookup(db, user_id, spec)  # execution raises


def test_lookup_item_limit_applies_after_filter_not_before(db, user_id) -> None:
    """The correctness bug Q1 flagged: if LIMIT ran in SQL before the item filter, the two
    most recent (non-matching) rows would be fetched and filtered away entirely, returning
    0 results instead of the 2 real chicken meals."""
    memories = [
        _meal(user_id, T0, "chicken"),
        _meal(user_id, T0 + timedelta(days=1), "chicken"),
        _meal(user_id, T0 + timedelta(days=2), "paneer"),
        _meal(user_id, T0 + timedelta(days=3), "rice"),
        _meal(user_id, T0 + timedelta(days=4), "dal"),
    ]
    ids = _seed(db, memories)
    result, step = _lookup(db, user_id, LookupSpec(type="meal", item="chicken", n=2))
    assert [e.id for e in result.entries] == [ids[1], ids[0]]  # newest chicken first
    assert step.row_count == 2


def test_lookup_item_row_count_is_post_filter(db, user_id) -> None:
    _seed(
        db,
        [
            _meal(user_id, T0, "chicken"),
            _meal(user_id, T0 + timedelta(days=1), "paneer"),
            _meal(user_id, T0 + timedelta(days=2), "rice"),
        ],
    )
    _, step = _lookup(db, user_id, LookupSpec(type="meal", item="chicken"))
    assert step.row_count == 1  # not 3 -- the unfiltered scan size


def test_lookup_item_trace_discloses_in_engine_filter(db, user_id) -> None:
    """ADR-12 honesty: the trace must not silently hide that filtering ran in Python. The
    executed SQL carries a comment naming normalize_item, and params exposes both the raw
    and normalized search term (recall_memories already sets this precedent for `query`)."""
    _seed(db, [_meal(user_id, T0, "Grilled Chicken")])
    _, step = _lookup(db, user_id, LookupSpec(type="meal", item="Grilled Chicken"))
    assert "normalize_item" in step.sql
    # LIMIT moved to Python -- no LIMIT clause in the executed SQL (the comment fragment
    # mentions the word "LIMIT" in prose, so check for the clause, not the substring).
    assert "LIMIT %(n)s" not in step.sql
    assert step.params["item"] == "Grilled Chicken"
    assert step.params["item_normalized"] == "grilled chicken"


def test_lookup_no_item_filter_keeps_limit_in_sql(db, user_id) -> None:
    """The non-item path is untouched: LIMIT stays server-side, no comment, no payload."""
    _seed(db, [_meal(user_id, T0, "chicken")])
    _, step = _lookup(db, user_id, LookupSpec(type="meal"))
    assert "LIMIT" in step.sql
    assert "normalize_item" not in step.sql
    assert "item" not in step.params
