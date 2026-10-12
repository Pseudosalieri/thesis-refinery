"""Running heads and page numbers.

Three things are checked, each corresponding to a way a formatted thesis is
wrong in a way that is only visible in print:

* the rule under the running head exists, on every header part;
* the configured odd-page text appears in some header — a check that a header
  exists at all would pass on a header containing the wrong institution's name;
* the page number in each footer is set at the configured size. A footer number
  that renders at the paragraph default is 12 pt where the rest of the
  specification says 9 pt, and it is the kind of thing nobody notices until the
  printed copy is in their hands.
"""

from __future__ import annotations

from collections.abc import Iterator

from thesis_refinery.docxlib.fields import iter_instructions
from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.xmlq import Element, find, iter_descendants, text_of
from thesis_refinery.verify.checks import Check
from thesis_refinery.verify.context import Context
from thesis_refinery.verify.findings import Finding, failed, passed, warned

CHECK = Check(
    name="headers",
    description="running head rule, odd-page text, and footer page-number size",
    run=lambda context: run(context),
)


def run(context: Context) -> Iterator[Finding]:
    if not context.header_parts and not context.footer_parts:
        yield warned(
            "headers.parts",
            "the document has no header or footer parts",
            ("a thesis with no running head is unusual, but this is a warning, not a failure",),
        )
        return

    yield from _header_rule(context)
    yield from _odd_page_text(context)
    yield from _footer_size(context)


def _header_rule(context: Context) -> Iterator[Finding]:
    if not context.header_parts:
        yield warned("headers.rule", "no header parts to check")
        return

    missing = [
        name for name, root in context.header_parts.items() if not _has_bottom_rule(root)
    ]
    if missing:
        yield failed(
            "headers.rule",
            f"{len(missing)} of {len(context.header_parts)} header part(s) have no rule below "
            "the running head",
            tuple(sorted(missing)),
        )
        return

    yield passed(
        "headers.rule",
        f"all {len(context.header_parts)} header part(s) carry a bottom rule",
    )


def _has_bottom_rule(header: Element) -> bool:
    for paragraph in iter_descendants(header, "w:p"):
        properties = find(paragraph, "w:pPr")
        if properties is None:
            continue
        borders = find(properties, "w:pBdr")
        if borders is None:
            continue
        bottom = find(borders, "w:bottom")
        if bottom is not None and bottom.get(q("val")) not in (None, "nil", "none"):
            return True
    return False


def _odd_page_text(context: Context) -> Iterator[Finding]:
    wanted = context.style.header_footer.odd_page_text
    if not wanted:
        yield warned(
            "headers.odd-text",
            "no odd-page running head is configured, so nothing was checked",
            ("set style.header_footer.odd_page_text to the wording your institution requires",),
        )
        return

    if not context.header_parts:
        yield warned("headers.odd-text", "no header parts to check")
        return

    for name, root in sorted(context.header_parts.items()):
        if wanted in text_of(root):
            yield passed("headers.odd-text", f"running head {wanted!r} found in {name}")
            return

    yield failed(
        "headers.odd-text",
        f"the configured running head {wanted!r} does not appear in any header part",
        tuple(sorted(context.header_parts)),
    )


def _footer_size(context: Context) -> Iterator[Finding]:
    if not context.footer_parts:
        yield warned("footers.page-number", "no footer parts to check")
        return

    wanted = str(context.style.header_footer.footer_size_half_points)
    offenders: list[str] = []
    checked = 0

    for name, root in sorted(context.footer_parts.items()):
        for paragraph in iter_descendants(root, "w:p"):
            if not _has_page_field(paragraph):
                continue
            checked += 1
            size = _paragraph_number_size(paragraph)
            if size != wanted:
                offenders.append(
                    f"{name}: page-number runs at "
                    f"{'the paragraph default' if size is None else f'{int(size) / 2:g} pt'}, "
                    f"expected {context.style.header_footer.footer_size_pt:g} pt"
                )

    if checked == 0:
        yield failed(
            "footers.page-number",
            "no footer contains a page-number field, so the folio is missing",
            tuple(sorted(context.footer_parts)),
        )
        return

    if offenders:
        yield failed(
            "footers.page-number",
            f"{len(offenders)} of {checked} page-number placement(s) have the wrong size",
            tuple(offenders),
        )
        return

    yield passed(
        "footers.page-number",
        f"{checked} page-number placement(s) at {context.style.header_footer.footer_size_pt:g} pt",
    )


def _has_page_field(paragraph: Element) -> bool:
    return any("PAGE" in instruction for instruction in iter_instructions(paragraph))


def _paragraph_number_size(paragraph: Element) -> str | None:
    """The ``w:sz`` on the runs carrying the page field, or ``None`` if unset.

    Looks at the field's own runs first and falls back to the paragraph mark's
    ``w:rPr``, which is where Word puts paragraph-level run formatting.
    """
    for run in iter_descendants(paragraph, "w:r"):
        if find(run, "w:fldChar") is None and find(run, "w:instrText") is None:
            continue
        properties = find(run, "w:rPr")
        if properties is None:
            continue
        size = find(properties, "w:sz")
        if size is not None:
            return size.get(q("val"))

    properties = find(paragraph, "w:pPr")
    if properties is None:
        return None
    run_properties = find(properties, "w:rPr")
    if run_properties is None:
        return None
    size = find(run_properties, "w:sz")
    return size.get(q("val")) if size is not None else None
