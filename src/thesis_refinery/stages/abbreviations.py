"""The abbreviation table, rebuilt from its markdown source.

Ported from a script that had been iterated on against a real thesis, and the
port keeps the three things that iteration bought:

**Locate the table by its header row.** The original pipeline assumed the
abbreviation table was the *second* table in the document, which is a fact about
one document's layout rather than about abbreviation tables. The header cells are
matched instead, with an explicit index as an escape hatch for the case where two
tables share a header.

**Parse the cell's inline markdown.** An abbreviation column carries emphasis:
a term is italic, a subscript is ``~x~``, a superscript ``^x^``. Writing the cell
text verbatim would put literal asterisks and tildes into a submitted thesis, so
the markup is turned into runs, and an unbalanced ``*`` is refused rather than
written.

**Keep the row's formatting, replace its text.** The first data row is the
template: cell properties and run properties come from it, so a table styled to
the specification stays styled after its contents are replaced.

What the port changes is the substrate, not the behaviour: this project's
:class:`~thesis_refinery.docxlib.pkgs.Package` and
:class:`~thesis_refinery.docxlib.xmlq` handle the XML, so run properties are placed
by the shared schema-order table instead of by a hand-copied ordering constant.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from thesis_refinery.config import ConfigError
from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.pkgs import Package
from thesis_refinery.docxlib.xmlq import (
    Element,
    children,
    deepcopy,
    find,
    findall,
    iter_descendants,
    new,
    ordered_insert,
    text_of,
)
from thesis_refinery.style import StyleSpec

__all__ = [
    "DOCUMENT_PART",
    "MARKUP_PATTERN",
    "OptionsError",
    "apply",
    "parse_markup",
    "read_markdown_table",
    "select_table",
    "table_header_cells",
]

DOCUMENT_PART = "word/document.xml"

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

MARKUP_PATTERN = re.compile(
    r"\*\*(?P<bold>[^*]+)\*\*"
    r"|\*(?P<italic>[^*]+)\*"
    r"|~(?P<sub>[^~\s]+)~"
    r"|\^(?P<sup>[^\^\s]+)\^"
)
"""Inline markdown understood inside an abbreviation cell.

``**bold**`` before ``*italic*``, because the second pattern would otherwise match
the first two characters of the first. ``~x~`` and ``^x^`` mirror the notation the
rest of this project uses for subscripts and superscripts.
"""

_KNOWN_OPTIONS = frozenset({"qmd", "columns", "table_index"})


class OptionsError(ConfigError):
    """Raised when the stage's ``options`` are missing or wrong.

    A subclass of :class:`~thesis_refinery.config.ConfigError` so the pipeline
    reports it as a configuration problem rather than an internal failure: the
    mistake is in ``thesis.yml``, and the message says which key.
    """


@dataclass(frozen=True)
class _Options:
    qmd: Path
    columns: tuple[str, ...]
    table_index: int | None


def _read_options(raw: Mapping[str, object] | None) -> _Options:
    """Validate this stage's options, naming the unknown ones.

    Each stage owns the validation of its own keys, because it is the only code
    that knows what they are. A ``thesis.yml`` key that a stage silently ignored
    would be a setting its author believes is in force.
    """
    given = dict(raw or {})
    unknown = sorted(set(given) - _KNOWN_OPTIONS)
    if unknown:
        raise OptionsError(
            f"unknown option(s) for the abbreviation stage: {', '.join(unknown)}.\n"
            f"Known: {', '.join(sorted(_KNOWN_OPTIONS))}"
        )

    missing = sorted({"qmd", "columns"} - set(given))
    if missing:
        raise OptionsError(
            f"the abbreviation stage needs {', '.join(missing)}.\n"
            "  stages:\n"
            "    abbreviations:\n"
            "      qmd: manuscript/chapters/08-abbreviations.qmd\n"
            "      columns: [缩略语, 英文全称, 中文全称]"
        )

    columns = given["columns"]
    if isinstance(columns, str) or not isinstance(columns, Sequence):
        raise OptionsError("stages.abbreviations.columns must be a list of column headings")

    index = given.get("table_index")
    if index is not None and not isinstance(index, int):
        raise OptionsError("stages.abbreviations.table_index must be an integer")

    return _Options(
        qmd=Path(str(given["qmd"])),
        columns=tuple(str(column) for column in columns),
        table_index=index,
    )


# ---------------------------------------------------------------------------
# The markdown side
# ---------------------------------------------------------------------------


def read_markdown_table(path: Path) -> tuple[list[str], list[tuple[str, ...]]]:
    """Return ``(header cells, data rows)`` of the first table in *path*.

    Only the **contiguous** block of ``|`` lines is read: from the first one to the
    first line that is not one. An earlier implementation scanned the whole file
    and treated every line with the right number of cells as an entry, so a second
    table further down was swallowed whole.

    Every row is validated against the header: the cell count must match, and no
    field may be empty. A row that fails is reported with its content, because the
    alternative is a silently short abbreviation list in a submitted thesis.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise OptionsError(f"cannot read the abbreviation source {path}: {exc}") from exc

    start = next((i for i, line in enumerate(lines) if line.lstrip().startswith("|")), None)
    if start is None:
        raise OptionsError(f"no markdown table found in {path}")

    block: list[list[str]] = []
    for line in lines[start:]:
        if not line.lstrip().startswith("|"):
            break
        block.append([cell.strip() for cell in line.strip().strip("|").split("|")])

    if len(block) < 2:
        raise OptionsError(f"the markdown table in {path} has no data rows")

    header = block[0]
    if not all(header):
        raise OptionsError(f"the abbreviation table has an empty header cell: {header!r}")

    rows: list[tuple[str, ...]] = []
    for cells in block[1:]:
        if all(cell and set(cell) <= {"-", ":"} for cell in cells):
            continue  # the |---|---| separator
        if len(cells) != len(header):
            raise OptionsError(
                f"abbreviation row has {len(cells)} cells, expected {len(header)}: {cells!r}"
            )
        if not all(cells):
            raise OptionsError(f"abbreviation row has an empty field: {cells!r}")
        rows.append(tuple(cells))

    if not rows:
        raise OptionsError(f"no abbreviation entries found in {path}")
    return header, rows


def parse_markup(text: str) -> list[tuple[str, bool, bool, str | None]]:
    """Split a cell value into ``(text, bold, italic, vertical alignment)`` runs.

    Text outside any marker is emitted verbatim, so an unmatched ``~`` or ``^``
    stays literal rather than disappearing. An odd number of ``*`` is a broken
    emphasis marker, and it is refused instead of being written into the thesis:
    the reader would see the asterisks, and nobody would know they were a mistake.
    """
    if text.count("*") % 2:
        raise OptionsError(f"unbalanced '*' emphasis marker in an abbreviation cell: {text!r}")

    segments: list[tuple[str, bool, bool, str | None]] = []
    position = 0
    for match in MARKUP_PATTERN.finditer(text):
        if match.start() > position:
            segments.append((text[position : match.start()], False, False, None))
        if match.group("bold") is not None:
            segments.append((match.group("bold"), True, False, None))
        elif match.group("italic") is not None:
            segments.append((match.group("italic"), False, True, None))
        elif match.group("sub") is not None:
            segments.append((match.group("sub"), False, False, "subscript"))
        else:
            segments.append((match.group("sup"), False, False, "superscript"))
        position = match.end()

    if position < len(text):
        segments.append((text[position:], False, False, None))
    return [segment for segment in segments if segment[0]]


# ---------------------------------------------------------------------------
# The document side
# ---------------------------------------------------------------------------


def table_header_cells(table: Element) -> list[str]:
    """The text of the table's first row, one entry per cell."""
    rows = findall(table, "w:tr")
    if not rows:
        return []
    return [text_of(cell).strip() for cell in findall(rows[0], "w:tc")]


def select_table(
    tables: Sequence[Element], columns: Sequence[str], index: int | None
) -> Element:
    """The table whose header row matches *columns*.

    The header is matched rather than an index assumed, because "the abbreviation
    table is the second table" is a fact about one document's layout. The index is
    kept as an explicit escape hatch for a document with two tables sharing a
    header, and it is the caller's statement of intent rather than a guess.

    Raising when nothing matches is the point: a stage that locates no table and
    returns quietly is indistinguishable from one that rebuilt it correctly.
    """
    if index is not None:
        if not 0 <= index < len(tables):
            raise OptionsError(
                f"table_index {index} is out of range; the document has {len(tables)} table(s)"
            )
        return tables[index]

    wanted = [column.strip() for column in columns]
    for table in tables:
        if table_header_cells(table) == wanted:
            return table

    available = [f"{position}: {table_header_cells(table)}" for position, table in enumerate(tables)]
    raise OptionsError(
        f"no table has the header {wanted}; pass table_index to choose one.\n"
        f"Tables in the document: {available or 'none'}"
    )


def _template_row(table: Element) -> Element:
    """The row a rebuilt table's formatting comes from: the first data row."""
    rows = findall(table, "w:tr")
    if len(rows) < 2:
        raise OptionsError("the abbreviation table has no data row to take its formatting from")
    return rows[1]


def _rebuild_row(template: Element, values: Sequence[str]) -> Element:
    """One row built from *template*, with the cells' text replaced by *values*."""
    row = deepcopy(template)
    cells = findall(row, "w:tc")

    if len(cells) != len(values):
        raise OptionsError(
            f"the abbreviation table has {len(cells)} column(s) and the source has "
            f"{len(values)}; they must agree or the table cannot be rebuilt"
        )

    for cell, value in zip(cells, values, strict=True):
        _set_cell_text(cell, value)
    return row


def _set_cell_text(cell: Element, value: str) -> None:
    """Replace a cell's text with *value*, keeping the cell's own formatting.

    The cell's first paragraph and first run are the templates: the paragraph
    properties and the run properties survive, and one run is emitted per inline
    markdown span. Emphasis comes from the *source*, never from the template — a
    template row whose first term happens to be italic must not make every term
    italic, which is why the three emphasis properties are stripped from the run
    template before the spans are written.
    """
    template_paragraph = next(iter(iter_descendants(cell, "w:p")), None)
    if template_paragraph is None:
        raise OptionsError("an abbreviation cell has no paragraph to take its formatting from")
    template_paragraph = deepcopy(template_paragraph)

    properties = find(cell, "w:tcPr")
    for child in children(cell):
        if child is not properties:
            cell.remove(child)

    runs = findall(template_paragraph, "w:r")
    if not runs:
        runs = [ordered_insert(template_paragraph, new("w:r"))]

    anchor = list(template_paragraph).index(runs[0])
    template_run = deepcopy(runs[0])
    for run in runs:
        template_paragraph.remove(run)

    # Keep only the run properties and a single text node, and drop the three
    # properties the source is about to decide for itself.
    for child in children(template_run):
        if child.tag not in (q("rPr"), q("t")):
            template_run.remove(child)

    run_properties = find(template_run, "w:rPr")
    if run_properties is not None:
        for tag in ("b", "bCs", "i", "iCs", "vertAlign"):
            stale = find(run_properties, f"w:{tag}")
            if stale is not None:
                run_properties.remove(stale)

    texts = findall(template_run, "w:t")
    for extra in texts[1:]:
        template_run.remove(extra)
    if not texts:
        ordered_insert(template_run, new("w:t"))

    for offset, (chunk, bold, italic, vertical) in enumerate(parse_markup(value)):
        run = deepcopy(template_run)
        run_properties = find(run, "w:rPr")
        if run_properties is None:
            run_properties = ordered_insert(run, new("w:rPr"))
        if bold:
            ordered_insert(run_properties, new("w:b"))
            ordered_insert(run_properties, new("w:bCs"))
        if italic:
            ordered_insert(run_properties, new("w:i"))
            ordered_insert(run_properties, new("w:iCs"))
        if vertical:
            ordered_insert(run_properties, new("w:vertAlign", **{"w:val": vertical}))

        text_node = find(run, "w:t")
        if text_node is None:
            text_node = ordered_insert(run, new("w:t"))
        text_node.set(XML_SPACE, "preserve")
        text_node.text = chunk

        template_paragraph.insert(anchor + offset, run)

    cell.append(template_paragraph)


def rebuild_table(table: Element, rows: Sequence[Sequence[str]]) -> None:
    """Replace the table's data rows with *rows*, keeping its header and formatting.

    Rows are added and removed to match the source. An implementation that only
    rewrote the rows already present would silently truncate an abbreviation list
    that had grown, which is the kind of loss nobody notices until a term is
    missing from the submitted copy.
    """
    template = _template_row(table)
    existing = findall(table, "w:tr")
    for row in existing[1:]:
        table.remove(row)

    for values in rows:
        table.append(_rebuild_row(template, values))


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------


def _write_if_changed(package: Package, name: str, root: Element, before: bytes) -> None:
    if etree.tostring(root) != before:
        package.replace_xml(name, root)


def apply(
    source: Path,
    destination: Path,
    *,
    style: StyleSpec,
    options: Mapping[str, object] | None = None,
) -> Path:
    """Read *source*, rebuild its abbreviation table from the markdown, write *destination*.

    *style* is accepted for the uniform stage protocol and is not read: an
    abbreviation table's formatting comes from the table already in the document,
    which the renderer produced from the project's style template. Re-deciding it
    here would be a second specification for something the first one already
    covers.
    """
    del style  # part of the stage protocol; this stage's formatting comes from the document

    settings = _read_options(options)
    header, rows = read_markdown_table(settings.qmd)

    if tuple(header) != settings.columns:
        raise OptionsError(
            f"the markdown table's header is {header}, but the configured columns are "
            f"{list(settings.columns)}; one of the two is out of date"
        )

    source = Path(source)
    destination = Path(destination)

    with Package(source) as package:
        document = package.xml(DOCUMENT_PART)
        before = etree.tostring(document)

        tables = list(iter_descendants(document, "w:tbl"))
        table = select_table(tables, settings.columns, settings.table_index)
        rebuild_table(table, rows)

        _write_if_changed(package, DOCUMENT_PART, document, before)
        return package.save(destination)
