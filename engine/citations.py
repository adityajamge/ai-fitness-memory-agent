"""Mechanical citation validation (T7b, glass-box-architecture.md §4.4).

The honest-narrator contract is an **enforced property, not a prompt instruction**
(05-agent-architecture.md, ADR-12): after generation, the engine checks every citation
marker in the answer against the set the narrator was permitted to cite.

**What this module must never do (I-27).** It does not call a model, does not parse prose for
meaning, and does not rewrite the answer. It extracts `[uuid]` markers, resolves each against
a set, and reports. Anything cleverer would put language interpretation inside the mechanism
whose entire value is that it is deterministic — and would make the glass box's guarantee
depend on the thing it exists to check.

**The honest scope of the guarantee (ADR-13.13).** A `valid` verdict proves every cited ID was
retrieved for this turn. It does **not** prove the sentence characterises that row correctly —
numeric and directional fidelity belong to the citation-compliance eval, and the UI lets a
reader compare claim against evidence in one click. Presentation must not imply "verified"
where this proves "resolvable". Phase 7's write-ups inherit that distinction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

#: Every ``[<uuid>]`` marker in an answer. Deliberately strict: a marker that is not a
#: well-formed UUID is not a citation at all, so prose containing ``[see below]`` is left
#: alone rather than reported as an invalid citation.
CITATION_RE = re.compile(
    r"\[([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\]"
)

CitationStatus = Literal["valid", "invalid", "uncited"]


@dataclass(frozen=True, slots=True)
class CitationReport:
    """The verdict on one answer's citations.

    ``cited`` preserves first-appearance order rather than sorting, because that is the order
    a reader meets the claims in; the UI numbers chips from it.
    """

    cited: tuple[UUID, ...]
    invalid: tuple[str, ...]
    status: CitationStatus
    #: How many IDs the narrator *could* have cited. Lets the UI tell an honest empty answer
    #: ("no logged data", nothing citable) from a suspicious one (evidence retrieved, none
    #: cited) without a fourth status or any inspection of the prose.
    citable_count: int

    def to_json(self) -> dict:
        return {
            "cited": [str(i) for i in self.cited],
            "invalid": list(self.invalid),
            "status": self.status,
            "citable_count": self.citable_count,
        }


def validate_citations(answer: str, citable_ids: frozenset[UUID] | set[UUID]) -> CitationReport:
    """Classify an answer's citations against the set it was allowed to cite.

    Three outcomes, mirroring the shape of the extraction and planning contracts:

    ``valid``     every marker resolves (including the no-markers-nothing-citable case).
    ``invalid``   at least one marker does not resolve — the UI flags those in place.
    ``uncited``   the answer cites nothing while evidence *was* available to cite.

    An answer with no markers and nothing citable is ``valid``, not ``uncited``: "no logged
    data in that window" is the correct, honest reply to an empty context, and reporting it as
    a citation defect would train the UI to cry wolf on the most common empty state.
    """
    allowed = {str(i) for i in citable_ids}

    cited: list[UUID] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for marker in CITATION_RE.findall(answer):
        if marker in seen:
            continue
        seen.add(marker)
        if marker in allowed:
            cited.append(UUID(marker))
        else:
            invalid.append(marker)

    if invalid:
        status: CitationStatus = "invalid"
    elif not seen and allowed:
        status = "uncited"
    else:
        status = "valid"

    return CitationReport(
        cited=tuple(cited),
        invalid=tuple(invalid),
        status=status,
        citable_count=len(allowed),
    )
