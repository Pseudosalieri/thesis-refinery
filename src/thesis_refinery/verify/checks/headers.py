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
    yield from _even_pages_flag(context)
    yield from _odd_page_text(context)
    yield from _footer_size(context)
    yield from _orphan_parts(context)


def _header_rule(context: Context) -> Iterator[Finding]:
    """The rule under the running head, on the parts the document actually uses.

    Only the referenced parts, for the reason
    :meth:`Context.running_part_references` gives: a part no section points at is
    not printed, so demanding a rule of it reports a defect nobody can see. That is
    not hypothetical — this stage's predecessor-repointing case leaves the old part
    behind, and a check over every part in the package would then be measuring the
    abandoned one.
    """
    used = context.referenced_running_parts("header")
    if not used:
        yield warned("headers.rule", "no section references a header part")
        return

    missing = sorted(name for name in used if not _has_bottom_rule(context.header_parts.get(name)))
    if missing:
        yield failed(
            "headers.rule",
            f"{len(missing)} of {len(used)} referenced header part(s) have no rule below "
            "the running head",
            tuple(missing),
        )
        return

    yield passed("headers.rule", f"all {len(used)} referenced header part(s) carry a bottom rule")


def _has_bottom_rule(header: Element | None) -> bool:
    if header is None:
        return False
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


def _even_pages_flag(context: Context) -> Iterator[Finding]:
    """A referenced ``even`` header is dead without the document-wide flag.

    This is the check whose absence let a configured feature silently do nothing. A
    stage created and referenced a chapter-title running head for even pages, Word
    ignored it because ``w:evenAndOddHeaders`` was never set, and every other header
    check passed — the default head was present and correct, the rule was present,
    the folio was the right size. The document was wrong in a way no check could
    see, which is precisely the failure this project exists to make impossible.

    The flag lives in ``word/settings.xml``. A copy on a ``w:sectPr`` does not count:
    WordprocessingML puts it in settings, so accepting the stray spelling would pass
    a document Word still prints with the same header on both sides.
    """
    even_parts = context.running_part_references("header").get("even")
    if not even_parts:
        yield passed("headers.even-pages", "no even-page header is referenced")
        return

    settings = context.settings
    flag = find(settings, "w:evenAndOddHeaders") if settings is not None else None
    enabled = flag is not None and (flag.get(q("val"), "true") not in ("false", "0", "off"))

    if not enabled:
        yield failed(
            "headers.even-pages",
            f"{len(even_parts)} even-page header part(s) are referenced but "
            "w:evenAndOddHeaders is not set, so Word prints the default header on "
            "every page and the chapter title never appears",
            (
                *sorted(even_parts),
                "the flag belongs in word/settings.xml, not on a section",
            ),
        )
        return

    yield passed(
        "headers.even-pages",
        f"{len(even_parts)} even-page header part(s), and the flag that activates them is set",
    )


def _orphan_parts(context: Context) -> Iterator[Finding]:
    """Parts reachable from the document that no section references.

    A warning rather than a failure: an orphan is not a defect a reader can see, and
    a gate that fails on it would be failing on something the document prints
    correctly without. It is worth saying because the part still has to be carried in
    the file, and because it is the visible trace of a stage having repointed a
    section at a new part.
    """
    orphans = context.unused_running_parts()
    if not orphans:
        yield passed("headers.orphans", "every header and footer part is referenced by a section")
        return

    yield warned(
        "headers.orphans",
        f"{len(orphans)} running-head part(s) are reachable but referenced by no section",
        tuple(orphans),
    )


def _odd_page_text(context: Context) -> Iterator[Finding]:
    """The configured running head, in the part the odd pages actually print.

    Odd pages use the ``default`` header, so that is where the wording has to be.
    Searching every header part in the package would pass a document whose default
    header is wrong but whose *even* header happens to contain the text — a pass
    earned by a part printed on the other side of the sheet.
    """
    wanted = context.style.header_footer.odd_page_text
    if not wanted:
        yield warned(
            "headers.odd-text",
            "no odd-page running head is configured, so nothing was checked",
            ("set style.header_footer.odd_page_text to the wording your institution requires",),
        )
        return

    defaults = context.running_part_references("header").get("default")
    if not defaults:
        yield warned("headers.odd-text", "no section references a default header part")
        return

    for name in sorted(defaults):
        root = context.header_parts.get(name)
        if root is not None and wanted in text_of(root):
            yield passed("headers.odd-text", f"running head {wanted!r} found in {name}")
            return

    yield failed(
        "headers.odd-text",
        f"the configured running head {wanted!r} does not appear in the default header "
        "part(s) the odd pages print",
        tuple(sorted(defaults)),
    )


def _footer_size(context: Context) -> Iterator[Finding]:
    """The folio's size, on the footer parts the document actually uses."""
    used = context.referenced_running_parts("footer")
    if not used:
        yield warned("footers.page-number", "no section references a footer part")
        return

    wanted = str(context.style.header_footer.footer_size_half_points)
    offenders: list[str] = []
    checked = 0

    for name in sorted(used):
        root = context.footer_parts.get(name)
        if root is None:
            continue
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
            "no referenced footer contains a page-number field, so the folio is missing",
            tuple(sorted(used)),
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
