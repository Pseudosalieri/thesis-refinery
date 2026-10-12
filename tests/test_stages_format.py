"""Tests for the style-application stage.

The headline test is the one that matters: a *raw* document — no page setup, no
page numbering, no header or footer parts, a table with no borders, body
paragraphs whose style carries neither indent nor spacing — goes in, and the gate
comes back green. Its negative half is asserted too, because a gate that passes on
the input would make the whole exercise vacuous.

Everything after that pins down one transformation of that journey, and every one
of them carries the assertion that makes it able to fail: the raw table is checked
*not* to be a three-line table before the stage runs, the raw section is checked to
have no page size, the parts that must not change are checked byte for byte, and
each configured value is exercised with a *non-default* specification so that a
literal in the stage could not pass.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from thesis_refinery.config import Checks, Config, Identity, ProjectPaths
from thesis_refinery.docxlib.fields import contains_field, iter_instructions, update_fields_enabled
from thesis_refinery.docxlib.ns import CONTENT_TYPE, NSMAP, q, qn
from thesis_refinery.docxlib.pkgs import Package, content_types_part
from thesis_refinery.docxlib.synth import (
    build_docx,
    paragraph_xml,
    part_xml,
    style_xml,
    styles_xml,
    table_xml,
)
from thesis_refinery.docxlib.tables import header_row, is_three_line_table, iter_cells, tables_in
from thesis_refinery.docxlib.xmlq import (
    Element,
    find,
    findall,
    iter_descendants,
    local_name,
    text_of,
)
from thesis_refinery.stages import format
from thesis_refinery.style import DEFAULT_STYLE, HeaderFooterSpec, StyleSpec, TableSpec
from thesis_refinery.verify.gate import evaluate

W = NSMAP["w"]

RUNNING_HEAD = "EXAMPLE THESIS"
"""Synthetic, and set explicitly: :class:`HeaderFooterSpec` ships a *default*
wording rather than a fact, and a fixture that inherited it would only be testing
that default."""

STYLE = DEFAULT_STYLE.merged(header_footer=HeaderFooterSpec(odd_page_text=RUNNING_HEAD))

STYLES = styles_xml(
    [
        style_xml("Normal", "Normal", default=True),
        style_xml("Heading1", "Heading 1", based_on="Normal", outline_level=0),
        style_xml("CoverTitle", "Cover Title", based_on="Normal"),
        # Run properties and no paragraph properties: the shape that makes a blind
        # append put `w:pPr` after `w:rPr`, which Word rejects as corrupt.
        style_xml(
            "BodyText",
            "Body Text",
            based_on="Normal",
            run_properties='<w:rFonts w:ascii="Times New Roman"/>',
        ),
        style_xml("ImageCaption", "Image Caption", based_on="Normal"),
    ]
)

STYLES_WITHOUT_BODY = styles_xml(
    [
        style_xml("Normal", "Normal", default=True),
        style_xml("Heading1", "Heading 1", based_on="Normal", outline_level=0),
        style_xml("CoverTitle", "Cover Title", based_on="Normal"),
        style_xml("ImageCaption", "Image Caption", based_on="Normal"),
    ]
)

LOCALISED_STYLES = styles_xml(
    [
        style_xml("Normal", "Normal", default=True),
        style_xml("Heading1", "标题 1", based_on="Normal", outline_level=0),
        style_xml("CoverTitle", "Cover Title", based_on="Normal"),
        style_xml("BodyText", "Body Text", based_on="Normal"),
        style_xml("ImageCaption", "Image Caption", based_on="Normal"),
    ]
)

LETTER_SECTION = (
    "<w:sectPr>"
    '<w:pgSz w:w="12240" w:h="15840"/>'
    '<w:pgMar w:top="2500" w:right="1134" w:bottom="2500" w:left="1134" '
    'w:header="720" w:footer="720" w:gutter="0"/>'
    "</w:sectPr>"
)

ADDED_PARTS = {
    "word/header1.xml",
    "word/header2.xml",
    "word/footer1.xml",
    "word/settings.xml",
}
"""Parts a raw document has none of, and the stage creates.

`word/settings.xml` is on the list because the two document-wide flags this stage
is responsible for — `w:evenAndOddHeaders` and `w:updateFields` — have nowhere
else to live, and an even header that Word ignores is not a running head."""

CHANGED_PARTS = {
    "[Content_Types].xml",
    "word/_rels/document.xml.rels",
    "word/document.xml",
    "word/styles.xml",
}
"""Parts the stage genuinely rewrites. Every other part — the package's own rels,
the parts it only read — comes through byte for byte."""


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _section_break(text: str, *, style: str, first_line: int | None = None) -> str:
    """A paragraph that carries *text* and closes a section.

    A raw fragment, for two reasons: ``synth`` has no ``sectPr`` parameter, and the
    fixture is meant to be *raw* — a section with no page setup is exactly what a
    renderer produces and what this stage has to fix.
    """
    indent = f'<w:ind w:firstLine="{first_line}"/>' if first_line is not None else ""
    return (
        f'<w:p><w:pPr><w:pStyle w:val="{style}"/>{indent}<w:sectPr/></w:pPr>'
        f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
    )


def _overridden_paragraph(text: str, *, style: str, first_line: int) -> str:
    """A paragraph that already carries a direct first-line indent."""
    return (
        f'<w:p><w:pPr><w:pStyle w:val="{style}"/>'
        f'<w:ind w:firstLine="{first_line}"/></w:pPr>'
        f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
    )


def build_raw(path: Path, *, styles: str = STYLES) -> Path:
    """A rendered document before the formatting stage touches it.

    Two sections, because one section cannot show that the front matter is treated
    differently from the body. No page size, no page-number format, no header or
    footer parts, a table with no borders, and body paragraphs whose style carries
    neither indent nor spacing.
    """
    body = "\n".join(
        [
            _section_break("A cover paragraph.", style="CoverTitle"),
            paragraph_xml("Chapter One", style="Heading1"),
            paragraph_xml("The first body paragraph.", style="BodyText"),
            paragraph_xml("The second body paragraph.", style="BodyText"),
            paragraph_xml("Fig. 1 A caption.", style="ImageCaption"),
            table_xml([["Head", "Value"], ["a", "1"]]),
            "<w:sectPr/>",
        ]
    )
    return build_docx(path, body=body, styles=styles)


def config_for(root: Path, *, style: StyleSpec = STYLE) -> Config:
    """A configuration for the gate that needs no files on disk.

    No identity strings and no prefix source, so the front-matter checks skip: this
    file is about the stage, not about a project layout.
    """
    return Config(
        root=root,
        paths=ProjectPaths(
            root=root,
            thesis_dir=root / "manuscript",
            chapters_dir=root / "manuscript/chapters",
            output_dir=root / "manuscript/_outputs",
        ),
        identity=Identity(),
        checks=Checks(),
        style=style,
    )


def parts_of(path: Path) -> dict[str, bytes]:
    with Package(path) as package:
        return {name: package.read(name) for name in package.names()}


def xml_of(path: Path, part: str) -> Element:
    with Package(path) as package:
        return package.xml(part)


def sections_of(path: Path) -> list[Element]:
    return list(iter_descendants(xml_of(path, "word/document.xml"), "w:sectPr"))


def body_paragraphs_of(path: Path) -> list[Element]:
    body = find(xml_of(path, "word/document.xml"), "w:body")
    assert body is not None
    return [child for child in body if child.tag == q("p")]


def paragraphs_with_style(path: Path, style_id: str) -> list[Element]:
    """Body-level paragraphs carrying one ``w:pStyle``.

    Selected by style rather than by position: an index into the body is an
    assertion about the fixture that fails for a reason that has nothing to do
    with the stage.
    """
    found: list[Element] = []
    for paragraph in body_paragraphs_of(path):
        properties = find(paragraph, "w:pPr")
        style = find(properties, "w:pStyle") if properties is not None else None
        if style is not None and style.get(q("val")) == style_id:
            found.append(paragraph)
    return found


def reference_of(section: Element, name: str, side: str) -> Element | None:
    """A section's reference of this kind and side, matched the way Word means it."""
    for reference in findall(section, name):
        if (reference.get(q("type")) or "default") == side:
            return reference
    return None


def has_bottom_rule(root: Element) -> bool:
    """True when some paragraph in *root* carries a visible bottom rule."""
    for paragraph in iter_descendants(root, "w:p"):
        properties = find(paragraph, "w:pPr")
        borders = find(properties, "w:pBdr") if properties is not None else None
        bottom = find(borders, "w:bottom") if borders is not None else None
        if bottom is not None and bottom.get(q("val")) not in (None, "nil", "none"):
            return True
    return False


def run_sizes(root: Element) -> list[str | None]:
    """The ``w:sz`` of every run in *root*, ``None`` where a run declares none."""
    sizes: list[str | None] = []
    for run in iter_descendants(root, "w:r"):
        properties = find(run, "w:rPr")
        size = find(properties, "w:sz") if properties is not None else None
        sizes.append(size.get(q("val")) if size is not None else None)
    return sizes


def even_header_part(path: Path) -> str:
    """The part a section references as its even-page header."""
    section = sections_of(path)[0]
    reference = reference_of(section, "w:headerReference", "even")
    assert reference is not None, "no section references an even-page header"
    with Package(path) as package:
        for relationship in package.relationships("word/document.xml"):
            if relationship.get("Id") == reference.get(qn("r:id")):
                return f"word/{relationship.get('Target')}"
    raise AssertionError("the even-page reference points at no relationship")


# ---------------------------------------------------------------------------
# The headline
# ---------------------------------------------------------------------------


def test_a_raw_document_fails_the_gate_before_the_stage_runs(tmp_path: Path) -> None:
    """The negative half of the headline. Without it the headline proves nothing."""
    raw = build_raw(tmp_path / "raw.docx")
    report = evaluate(raw, config=config_for(tmp_path))

    failed = {finding.check for finding in report.failures()}
    assert not report.ok
    assert {
        "geometry.page-size",
        "numbering.formats",
        "body.first-line-indent",
        "body.line-spacing",
        "tables.three-line",
    } <= failed


def test_a_raw_document_passes_every_gate_after_the_stage_runs(tmp_path: Path) -> None:
    raw = build_raw(tmp_path / "raw.docx")
    destination = tmp_path / "final.docx"

    format.apply(raw, destination, style=STYLE)

    report = evaluate(destination, config=config_for(tmp_path))
    failures = report.failures()
    assert not failures, "\n".join(
        f"{finding.check}: {finding.message} {finding.detail}" for finding in failures
    )
    assert report.ok


def test_a_localised_heading_style_is_recognised_and_named(tmp_path: Path) -> None:
    """The localisation trap: a scan that knows only ``Heading 1`` runs to the end
    of a document whose heading is ``标题 1`` and indents the cover, and a
    ``STYLEREF`` naming the style that is not there renders as an error."""
    raw = build_raw(tmp_path / "raw.docx", styles=LOCALISED_STYLES)
    destination = tmp_path / "final.docx"

    format.apply(raw, destination, style=STYLE)

    report = evaluate(destination, config=config_for(tmp_path))
    assert report.ok, [finding.check for finding in report.failures()]

    even = xml_of(destination, even_header_part(destination))
    assert contains_field(even, 'STYLEREF "标题 1"')


# ---------------------------------------------------------------------------
# The contract with whoever called the stage
# ---------------------------------------------------------------------------


def test_apply_returns_the_destination_and_leaves_the_source_untouched(tmp_path: Path) -> None:
    raw = build_raw(tmp_path / "raw.docx")
    before = hashlib.sha256(raw.read_bytes()).hexdigest()

    destination = tmp_path / "nested" / "final.docx"
    returned = format.apply(raw, destination, style=STYLE)

    assert returned == destination
    assert destination.is_file()
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == before


def test_apply_refuses_to_write_over_its_own_input(tmp_path: Path) -> None:
    """The refusal in ``Package.save`` is relied on, not worked around."""
    raw = build_raw(tmp_path / "raw.docx")
    before = raw.read_bytes()

    with pytest.raises(ValueError, match="refusing to save over the input"):
        format.apply(raw, raw, style=STYLE)

    assert raw.read_bytes() == before


def test_exactly_the_expected_parts_change(tmp_path: Path) -> None:
    """``Package.changed`` is not observable from outside ``apply``, so the same
    assertion is made the way the package tests make it: part by part, against the
    input."""
    raw = build_raw(tmp_path / "raw.docx")
    before = parts_of(raw)

    destination = tmp_path / "final.docx"
    format.apply(raw, destination, style=STYLE)
    after = parts_of(destination)

    assert set(before) <= set(after), "the stage deleted a part"
    assert set(after) - set(before) == ADDED_PARTS
    assert {name for name in before if before[name] != after[name]} == CHANGED_PARTS


def test_applying_twice_changes_nothing_the_second_time(tmp_path: Path) -> None:
    raw = build_raw(tmp_path / "raw.docx")
    first = tmp_path / "first.docx"
    second = tmp_path / "second.docx"

    format.apply(raw, first, style=STYLE)
    format.apply(first, second, style=STYLE)

    assert parts_of(second)["word/document.xml"] == parts_of(first)["word/document.xml"]
    assert parts_of(second) == parts_of(first)


def test_without_a_heading_nothing_is_taken_for_body_text(tmp_path: Path) -> None:
    """No level-1 heading means no front-matter boundary, and the gate fails the
    document for the heading rather than for an indent. Indenting everything would
    indent the cover of a document whose cover cannot be identified."""
    body = "\n".join(
        [
            paragraph_xml("A cover paragraph.", style="CoverTitle"),
            paragraph_xml("Body text with no heading above it.", style="BodyText"),
            "<w:sectPr/>",
        ]
    )
    document = build_docx(tmp_path / "headingless.docx", body=body, styles=STYLES)
    styles_before = parts_of(document)["word/styles.xml"]

    destination = tmp_path / "final.docx"
    format.apply(document, destination, style=STYLE)

    assert parts_of(destination)["word/styles.xml"] == styles_before
    paragraph = paragraphs_with_style(destination, "BodyText")[0]
    properties = find(paragraph, "w:pPr")
    assert properties is not None
    assert find(properties, "w:ind") is None


def test_a_document_with_no_sections_gets_no_header_parts(tmp_path: Path) -> None:
    """Nothing would reference them, and an unreferenced part is not free: the gate
    reads header parts through the document's relationships, so it would still have
    to conform."""
    document = build_docx(
        tmp_path / "no-sections.docx",
        body=(
            paragraph_xml("Chapter One", style="Heading1")
            + paragraph_xml("Body text.", style="BodyText")
        ),
        styles=STYLES,
    )
    destination = tmp_path / "final.docx"

    format.apply(document, destination, style=STYLE)

    assert not any(
        name.startswith(("word/header", "word/footer")) for name in parts_of(destination)
    )


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------


def test_page_setup_goes_on_every_section(tmp_path: Path) -> None:
    raw = build_raw(tmp_path / "raw.docx")
    destination = tmp_path / "final.docx"

    format.apply(raw, destination, style=STYLE)

    sections = sections_of(destination)
    assert len(sections) == 2

    margins = STYLE.margins
    sides = [margins.front_matter_side_twips, margins.body_side_twips]
    for index, section in enumerate(sections):
        size = find(section, "w:pgSz")
        assert size is not None
        assert (size.get(q("w")), size.get(q("h"))) == (
            str(STYLE.page.width_twips),
            str(STYLE.page.height_twips),
        )

        page_margins = find(section, "w:pgMar")
        assert page_margins is not None
        assert page_margins.get(q("top")) == str(margins.top_twips)
        assert page_margins.get(q("bottom")) == str(margins.bottom_twips)
        assert page_margins.get(q("left")) == str(sides[index])
        assert page_margins.get(q("right")) == str(sides[index])
        assert page_margins.get(q("header")) == str(margins.header_distance_twips)
        assert page_margins.get(q("footer")) == str(margins.footer_distance_twips)


def test_a_wrong_page_size_is_corrected(tmp_path: Path) -> None:
    document = build_docx(
        tmp_path / "letter.docx",
        body=(
            paragraph_xml("Chapter One", style="Heading1")
            + paragraph_xml("Body text.", style="BodyText")
            + LETTER_SECTION
        ),
        styles=STYLES,
    )
    size = find(sections_of(document)[0], "w:pgSz")
    assert size is not None
    assert (size.get(q("w")), size.get(q("h"))) == ("12240", "15840")

    destination = tmp_path / "final.docx"
    format.apply(document, destination, style=STYLE)

    corrected = find(sections_of(destination)[0], "w:pgSz")
    assert corrected is not None
    assert (corrected.get(q("w")), corrected.get(q("h"))) == (
        str(STYLE.page.width_twips),
        str(STYLE.page.height_twips),
    )
    margins = find(sections_of(destination)[0], "w:pgMar")
    assert margins is not None
    assert margins.get(q("top")) == str(STYLE.margins.top_twips)


# ---------------------------------------------------------------------------
# Page numbering
# ---------------------------------------------------------------------------


def test_numbering_follows_the_heading_boundary_and_restarts_once(tmp_path: Path) -> None:
    raw = build_raw(tmp_path / "raw.docx")
    destination = tmp_path / "final.docx"

    format.apply(raw, destination, style=STYLE)

    numbers = [find(section, "w:pgNumType") for section in sections_of(destination)]
    assert all(number is not None for number in numbers)
    numbers = [number for number in numbers if number is not None]

    assert [number.get(q("fmt")) for number in numbers] == [
        STYLE.header_footer.front_matter_number_format,
        STYLE.header_footer.body_number_format,
    ]
    # The restart belongs where the format changes and nowhere else: a start on
    # every section restarts the count at each section break.
    assert numbers[0].get(q("start")) is None
    assert numbers[1].get(q("start")) == "1"


# ---------------------------------------------------------------------------
# Body paragraphs
# ---------------------------------------------------------------------------


def test_body_metrics_go_on_the_paragraph_style(tmp_path: Path) -> None:
    raw = build_raw(tmp_path / "raw.docx")
    destination = tmp_path / "final.docx"

    format.apply(raw, destination, style=STYLE)

    styles = xml_of(destination, "word/styles.xml")
    definition = find(styles, "w:style", **{"w:styleId": "BodyText"})
    assert definition is not None
    properties = find(definition, "w:pPr")
    assert properties is not None
    indent = find(properties, "w:ind")
    spacing = find(properties, "w:spacing")
    assert indent is not None and indent.get(q("firstLine")) == str(
        STYLE.body.first_line_indent_twips
    )
    assert spacing is not None and spacing.get(q("line")) == str(STYLE.body.line_spacing_twips)

    for paragraph in paragraphs_with_style(destination, "BodyText"):
        own = find(paragraph, "w:pPr")
        assert find(own, "w:ind") is None, "the paragraph must inherit, not repeat"
        assert find(own, "w:spacing") is None


def test_the_created_style_properties_precede_the_run_properties(tmp_path: Path) -> None:
    """``BodyText`` has run properties and no paragraph properties: the shape that
    turns a blind append into a document Word calls corrupt."""
    raw = build_raw(tmp_path / "raw.docx")
    destination = tmp_path / "final.docx"

    format.apply(raw, destination, style=STYLE)

    styles = xml_of(destination, "word/styles.xml")
    definition = find(styles, "w:style", **{"w:styleId": "BodyText"})
    assert definition is not None
    names = [local_name(child.tag) for child in definition]
    assert "pPr" in names and "rPr" in names
    assert names.index("pPr") < names.index("rPr")


def test_body_metrics_fall_back_to_the_paragraph_when_the_style_is_missing(tmp_path: Path) -> None:
    """A dangling ``w:pStyle``. The checker still counts the paragraph as body
    text, so the value has to land somewhere that actually applies."""
    raw = build_raw(tmp_path / "raw.docx", styles=STYLES_WITHOUT_BODY)
    styles_before = parts_of(raw)["word/styles.xml"]

    destination = tmp_path / "final.docx"
    format.apply(raw, destination, style=STYLE)

    paragraph = paragraphs_with_style(destination, "BodyText")[0]
    properties = find(paragraph, "w:pPr")
    assert properties is not None
    indent = find(properties, "w:ind")
    spacing = find(properties, "w:spacing")
    assert indent is not None and indent.get(q("firstLine")) == str(
        STYLE.body.first_line_indent_twips
    )
    assert spacing is not None and spacing.get(q("line")) == str(STYLE.body.line_spacing_twips)

    # Nothing to put a style-level value on, so the styles part is not touched at
    # all — and the gate still passes, which is the part that matters.
    assert parts_of(destination)["word/styles.xml"] == styles_before
    assert evaluate(destination, config=config_for(tmp_path)).ok


def test_a_direct_override_is_corrected_on_the_paragraph(tmp_path: Path) -> None:
    """The style is right and the paragraph overrides it. Word renders the direct
    value, so correcting only the style would leave the page wrong while making the
    diff look right."""
    body = "\n".join(
        [
            _section_break("A cover paragraph.", style="CoverTitle"),
            paragraph_xml("Chapter One", style="Heading1"),
            _overridden_paragraph("An overridden paragraph.", style="BodyText", first_line=240),
            "<w:sectPr/>",
        ]
    )
    document = build_docx(tmp_path / "raw.docx", body=body, styles=STYLES)
    destination = tmp_path / "final.docx"

    format.apply(document, destination, style=STYLE)

    paragraph = paragraphs_with_style(destination, "BodyText")[0]
    properties = find(paragraph, "w:pPr")
    assert properties is not None
    indent = find(properties, "w:ind")
    assert indent is not None
    assert indent.get(q("firstLine")) == str(STYLE.body.first_line_indent_twips)


def test_front_matter_is_not_reformatted(tmp_path: Path) -> None:
    """The cover is not body text, and a direct value on it is left exactly as it
    was found — including a wrong one."""
    body = "\n".join(
        [
            _section_break("A cover paragraph.", style="BodyText", first_line=0),
            paragraph_xml("Chapter One", style="Heading1"),
            paragraph_xml("Body text after the heading.", style="BodyText"),
            "<w:sectPr/>",
        ]
    )
    document = build_docx(tmp_path / "raw.docx", body=body, styles=STYLES)
    destination = tmp_path / "final.docx"

    format.apply(document, destination, style=STYLE)

    cover = body_paragraphs_of(destination)[0]
    cover_properties = find(cover, "w:pPr")
    assert cover_properties is not None
    cover_indent = find(cover_properties, "w:ind")
    assert cover_indent is not None
    assert cover_indent.get(q("firstLine")) == "0"

    # The paragraph after the heading did get formatted, so the assertion above is
    # about the boundary rather than about a stage that did nothing. It shares the
    # cover's style, which is why the cover's own direct value had to be left be.
    paragraph = paragraphs_with_style(destination, "BodyText")[-1]
    assert paragraph is not cover
    converted = find(paragraph, "w:pPr")
    assert converted is not None
    assert find(converted, "w:ind") is None
    styles = xml_of(destination, "word/styles.xml")
    definition = find(styles, "w:style", **{"w:styleId": "BodyText"})
    assert definition is not None
    assert find(find(definition, "w:pPr"), "w:ind").get(q("firstLine")) == str(
        STYLE.body.first_line_indent_twips
    )


# ---------------------------------------------------------------------------
# Three-line tables
# ---------------------------------------------------------------------------


def test_every_table_becomes_a_three_line_table(tmp_path: Path) -> None:
    raw = build_raw(tmp_path / "raw.docx")
    before = tables_in(xml_of(raw, "word/document.xml"))
    assert before and not any(is_three_line_table(table, STYLE.table) for table in before)

    destination = tmp_path / "final.docx"
    format.apply(raw, destination, style=STYLE)

    after = tables_in(xml_of(destination, "word/document.xml"))
    assert len(after) == len(before)
    assert all(is_three_line_table(table, STYLE.table) for table in after)


def test_table_border_sizes_come_from_the_specification(tmp_path: Path) -> None:
    """Non-default sizes, so a literal in the stage could not pass this."""
    table_spec = TableSpec(
        outer_size_eighths=8, header_size_eighths=4, inner_border="none", vertical_border="none"
    )
    style = DEFAULT_STYLE.merged(
        header_footer=HeaderFooterSpec(odd_page_text=RUNNING_HEAD), table=table_spec
    )
    raw = build_raw(tmp_path / "raw.docx")
    destination = tmp_path / "final.docx"

    format.apply(raw, destination, style=style)

    table = tables_in(xml_of(destination, "word/document.xml"))[0]
    assert is_three_line_table(table, table_spec)
    properties = find(table, "w:tblPr")
    assert properties is not None
    borders = find(properties, "w:tblBorders")
    assert borders is not None
    for name in ("top", "bottom"):
        edge = find(borders, f"w:{name}")
        assert edge is not None and edge.get(q("sz")) == str(table_spec.outer_size_eighths)

    header = header_row(table)
    assert header is not None
    cell = next(iter_cells(header))
    cell_properties = find(cell, "w:tcPr")
    assert cell_properties is not None
    cell_borders = find(cell_properties, "w:tcBorders")
    assert cell_borders is not None
    rule = find(cell_borders, "w:bottom")
    assert rule is not None and rule.get(q("sz")) == str(table_spec.header_size_eighths)


# ---------------------------------------------------------------------------
# Running heads and footers
# ---------------------------------------------------------------------------


def test_the_header_part_is_created_with_the_running_head_and_its_rule(tmp_path: Path) -> None:
    raw = build_raw(tmp_path / "raw.docx")
    assert "word/header1.xml" not in parts_of(raw)

    destination = tmp_path / "final.docx"
    format.apply(raw, destination, style=STYLE)

    header = xml_of(destination, "word/header1.xml")
    assert RUNNING_HEAD in text_of(header)
    assert has_bottom_rule(header)


def test_the_footer_carries_the_page_field_at_the_configured_size(tmp_path: Path) -> None:
    """10.5 pt, i.e. 21 half-points: a stage that wrote the field's default size
    would produce 18 and fail here."""
    style = DEFAULT_STYLE.merged(
        header_footer=HeaderFooterSpec(odd_page_text=RUNNING_HEAD, footer_size_pt=10.5)
    )
    raw = build_raw(tmp_path / "raw.docx")
    destination = tmp_path / "final.docx"

    format.apply(raw, destination, style=style)

    footer = xml_of(destination, "word/footer1.xml")
    assert "PAGE" in " ".join(iter_instructions(footer))
    # Every run of the field, cached result included, so the folio Word renders
    # after a refresh is the size the page already shows.
    assert set(run_sizes(footer)) == {str(style.header_footer.footer_size_half_points)}
    assert evaluate(destination, config=config_for(tmp_path, style=style)).ok


def test_the_even_header_carries_a_styleref_for_the_heading_style(tmp_path: Path) -> None:
    raw = build_raw(tmp_path / "raw.docx")
    destination = tmp_path / "final.docx"

    format.apply(raw, destination, style=STYLE)

    even = xml_of(destination, even_header_part(destination))
    assert contains_field(even, f'STYLEREF "{STYLE.heading_styles[0]}"')
    # The gate asks the rule of every header part, this one included.
    assert has_bottom_rule(even)


def test_no_even_header_is_created_when_the_specification_says_so(tmp_path: Path) -> None:
    style = DEFAULT_STYLE.merged(
        header_footer=HeaderFooterSpec(odd_page_text=RUNNING_HEAD, even_page_uses_heading=False)
    )
    raw = build_raw(tmp_path / "raw.docx")
    destination = tmp_path / "final.docx"

    format.apply(raw, destination, style=style)

    headers = {name for name in parts_of(destination) if name.startswith("word/header")}
    assert headers == {"word/header1.xml"}
    assert all(
        reference_of(section, "w:headerReference", "even") is None
        for section in sections_of(destination)
    )


def test_no_rule_is_drawn_when_the_specification_says_so(tmp_path: Path) -> None:
    style = DEFAULT_STYLE.merged(
        header_footer=HeaderFooterSpec(odd_page_text=RUNNING_HEAD, rule_below_header=False)
    )
    raw = build_raw(tmp_path / "raw.docx")
    destination = tmp_path / "final.docx"

    format.apply(raw, destination, style=style)

    header = xml_of(destination, "word/header1.xml")
    assert RUNNING_HEAD in text_of(header)
    assert not has_bottom_rule(header)


def test_every_section_references_the_parts_as_default(tmp_path: Path) -> None:
    raw = build_raw(tmp_path / "raw.docx")
    destination = tmp_path / "final.docx"

    format.apply(raw, destination, style=STYLE)

    with Package(destination) as package:
        targets = {
            relationship.get("Id"): relationship.get("Target")
            for relationship in package.relationships("word/document.xml")
        }
        overrides = {
            child.get("PartName"): child.get("ContentType")
            for child in package.xml(content_types_part())
            if child.tag == qn("ct:Override")
        }

    sections = sections_of(destination)
    assert len(sections) == 2
    for section in sections:
        header = reference_of(section, "w:headerReference", "default")
        footer = reference_of(section, "w:footerReference", "default")
        assert header is not None and targets[header.get(qn("r:id"))] == "header1.xml"
        assert footer is not None and targets[footer.get(qn("r:id"))] == "footer1.xml"

    assert overrides["/word/header1.xml"] == CONTENT_TYPE["header"]
    assert overrides["/word/footer1.xml"] == CONTENT_TYPE["footer"]


def test_an_existing_header_is_rewritten_rather_than_duplicated(tmp_path: Path) -> None:
    """A document that already has a running head keeps exactly one header part.

    Reused, not replaced: the gate reads header parts through the document's
    relationships, so a fresh part would not take the old one out of scope — it
    would add a second part that also has to conform.
    """
    stale_header = part_xml(
        root="w:hdr",
        namespaces={"w": W},
        body='<w:p><w:r><w:t xml:space="preserve">STALE RUNNING HEAD</w:t></w:r></w:p>',
    )
    stale_footer = part_xml(
        root="w:ftr",
        namespaces={"w": W},
        body='<w:p><w:r><w:t xml:space="preserve">STALE FOOTER</w:t></w:r></w:p>',
    )
    body = "\n".join(
        [
            _section_break("A cover paragraph.", style="CoverTitle"),
            paragraph_xml("Chapter One", style="Heading1"),
            paragraph_xml("Body text.", style="BodyText"),
            "<w:sectPr>"
            '<w:headerReference w:type="default" r:id="rIdStaleHeader"/>'
            '<w:footerReference w:type="default" r:id="rIdStaleFooter"/>'
            "</w:sectPr>",
        ]
    )
    document = build_docx(
        tmp_path / "stale.docx",
        body=body,
        styles=STYLES,
        extra_parts={
            "word/header1.xml": stale_header.encode("utf-8"),
            "word/footer1.xml": stale_footer.encode("utf-8"),
        },
        extra_overrides={
            "/word/header1.xml": CONTENT_TYPE["header"],
            "/word/footer1.xml": CONTENT_TYPE["footer"],
        },
        document_relationships=[
            ("rIdStaleHeader", "header", "header1.xml"),
            ("rIdStaleFooter", "footer", "footer1.xml"),
        ],
    )
    destination = tmp_path / "final.docx"

    format.apply(document, destination, style=STYLE)

    headers = sorted(name for name in parts_of(destination) if name.startswith("word/header"))
    assert headers == ["word/header1.xml", "word/header2.xml"]

    header = xml_of(destination, "word/header1.xml")
    assert RUNNING_HEAD in text_of(header)
    assert "STALE RUNNING HEAD" not in text_of(header)


# ---------------------------------------------------------------------------
# Document settings
# ---------------------------------------------------------------------------


def test_update_fields_and_even_odd_headers_are_declared_when_settings_exist(
    tmp_path: Path,
) -> None:
    settings = part_xml(root="w:settings", namespaces={"w": W}, body="")
    body = "\n".join(
        [
            _section_break("A cover paragraph.", style="CoverTitle"),
            paragraph_xml("Chapter One", style="Heading1"),
            paragraph_xml("Body text.", style="BodyText"),
            "<w:sectPr/>",
        ]
    )
    document = build_docx(
        tmp_path / "with-settings.docx",
        body=body,
        styles=STYLES,
        extra_parts={"word/settings.xml": settings.encode("utf-8")},
        extra_overrides={"/word/settings.xml": CONTENT_TYPE["settings"]},
        document_relationships=[("rIdSettings", "settings", "settings.xml")],
    )
    destination = tmp_path / "final.docx"

    format.apply(document, destination, style=STYLE)

    written = xml_of(destination, "word/settings.xml")
    assert update_fields_enabled(written) is True
    # Without this flag Word prints the default header on every page, and the
    # chapter title in the even header never appears.
    assert find(written, "w:evenAndOddHeaders") is not None


def test_a_settings_part_is_created_when_the_even_header_needs_one(tmp_path: Path) -> None:
    """The settings part is created rather than assumed to exist.

    This test asserts the reverse of what it asserted before, and the reversal is
    the point. An earlier revision refused to create a settings part, on the
    grounds that a stage should not invent values it has no facts about. That is
    right about the *other* children of the part — the compatibility mode, the
    default tab stop, the theme language — and wrong about these two flags, because
    they have nowhere else to live.

    The cost of the refusal was measurable: on a document without a settings part,
    the stage created and referenced an even header that Word then ignored, so
    `style.header_footer.even_page_uses_heading` was a configuration value with no
    effect. A stage whose own output does not do what its specification says is
    worse than a stage that writes two flags into a part it created.
    """
    raw = build_raw(tmp_path / "raw.docx")
    assert "word/settings.xml" not in parts_of(raw)

    destination = tmp_path / "final.docx"
    format.apply(raw, destination, style=STYLE)

    parts = parts_of(destination)
    assert "word/settings.xml" in parts

    written = xml_of(destination, "word/settings.xml")
    assert find(written, "w:evenAndOddHeaders") is not None, (
        "the even header is referenced, so the flag that makes Word use it must be set"
    )

    # A part this stage is the first to write needs its own namespace declaration
    # and must be reachable from the document, or Word will not read it.
    assert b"xmlns:w=" in parts["word/settings.xml"]
    with Package(destination) as package:
        assert package.find_relationship("word/document.xml", "settings") is not None
        assert b"settings.xml" in parts["[Content_Types].xml"]


# ---------------------------------------------------------------------------
# Defect regressions
# ---------------------------------------------------------------------------
# Each of these asserts the behaviour that replaced a defect. They are written so
# that restoring the old behaviour fails them, which is the only way a regression
# test is worth having.


def build_two_chapters(path: Path, *, styles: str = STYLES) -> Path:
    """Two chapters, each closing its own section.

    One chapter per section is the shape a real thesis has once the front matter has
    been imported, and it is the only shape in which a per-section cached running
    head can be exact. A single-section document with several chapters is the shape
    that cannot be exact, and it is covered separately.
    """
    body = "\n".join(
        [
            _section_break("A cover paragraph.", style="CoverTitle"),
            paragraph_xml("Chapter One", style="Heading1"),
            paragraph_xml("Body of chapter one.", style="BodyText"),
            _section_break("Chapter Two", style="Heading1"),
            paragraph_xml("Body of chapter two.", style="BodyText"),
            "<w:sectPr/>",
        ]
    )
    return build_docx(path, body=body, styles=styles)


def build_single_section(path: Path, *, styles: str = STYLES) -> Path:
    """One section holding a whole document: no cover, so no front matter."""
    body = "\n".join(
        [
            paragraph_xml("Chapter One", style="Heading1"),
            paragraph_xml("Body text.", style="BodyText"),
            "<w:sectPr/>",
        ]
    )
    return build_docx(path, body=body, styles=styles)


def _relationship_targets(package: Package) -> dict[str, str]:
    """Relationship id → part name, with the document-relative targets resolved.

    A header target is written relative to ``word/`` (``header2.xml``), so it has to
    be turned into a part name before it can be compared with one.
    """
    known = set(package.names())
    resolved: dict[str, str] = {}
    for relationship in package.relationships("word/document.xml"):
        target = relationship.get("Target", "")
        resolved[relationship.get("Id")] = target if target in known else f"word/{target}"
    return resolved


def even_header_targets(path: Path) -> list[str | None]:
    """The ``even`` header part each section references, in document order."""
    with Package(path) as package:
        targets = _relationship_targets(package)
        document = package.xml("word/document.xml")
        found: list[str | None] = []
        for section in iter_descendants(document, "w:sectPr"):
            reference = find(section, "w:headerReference", **{"w:type": "even"})
            if reference is None:
                found.append(None)
                continue
            found.append(targets.get(reference.get(qn("r:id"))))
        return found


def header_texts(path: Path) -> dict[str, str]:
    """The text of every header part, keyed by part name."""
    with Package(path) as package:
        return {
            name: text_of(package.xml(name))
            for name in package.names()
            if name.startswith("word/header")
        }


def test_each_chapter_gets_its_own_cached_even_header_title(tmp_path: Path) -> None:
    """A document-wide cache shows chapter one on every chapter's even pages.

    The cached result of a ``STYLEREF`` is what a reader sees before Word refreshes
    anything, and a thesis is often read before it is ever opened in Word — a
    supervisor reading a PDF, a committee member skimming a printout. One cache for
    the whole document is therefore not a harmless approximation.
    """
    raw = build_two_chapters(tmp_path / "raw.docx")
    destination = tmp_path / "final.docx"
    format.apply(raw, destination, style=STYLE)

    referenced = even_header_targets(destination)
    assert len(referenced) == 3, "the fixture has three sections"
    assert all(target is not None for target in referenced), "every section got an even header"

    texts = header_texts(destination)
    cached = {target: texts[target] for target in referenced if target is not None}

    assert len(set(referenced)) == 2, (
        "two chapters must produce two even header parts; one part means one cache "
        f"for the whole document (got {referenced})"
    )
    assert set(cached.values()) == {"Chapter One", "Chapter Two"}, cached

    # The section that holds chapter two must point at chapter two's part.
    assert texts[referenced[2]] == "Chapter Two"
    assert texts[referenced[1]] == "Chapter One"


def test_a_single_section_document_takes_the_body_side_margins(tmp_path: Path) -> None:
    """`body_side_mm` was unreachable: a one-section thesis took the cover's gutter.

    The wider front-matter gutter exists for a cover that was imported as a separate
    section. A document with one section has no such cover, so taking the value
    anyway made the configuration key a setting with no effect — and the effect was
    invisible, because the gate accepts either margin.
    """
    raw = build_single_section(tmp_path / "raw.docx")
    destination = tmp_path / "final.docx"
    format.apply(raw, destination, style=STYLE)

    document = xml_of(destination, "word/document.xml")
    section = next(iter_descendants(document, "w:sectPr"))
    margins = find(section, "w:pgMar")

    assert margins is not None
    assert margins.get(qn("w:left")) == str(STYLE.margins.body_side_twips)
    assert margins.get(qn("w:right")) == str(STYLE.margins.body_side_twips)


def test_a_document_with_a_front_matter_section_still_takes_the_cover_margins(
    tmp_path: Path,
) -> None:
    """The control: the fix must not remove the gutter from documents that have a cover."""
    raw = build_raw(tmp_path / "raw.docx")
    destination = tmp_path / "final.docx"
    format.apply(raw, destination, style=STYLE)

    document = xml_of(destination, "word/document.xml")
    sections = list(iter_descendants(document, "w:sectPr"))
    assert len(sections) == 2

    first = find(sections[0], "w:pgMar")
    second = find(sections[1], "w:pgMar")
    assert first is not None and second is not None
    assert first.get(qn("w:left")) == str(STYLE.margins.front_matter_side_twips)
    assert second.get(qn("w:left")) == str(STYLE.margins.body_side_twips)


def test_a_stray_even_and_odd_flag_on_a_section_is_removed(tmp_path: Path) -> None:
    """The flag belongs in settings; a copy on a section is invalid WordprocessingML.

    A document carrying one is the shape of an earlier tool's mistake. Leaving it
    would keep the invalidity in the file while the real flag did the work, so the
    two would disagree the moment anybody looked.
    """
    raw = tmp_path / "raw.docx"
    body = "\n".join(
        [
            paragraph_xml("Chapter One", style="Heading1"),
            paragraph_xml("Body text.", style="BodyText"),
            "<w:sectPr><w:evenAndOddHeaders/></w:sectPr>",
        ]
    )
    build_docx(raw, body=body, styles=STYLES)

    destination = tmp_path / "final.docx"
    format.apply(raw, destination, style=STYLE)

    document = xml_of(destination, "word/document.xml")
    for section in iter_descendants(document, "w:sectPr"):
        assert find(section, "w:evenAndOddHeaders") is None

    settings = xml_of(destination, "word/settings.xml")
    assert find(settings, "w:evenAndOddHeaders") is not None
