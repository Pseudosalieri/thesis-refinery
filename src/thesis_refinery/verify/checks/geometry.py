"""Page geometry: size and margins, per section.

Two decisions worth stating.

**Every section, not just the first.** Word stores page setup per section, so a
document whose first section is A4 and whose appendix is Letter prints correctly
in the middle and wrong at the end. The predecessor's audit checked one section's
literal values and reported everything else as fine.

**Side margins are allowed to be one of two values.** Front matter is bound
differently from the body, so the conventional specification genuinely has two
legitimate side margins. A check that demanded one number would fail on every
correct document; a check that accepted *any* number would pass on a wrong one.
Accepting exactly the two configured values, and reporting which sections use
which, is the honest middle.
"""

from __future__ import annotations

from collections.abc import Iterator

from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.units import twips_to_mm
from thesis_refinery.docxlib.xmlq import Element, find
from thesis_refinery.verify.checks import Check
from thesis_refinery.verify.context import Context
from thesis_refinery.verify.findings import Finding, failed, passed, warned

CHECK = Check(
    name="geometry",
    description="page size and margins, for every section",
    run=lambda context: run(context),
)

_TOLERANCE_MM = 0.2
"""Twips are 1/1440 inch, so a correct millimetre value round-trips to within this."""


def run(context: Context) -> Iterator[Finding]:
    sections = context.sections()
    if not sections:
        yield warned("geometry.sections", "the document declares no sections at all")
        return

    yield from _page_size(context, sections)
    yield from _margins(context, sections)
    yield from _section_count(context, sections)


def _page_size(context: Context, sections: list[Element]) -> Iterator[Finding]:
    expected = (
        context.style.page.width_twips,
        context.style.page.height_twips,
    )
    offenders: list[str] = []

    for index, section in enumerate(sections):
        size = find(section, "w:pgSz")
        if size is None:
            offenders.append(f"section {index + 1}: no w:pgSz")
            continue
        actual = (size.get(q("w")), size.get(q("h")))
        if actual != (str(expected[0]), str(expected[1])):
            width_mm, height_mm = _mm_of(size)
            offenders.append(
                f"section {index + 1}: {width_mm:.1f} x {height_mm:.1f} mm, "
                f"expected {context.style.page.width_mm:.1f} x {context.style.page.height_mm:.1f} mm"
            )

    if offenders:
        yield failed(
            "geometry.page-size",
            f"{len(offenders)} of {len(sections)} section(s) have the wrong page size",
            tuple(offenders),
        )
        return

    yield passed(
        "geometry.page-size",
        f"{len(sections)} section(s) at {context.style.page.width_mm:g} x "
        f"{context.style.page.height_mm:g} mm",
    )


def _margins(context: Context, sections: list[Element]) -> Iterator[Finding]:
    margins = context.style.margins
    allowed_sides = {margins.body_side_twips, margins.front_matter_side_twips}
    offenders: list[str] = []
    side_usage: dict[int, list[int]] = {}

    for index, section in enumerate(sections):
        element = find(section, "w:pgMar")
        if element is None:
            offenders.append(f"section {index + 1}: no w:pgMar")
            continue

        for attribute, wanted, label in (
            ("top", margins.top_twips, "top"),
            ("bottom", margins.bottom_twips, "bottom"),
        ):
            actual = _int_or_none(element.get(q(attribute)))
            if actual is None or abs(actual - wanted) > _twips_tolerance():
                offenders.append(
                    f"section {index + 1}: {label} margin {_mm(actual)} mm, "
                    f"expected {_mm(wanted)} mm"
                )

        left = _int_or_none(element.get(q("left")))
        right = _int_or_none(element.get(q("right")))
        for actual, label in ((left, "left"), (right, "right")):
            if actual is not None and not _is_allowed_side(actual, allowed_sides):
                offenders.append(
                    f"section {index + 1}: {label} margin {_mm(actual)} mm is neither the body "
                    f"({margins.body_side_mm:g} mm) nor the front-matter "
                    f"({margins.front_matter_side_mm:g} mm) value"
                )
        side_usage.setdefault(left if left is not None else -1, []).append(index + 1)

    if offenders:
        yield failed(
            "geometry.margins",
            f"{len(offenders)} margin problem(s) across {len(sections)} section(s)",
            tuple(offenders),
        )
        return

    described = ", ".join(
        f"{twips_to_mm(value):.0f} mm in section(s) {', '.join(str(i) for i in indexes)}"
        for value, indexes in sorted(side_usage.items())
        if value >= 0
    )
    yield passed(
        "geometry.margins",
        f"top {margins.top_mm:g} mm, bottom {margins.bottom_mm:g} mm; side margins: {described}",
    )


def _section_count(context: Context, sections: list[Element]) -> Iterator[Finding]:
    expected = context.expectations.sections
    actual = len(sections)

    if expected is None:
        yield passed(
            "geometry.sections",
            f"{actual} section(s), derived from the document",
            ("set checks.expect_sections to pin this",),
        )
        return

    if actual != expected:
        yield failed(
            "geometry.sections",
            f"expected {expected} section(s), found {actual}",
            (
                "a missing section break does not look like a broken section break: it "
                "surfaces as a wrong running head on the even pages of one chapter",
            ),
        )
        return

    yield passed("geometry.sections", f"{actual} section(s), as expected")


def _mm_of(size: Element) -> tuple[float, float]:
    return (
        twips_to_mm(_int_or_none(size.get(q("w"))) or 0),
        twips_to_mm(_int_or_none(size.get(q("h"))) or 0),
    )


def _is_allowed_side(actual: int, allowed: set[int]) -> bool:
    """True when a side margin is one of the configured values, within tolerance."""
    return any(abs(actual - candidate) <= _twips_tolerance() for candidate in allowed)


def _mm(twips: int | None) -> str:
    return "missing" if twips is None else f"{twips_to_mm(twips):.1f}"


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _twips_tolerance() -> int:
    from thesis_refinery.docxlib.units import mm_to_twips

    return mm_to_twips(_TOLERANCE_MM)
