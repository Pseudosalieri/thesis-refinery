"""Tables: every one of them is a three-line table.

The check reads the table and compares against
:class:`~thesis_refinery.style.TableSpec` — the same object the stage that
*applied* the borders reads. Neither the sizes nor the traversal are duplicated,
which is the whole reason the specification lives in one place.

What is deliberately not shared is the traversal: the applier in
:mod:`thesis_refinery.docxlib.tables` walks the table its own way, and so does the
predicate this delegates to. If they shared a walk, a mistake in the walk would
cancel itself out and a broken table would be reported as fine.
"""

from __future__ import annotations

from collections.abc import Iterator

from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.tables import is_three_line_table
from thesis_refinery.docxlib.xmlq import Element, find, iter_descendants, local_name, text_of
from thesis_refinery.verify.checks import Check
from thesis_refinery.verify.context import Context
from thesis_refinery.verify.findings import Finding, failed, passed

CHECK = Check(
    name="tables",
    description="every table is a three-line table",
    run=lambda context: run(context),
)

_MAX_REPORTED = 10


def run(context: Context) -> Iterator[Finding]:
    tables = context.tables()
    if not tables:
        yield passed("tables.three-line", "no tables in the document")
        return

    spec = context.style.table
    offenders = [table for table in tables if not is_three_line_table(table, spec)]

    if offenders:
        yield failed(
            "tables.three-line",
            f"{len(offenders)} of {len(tables)} table(s) are not three-line tables "
            f"(outer {spec.outer_size_eighths}/8 pt, header {spec.header_size_eighths}/8 pt, "
            f"inner {spec.inner_border})",
            tuple(_describe(table, index) for index, table in enumerate(offenders[:_MAX_REPORTED])),
        )
        return

    yield passed("tables.three-line", f"all {len(tables)} table(s) are three-line tables")


def _describe(table: Element, index: int) -> str:
    first_cell = next(iter_descendants(table, "w:tc"), None)
    caption = ""
    if first_cell is not None:
        text = " ".join(text_of(first_cell).split())
        caption = f" first cell {text[:40]!r}" if text else ""

    properties = find(table, "w:tblPr")
    borders = find(properties, "w:tblBorders") if properties is not None else None
    if borders is None:
        declared = "no w:tblBorders"
    else:
        declared = ", ".join(
            f"{local_name(child.tag)}={child.get(q('val'))}" for child in borders
        )
    return f"table {index + 1}:{caption} borders: {declared or 'none'}"
