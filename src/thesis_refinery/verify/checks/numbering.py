"""Page numbering: the format sequence, and whether it restarts where it should.

The defect this is built to catch is specific and common. Front matter is
numbered in lower-case or upper-case roman; the body restarts at arabic 1. If the
restart is missing, the body continues from the front matter's last number, and
the document reads ``vii, viii, 9, 10`` — a defect that is invisible in a
screenshot of any single page and obvious to an examiner holding the printed
thesis.

One transition is allowed, front matter to body. Two transitions means the format
flips back somewhere and pages after that are numbered in the wrong style.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import pairwise

from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.xmlq import Element, find
from thesis_refinery.verify.checks import Check
from thesis_refinery.verify.context import Context
from thesis_refinery.verify.findings import Finding, failed, passed

CHECK = Check(
    name="numbering",
    description="page-number format per section, and where numbering restarts",
    run=lambda context: run(context),
)

_KNOWN_FORMATS = ("decimal", "upperRoman", "lowerRoman", "upperLetter", "lowerLetter")


@dataclass(frozen=True)
class SectionNumbering:
    index: int
    fmt: str | None
    start: int | None


def run(context: Context) -> Iterator[Finding]:
    sections = context.sections()
    if not sections:
        return

    numbering = [_numbering_of(index, section) for index, section in enumerate(sections)]

    yield from _formats_declared(context, numbering)
    yield from _single_transition(context, numbering)
    yield from _restarts_at_one(context, numbering)


def _numbering_of(index: int, section: Element) -> SectionNumbering:
    element = find(section, "w:pgNumType")
    if element is None:
        return SectionNumbering(index=index, fmt=None, start=None)
    return SectionNumbering(
        index=index,
        fmt=element.get(q("fmt")),
        start=_int_or_none(element.get(q("start"))),
    )


def _formats_declared(context: Context, numbering: list[SectionNumbering]) -> Iterator[Finding]:
    allowed = {
        context.style.header_footer.front_matter_number_format,
        context.style.header_footer.body_number_format,
    }
    missing = [entry.index + 1 for entry in numbering if entry.fmt is None]
    unknown = [
        f"section {entry.index + 1}: format {entry.fmt!r}"
        for entry in numbering
        if entry.fmt is not None and entry.fmt not in _KNOWN_FORMATS
    ]
    unexpected = [
        f"section {entry.index + 1}: {entry.fmt}, expected one of "
        f"{context.style.header_footer.front_matter_number_format} or "
        f"{context.style.header_footer.body_number_format}"
        for entry in numbering
        if entry.fmt is not None and entry.fmt in _KNOWN_FORMATS and entry.fmt not in allowed
    ]

    if missing or unknown or unexpected:
        yield failed(
            "numbering.formats",
            f"{len(missing) + len(unknown) + len(unexpected)} section(s) without a usable "
            "page-number format",
            (
                *(f"section {index}: no w:pgNumType/w:fmt" for index in missing),
                *unknown,
                *unexpected,
            ),
        )
        return

    sequence = " → ".join(entry.fmt or "?" for entry in numbering)
    yield passed("numbering.formats", f"formats follow {sequence}")


def _single_transition(context: Context, numbering: list[SectionNumbering]) -> Iterator[Finding]:
    formats = [entry.fmt for entry in numbering if entry.fmt is not None]
    transitions = sum(1 for before, after in pairwise(formats) if before != after)

    if transitions > 1:
        yield failed(
            "numbering.transitions",
            f"the page-number format changes {transitions} times; at most one change "
            "(front matter to body) is expected",
            tuple(f"section {entry.index + 1}: {entry.fmt}" for entry in numbering),
        )
        return

    yield passed(
        "numbering.transitions",
        "no format change" if transitions == 0 else "one format change, front matter to body",
    )


def _restarts_at_one(context: Context, numbering: list[SectionNumbering]) -> Iterator[Finding]:
    """A format change must come with ``w:start="1"``, or the body continues the roman numerals."""
    problems: list[str] = []
    previous: str | None = None

    for entry in numbering:
        if entry.fmt is None:
            continue
        if previous is not None and entry.fmt != previous and entry.start != 1:
            problems.append(
                f"section {entry.index + 1}: format changes to {entry.fmt} but "
                f"{'declares no start' if entry.start is None else f'starts at {entry.start}'}"
            )
        previous = entry.fmt

    if problems:
        yield failed(
            "numbering.restart",
            f"{len(problems)} format change(s) do not restart at 1; the body would continue "
            "the front matter's numbering",
            tuple(problems),
        )
        return

    yield passed("numbering.restart", "every format change restarts at 1")


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
