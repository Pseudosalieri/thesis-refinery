"""Tables, and the three-line table convention.

A Chinese thesis table is a *three-line table* (三线表): one heavy rule above the
header row, one light rule below it, one heavy rule at the foot, and no vertical
or interior rules at all. Getting there means touching borders at two levels —
``w:tblBorders`` for the table and ``w:tcBorders`` for individual cells — and the
cell level always wins, so a cell that carries its own borders will ignore the
table's.

The predecessor applied this across ~100 lines of inline XML building in two
separate stages and then re-typed the same border sizes as literals in the
auditing script. Here the sizes come from
:class:`~thesis_refinery.style.TableSpec` and the traversal is in one place.
"""

from __future__ import annotations

from collections.abc import Iterator

from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.xmlq import (
    Element,
    ensure_ordered,
    find,
    iter_descendants,
    local_name,
    new,
    ordered_insert,
    remove,
)
from thesis_refinery.style import TableSpec

__all__ = [
    "apply_three_line_borders",
    "cell_borders",
    "clear_direct_cell_borders",
    "header_row",
    "is_three_line_table",
    "iter_cells",
    "iter_rows",
    "set_paragraph_bottom_border",
    "tables_in",
]

# The order borders are declared in, per CT_TblBorders / CT_TcBorders. Both
# spellings of the left/right rule are listed; a document may contain either.
_EDGE_NAMES = ("top", "left", "start", "bottom", "right", "end", "insideH", "insideV", "tl2br", "tr2bl")


def tables_in(element: Element) -> list[Element]:
    """Every ``w:tbl`` at any depth."""
    return list(iter_descendants(element, "w:tbl"))


def iter_rows(table: Element) -> Iterator[Element]:
    """Direct ``w:tr`` children of a table.

    Nested tables are skipped: their rows belong to the nested table, and
    counting them as this table's rows is how a table reports three rows when it
    has one.
    """
    for child in table:
        if child.tag == q("tr"):
            yield child


def iter_cells(row: Element) -> Iterator[Element]:
    """Direct ``w:tc`` children of a row."""
    for child in row:
        if child.tag == q("tc"):
            yield child


def header_row(table: Element) -> Element | None:
    """The row Word would repeat on a page break, else the first row.

    A row is a header row if it is marked ``w:tblHeader``. Falling back to the
    first row matches the convention, but only after looking for the explicit
    marker, because a table that repeats a *data* row on every page is a real
    and visible defect.
    """
    rows = list(iter_rows(table))
    for row in rows:
        properties = find(row, "w:trPr")
        if properties is not None and find(properties, "w:tblHeader") is not None:
            return row
    return rows[0] if rows else None


def cell_borders(cell: Element) -> Element:
    """The cell's ``w:tcBorders``, creating it in schema order if absent."""
    properties = ensure_ordered(cell, "w:tcPr")
    return ensure_ordered(properties, "w:tcBorders")


def _set_border(
    borders: Element, name: str, *, style: str, size_eighths: int, space: int = 0
) -> Element:
    existing = find(borders, f"w:{name}")
    if existing is not None:
        element = existing
    else:
        element = new(f"w:{name}")
        ordered_insert(borders, element, on_unknown="append")

    element.set(q("val"), style)
    if style == "nil":
        # A nil border must not carry a size; Word treats a sized nil border as
        # inconsistent and some readers draw it anyway.
        for attribute in ("sz", "space", "color"):
            if element.get(q(attribute)) is not None:
                del element.attrib[q(attribute)]
        return element

    element.set(q("sz"), str(size_eighths))
    element.set(q("space"), str(space))
    element.set(q("color"), "auto")
    return element


def _set_table_borders(table: Element, spec: TableSpec) -> None:
    properties = ensure_ordered(table, "w:tblPr")
    borders = ensure_ordered(properties, "w:tblBorders")

    for name in _EDGE_NAMES:
        existing = find(borders, f"w:{name}")
        if existing is not None:
            remove(existing)

    _set_border(borders, "top", style="single", size_eighths=spec.outer_size_eighths)
    _set_border(borders, "bottom", style="single", size_eighths=spec.outer_size_eighths)
    _set_border(borders, "left", style=spec.vertical_border, size_eighths=0)
    _set_border(borders, "right", style=spec.vertical_border, size_eighths=0)
    _set_border(borders, "insideH", style=spec.inner_border, size_eighths=0)
    _set_border(borders, "insideV", style=spec.inner_border, size_eighths=0)


def clear_direct_cell_borders(table: Element) -> int:
    """Remove every cell-level border override in *table*.

    Cell borders beat table borders, so a table whose cells carry their own
    borders will not become a three-line table no matter what the table-level
    borders say. Returns the number of cells changed, which is what makes the
    difference between "applied" and "applied to something that listened".
    """
    changed = 0
    for row in iter_rows(table):
        for cell in iter_cells(row):
            properties = find(cell, "w:tcPr")
            if properties is None:
                continue
            borders = find(properties, "w:tcBorders")
            if borders is not None:
                remove(borders)
                changed += 1
    return changed


def apply_three_line_borders(table: Element, spec: TableSpec) -> None:
    """Turn *table* into a three-line table.

    Three steps, and all three are needed:

    1. Drop every cell-level border, because a cell-level rule beats the table's
       and would otherwise survive the next step.
    2. Give the table heavy top and bottom rules and no interior rules at all.
    3. Give the header row its own light bottom rule, since step 2 just removed
       every interior rule including the one under the header.

    Nothing is written to the other cells: with ``insideH`` set to nil there is
    no inherited rule for them to need suppressing, and empty border declarations
    are noise in every later diff.
    """
    header = header_row(table)

    clear_direct_cell_borders(table)
    _set_table_borders(table, spec)

    if header is None:
        return

    for cell in iter_cells(header):
        _set_border(
            cell_borders(cell),
            "bottom",
            style="single",
            size_eighths=spec.header_size_eighths,
        )


def is_three_line_table(table: Element, spec: TableSpec) -> bool:
    """True when *table* already satisfies *spec*.

    Reads only the borders, and reports on the table as a whole: a table where
    one cell carries a stray vertical rule is not a three-line table, even if the
    other borders are right.
    """
    properties = find(table, "w:tblPr")
    if properties is None:
        return False
    borders = find(properties, "w:tblBorders")
    if borders is None:
        return False

    if not _border_matches(borders, "top", "single", spec.outer_size_eighths):
        return False
    if not _border_matches(borders, "bottom", "single", spec.outer_size_eighths):
        return False
    for name in ("left", "right", "insideH", "insideV"):
        element = find(borders, f"w:{name}")
        if element is not None and element.get(q("val")) not in (None, "nil", "none"):
            return False

    for row in iter_rows(table):
        for cell in iter_cells(row):
            cell_properties = find(cell, "w:tcPr")
            if cell_properties is None:
                continue
            cell_rules = find(cell_properties, "w:tcBorders")
            if cell_rules is None:
                continue
            for border in cell_rules:
                vertical = local_name(border.tag) in ("left", "right", "insideV")
                if vertical and border.get(q("val")) not in (None, "nil", "none"):
                    return False
    return True


def _border_matches(borders: Element, name: str, style: str, size: int) -> bool:
    element = find(borders, f"w:{name}")
    if element is None:
        return False
    return element.get(q("val")) == style and element.get(q("sz")) == str(size)


def set_paragraph_bottom_border(
    paragraph: Element,
    *,
    size_eighths: int = 6,
    space: int = 1,
    style: str = "single",
) -> Element:
    """Give a paragraph a bottom rule, such as the line under a running head.

    This lives beside the table borders rather than in a module of its own
    because they are the same idea — a rule with a style, a size in eighths of a
    point, and a gap — and because the pipeline this came from built the same
    three lines of XML in two different stages at two different sizes.
    """
    properties = ensure_ordered(paragraph, "w:pPr")
    borders = ensure_ordered(properties, "w:pBdr")

    bottom = find(borders, "w:bottom")
    if bottom is None:
        bottom = new("w:bottom")
        ordered_insert(borders, bottom, on_unknown="append")

    bottom.set(q("val"), style)
    bottom.set(q("sz"), str(size_eighths))
    bottom.set(q("space"), str(space))
    bottom.set(q("color"), "auto")
    return bottom
