"""Tests for docxlib.tables, especially the three-line convention."""

from __future__ import annotations

from lxml import etree

from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.parse import parse
from thesis_refinery.docxlib.synth import (
    DOCUMENT_NAMESPACES,
    paragraph_xml,
    parse_fragment,
    part_xml,
    table_xml,
)
from thesis_refinery.docxlib.tables import (
    apply_three_line_borders,
    cell_borders,
    clear_direct_cell_borders,
    header_row,
    is_three_line_table,
    iter_cells,
    iter_rows,
    set_paragraph_bottom_border,
    tables_in,
)
from thesis_refinery.docxlib.xmlq import find, findall, iter_descendants
from thesis_refinery.style import TableSpec

SPEC = TableSpec()


def document(body: str) -> etree._Element:
    return parse(
        part_xml(
            root="w:document",
            namespaces=DOCUMENT_NAMESPACES,
            body=f"<w:body>{body}</w:body>",
        )
    )


def a_table(rows=None) -> etree._Element:
    rows = rows or [["Head A", "Head B"], ["1", "2"], ["3", "4"]]
    return next(iter_descendants(document(table_xml(rows)), "w:tbl"))


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------


def test_tables_in_finds_nested_tables_too() -> None:
    """A table inside a cell is still a table, and must be found and fixed."""
    outer = a_table()
    cell = next(iter_cells(next(iter_rows(outer))))

    from thesis_refinery.docxlib.xmlq import ensure_ordered

    cell_properties = ensure_ordered(cell, "w:tcPr")
    inner = parse_fragment(table_xml([["nested"]]))
    cell.insert(list(cell).index(cell_properties) + 1, inner)

    found = tables_in(outer.getroottree().getroot())

    assert len(found) == 2
    assert inner in found


def test_iter_rows_skips_nested_table_rows() -> None:
    """Counting a nested table's rows as this table's is how 1 row reports 3."""
    outer = a_table()
    cell = next(iter_cells(next(iter_rows(outer))))
    cell.append(parse_fragment(table_xml([["nested a"], ["nested b"]])))

    assert len(list(iter_rows(outer))) == 3


def test_iter_cells_returns_the_cells_of_a_row() -> None:
    row = next(iter_rows(a_table()))
    assert len(list(iter_cells(row))) == 2


def test_tables_in_is_empty_for_a_document_without_tables() -> None:
    assert tables_in(document(paragraph_xml("no tables"))) == []


# ---------------------------------------------------------------------------
# Header row detection
# ---------------------------------------------------------------------------


def test_header_row_prefers_the_explicit_marker() -> None:
    table = a_table()
    rows = list(iter_rows(table))
    # Move the marker to the second row: it must win over "just take the first".
    first_properties = find(rows[0], "w:trPr")
    assert first_properties is not None
    first_properties.getparent().remove(first_properties)
    second = etree.SubElement(rows[1], q("trPr"))
    etree.SubElement(second, q("tblHeader"))

    assert header_row(table) is rows[1]


def test_header_row_falls_back_to_the_first_row() -> None:
    table = a_table()
    rows = list(iter_rows(table))
    for row in rows:
        properties = find(row, "w:trPr")
        if properties is not None:
            properties.getparent().remove(properties)

    assert header_row(table) is rows[0]


def test_header_row_of_an_empty_table_is_none() -> None:
    table = etree.Element(q("tbl"))
    assert header_row(table) is None


# ---------------------------------------------------------------------------
# Applying three-line borders
# ---------------------------------------------------------------------------


def test_a_freshly_applied_table_satisfies_the_specification() -> None:
    table = a_table()
    assert not is_three_line_table(table, SPEC)

    apply_three_line_borders(table, SPEC)

    assert is_three_line_table(table, SPEC)


def test_heavy_rules_are_on_the_outside_and_light_on_the_header() -> None:
    table = a_table()
    apply_three_line_borders(table, SPEC)

    borders = find(find(table, "w:tblPr"), "w:tblBorders")
    assert find(borders, "w:top").get(q("sz")) == "12"
    assert find(borders, "w:bottom").get(q("sz")) == "12"

    header = header_row(table)
    header_cell_rule = find(find(find(header, "w:tc"), "w:tcPr"), "w:tcBorders")
    assert find(header_cell_rule, "w:bottom").get(q("sz")) == "6"


def test_there_are_no_vertical_or_interior_rules() -> None:
    table = a_table()
    apply_three_line_borders(table, SPEC)

    borders = find(find(table, "w:tblPr"), "w:tblBorders")
    for name in ("left", "right", "insideH", "insideV"):
        assert find(borders, f"w:{name}").get(q("val")) == "nil"


def test_cell_level_borders_are_cleared_first() -> None:
    """A cell rule beats the table rule, so it would survive the next step."""
    table = a_table()
    for row in iter_rows(table):
        for cell in iter_cells(row):
            borders = cell_borders(cell)
            for name in ("left", "right", "top"):
                element = etree.SubElement(borders, q(name))
                element.set(q("val"), "single")
                element.set(q("sz"), "8")

    assert clear_direct_cell_borders(table) == 6

    apply_three_line_borders(table, SPEC)
    assert is_three_line_table(table, SPEC)


def test_only_the_header_row_keeps_a_cell_rule() -> None:
    table = a_table()
    apply_three_line_borders(table, SPEC)

    rows = list(iter_rows(table))
    header_index = rows.index(header_row(table))

    for index, row in enumerate(rows):
        for cell in iter_cells(row):
            properties = find(cell, "w:tcPr")
            rules = find(properties, "w:tcBorders") if properties is not None else None
            if index == header_index:
                assert rules is not None
            else:
                assert rules is None


def test_applying_twice_is_idempotent() -> None:
    table = a_table()
    apply_three_line_borders(table, SPEC)
    first = etree.tostring(table)

    apply_three_line_borders(table, SPEC)

    assert etree.tostring(table) == first


def test_a_table_without_rows_still_gets_outer_rules() -> None:
    table = etree.Element(q("tbl"))
    apply_three_line_borders(table, SPEC)

    borders = find(find(table, "w:tblPr"), "w:tblBorders")
    assert find(borders, "w:top") is not None
    assert find(borders, "w:bottom") is not None


def test_a_nil_border_carries_no_size() -> None:
    """A sized nil border is inconsistent, and some readers draw it anyway."""
    table = a_table()
    apply_three_line_borders(table, SPEC)

    borders = find(find(table, "w:tblPr"), "w:tblBorders")
    left = find(borders, "w:left")
    assert left.get(q("val")) == "nil"
    assert left.get(q("sz")) is None


def test_is_three_line_rejects_a_table_without_borders() -> None:
    assert not is_three_line_table(a_table(), SPEC)


def test_is_three_line_rejects_a_stray_vertical_cell_rule() -> None:
    table = a_table()
    apply_three_line_borders(table, SPEC)

    row = list(iter_rows(table))[1]
    borders = cell_borders(next(iter_cells(row)))
    element = etree.SubElement(borders, q("left"))
    element.set(q("val"), "single")

    assert not is_three_line_table(table, SPEC)


# ---------------------------------------------------------------------------
# Case counts
# ---------------------------------------------------------------------------


def test_clear_direct_cell_borders_reports_what_it_changed() -> None:
    table = a_table()
    assert clear_direct_cell_borders(table) == 0

    for row in iter_rows(table):
        for cell in iter_cells(row):
            cell_borders(cell)

    assert clear_direct_cell_borders(table) == 6


# ---------------------------------------------------------------------------
# Paragraph rules
# ---------------------------------------------------------------------------


def test_paragraph_bottom_border_is_created_in_schema_order() -> None:
    paragraph = parse_fragment(paragraph_xml("a running head"))
    set_paragraph_bottom_border(paragraph)

    properties = find(paragraph, "w:pPr")
    assert properties is not None
    assert [child.tag for child in properties] == [q("pBdr")]

    bottom = find(find(properties, "w:pBdr"), "w:bottom")
    assert bottom.get(q("val")) == "single"
    assert bottom.get(q("sz")) == "6"


def test_paragraph_bottom_border_is_applied_once() -> None:
    paragraph = parse_fragment(paragraph_xml("a running head"))
    set_paragraph_bottom_border(paragraph, size_eighths=6)
    set_paragraph_bottom_border(paragraph, size_eighths=12)

    borders = find(find(paragraph, "w:pPr"), "w:pBdr")
    assert len(findall(borders, "w:bottom")) == 1
    assert find(borders, "w:bottom").get(q("sz")) == "12"


def test_paragraph_border_coexists_with_an_indent_in_schema_order() -> None:
    """pPr is sequence-typed: pBdr comes before ind, whatever order they are set."""
    paragraph = parse_fragment(paragraph_xml("running head"))
    set_paragraph_bottom_border(paragraph)

    from thesis_refinery.docxlib.xmlq import ensure_ordered

    ensure_ordered(find(paragraph, "w:pPr"), "w:ind", **{"w:firstLine": "480"})

    names = [child.tag for child in find(paragraph, "w:pPr")]
    assert names == [q("pBdr"), q("ind")]
