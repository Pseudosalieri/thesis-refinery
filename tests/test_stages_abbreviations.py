"""Tests for the abbreviation-table stage.

Ported behaviour is only worth having if the port is checked, so the three things
the original script's iteration bought each get their own tests: the table is found
by its header rather than by an index, inline markdown becomes runs, and the row's
formatting survives the text being replaced.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.pkgs import Package
from thesis_refinery.docxlib.synth import (
    build_docx,
    paragraph_xml,
    style_xml,
    styles_xml,
    table_xml,
)
from thesis_refinery.docxlib.xmlq import find, findall, iter_descendants, text_of
from thesis_refinery.stages import abbreviations
from thesis_refinery.stages.abbreviations import OptionsError
from thesis_refinery.style import DEFAULT_STYLE

DOCUMENT = "word/document.xml"
COLUMNS = ["缩略语", "英文全称", "中文全称"]

STYLES = styles_xml(
    [
        style_xml("Normal", "Normal", default=True),
        style_xml("BodyText", "Body Text", based_on="Normal"),
    ]
)


def write_markdown(path: Path, rows: list[tuple[str, str, str]], *, header: list[str] | None = None) -> Path:
    header = header or COLUMNS
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def build_document(path: Path, rows: list[tuple[str, str, str]], *, header: list[str] | None = None) -> Path:
    header = header or COLUMNS
    body = "\n".join(
        [
            paragraph_xml("A heading", style="BodyText"),
            table_xml([header, *rows]),
            "<w:sectPr/>",
        ]
    )
    return build_docx(path, body=body, styles=STYLES)


def the_table(path: Path):
    with Package(path) as package:
        document = package.xml(DOCUMENT)
        return next(iter_descendants(document, "w:tbl"))


def rows_of(table) -> list[list[str]]:
    return [[text_of(cell).strip() for cell in findall(row, "w:tc")] for row in findall(table, "w:tr")]


# ---------------------------------------------------------------------------
# The markdown side
# ---------------------------------------------------------------------------


def test_the_first_contiguous_table_is_read(tmp_path: Path) -> None:
    source = write_markdown(tmp_path / "abbr.qmd", [("ABC", "a spelled-out term", "术语释义")])
    header, rows = abbreviations.read_markdown_table(source)

    assert header == COLUMNS
    assert rows == [("ABC", "a spelled-out term", "术语释义")]


def test_a_second_table_further_down_is_not_swallowed(tmp_path: Path) -> None:
    """The original scanned the whole file and absorbed any later table whole.

    A line is an entry only inside one contiguous block of pipes.
    """
    source = tmp_path / "abbr.qmd"
    source.write_text(
        "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |\n\nSome prose.\n\n"
        "| X | Y | Z |\n|---|---|---|\n| 7 | 8 | 9 |\n",
        encoding="utf-8",
    )
    _, rows = abbreviations.read_markdown_table(source)

    assert rows == [("1", "2", "3")]


def test_a_row_with_the_wrong_cell_count_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "abbr.qmd"
    source.write_text("| A | B | C |\n|---|---|---|\n| 1 | 2 |\n", encoding="utf-8")

    with pytest.raises(OptionsError, match="2 cells, expected 3"):
        abbreviations.read_markdown_table(source)


def test_a_row_with_an_empty_field_is_refused(tmp_path: Path) -> None:
    """A missing definition is a typo, and reporting it beats shipping a blank cell."""
    source = tmp_path / "abbr.qmd"
    source.write_text("| A | B | C |\n|---|---|---|\n| 1 |  | 3 |\n", encoding="utf-8")

    with pytest.raises(OptionsError, match="empty field"):
        abbreviations.read_markdown_table(source)


def test_a_file_with_no_table_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "abbr.qmd"
    source.write_text("# Headings only\n", encoding="utf-8")

    with pytest.raises(OptionsError, match="no markdown table"):
        abbreviations.read_markdown_table(source)


def test_a_missing_source_file_is_refused_with_its_path(tmp_path: Path) -> None:
    with pytest.raises(OptionsError, match="cannot read"):
        abbreviations.read_markdown_table(tmp_path / "nowhere.qmd")


# ---------------------------------------------------------------------------
# Inline markdown
# ---------------------------------------------------------------------------


def test_emphasis_and_scripts_are_split_into_spans() -> None:
    assert abbreviations.parse_markup("*N*~2~ and ^1^H") == [
        ("N", False, True, None),
        ("2", False, False, "subscript"),
        (" and ", False, False, None),
        ("1", False, False, "superscript"),
        ("H", False, False, None),
    ]


def test_bold_is_read_before_italic() -> None:
    assert abbreviations.parse_markup("**bold** and *italic*") == [
        ("bold", True, False, None),
        (" and ", False, False, None),
        ("italic", False, True, None),
    ]


def test_plain_text_has_no_markup() -> None:
    assert abbreviations.parse_markup("ordinary text") == [("ordinary text", False, False, None)]


def test_an_unmatched_tilde_stays_literal() -> None:
    """Only a *pair* is a subscript; a lone tilde is a character somebody typed."""
    assert abbreviations.parse_markup("a ~ b") == [("a ~ b", False, False, None)]


def test_unbalanced_emphasis_is_refused() -> None:
    """Writing the asterisks into a thesis is the alternative, and nobody would know."""
    with pytest.raises(OptionsError, match="unbalanced"):
        abbreviations.parse_markup("*broken emphasis")


# ---------------------------------------------------------------------------
# Finding the table
# ---------------------------------------------------------------------------


def test_the_table_is_found_by_its_header(tmp_path: Path) -> None:
    document = build_document(tmp_path / "in.docx", [("a", "b", "c")])

    with Package(document) as package:
        tables = list(iter_descendants(package.xml(DOCUMENT), "w:tbl"))
    assert abbreviations.select_table(tables, COLUMNS, None) is tables[0]


def test_a_header_that_matches_nothing_lists_what_is_there(tmp_path: Path) -> None:
    """A stage that finds no table and returns quietly is indistinguishable from one that worked."""
    document = build_document(tmp_path / "in.docx", [("a", "b", "c")], header=["X", "Y", "Z"])

    with Package(document) as package:
        tables = list(iter_descendants(package.xml(DOCUMENT), "w:tbl"))

    with pytest.raises(OptionsError) as excinfo:
        abbreviations.select_table(tables, COLUMNS, None)
    assert "pass table_index" in str(excinfo.value)
    assert "X" in str(excinfo.value)


def test_the_index_is_an_escape_hatch(tmp_path: Path) -> None:
    document = build_document(tmp_path / "in.docx", [("a", "b", "c")], header=["X", "Y", "Z"])

    with Package(document) as package:
        tables = list(iter_descendants(package.xml(DOCUMENT), "w:tbl"))

    assert abbreviations.select_table(tables, COLUMNS, 0) is tables[0]


def test_an_out_of_range_index_is_refused(tmp_path: Path) -> None:
    document = build_document(tmp_path / "in.docx", [("a", "b", "c")])

    with Package(document) as package:
        tables = list(iter_descendants(package.xml(DOCUMENT), "w:tbl"))

    with pytest.raises(OptionsError, match="out of range"):
        abbreviations.select_table(tables, COLUMNS, 7)


# ---------------------------------------------------------------------------
# Rebuilding
# ---------------------------------------------------------------------------


def test_rebuilding_replaces_the_data_rows_and_keeps_the_header(tmp_path: Path) -> None:
    document = build_document(tmp_path / "in.docx", [("old", "old", "old")])
    table = the_table(document)

    abbreviations.rebuild_table(table, [("new", "definition", "定义"), ("two", "second", "第二")])

    assert rows_of(table) == [COLUMNS, ["new", "definition", "定义"], ["two", "second", "第二"]]


def test_a_grown_list_is_not_truncated(tmp_path: Path) -> None:
    """Rewriting only the rows already present silently loses an added abbreviation."""
    document = build_document(tmp_path / "in.docx", [("one", "1", "一"), ("two", "2", "二")])
    table = the_table(document)

    abbreviations.rebuild_table(table, [("a", "1", "一"), ("b", "2", "二"), ("c", "3", "三")])

    assert len(findall(table, "w:tr")) == 4


def test_a_shrunk_list_loses_its_extra_rows(tmp_path: Path) -> None:
    document = build_document(tmp_path / "in.docx", [("one", "1", "一"), ("two", "2", "二")])
    table = the_table(document)

    abbreviations.rebuild_table(table, [("only", "1", "一")])

    assert len(findall(table, "w:tr")) == 2


def test_emphasis_from_the_source_becomes_run_properties(tmp_path: Path) -> None:
    document = build_document(tmp_path / "in.docx", [("old", "old", "old")])
    table = the_table(document)

    abbreviations.rebuild_table(table, [("*IC*~50~", "half-maximal", "半最大")])
    row = findall(table, "w:tr")[1]

    def property_of(run, tag: str):
        properties = find(run, "w:rPr")
        return None if properties is None else find(properties, tag)

    italic = [r for r in iter_descendants(row, "w:r") if property_of(r, "w:i") is not None]
    subscript = [
        r
        for r in iter_descendants(row, "w:r")
        if property_of(r, "w:vertAlign") is not None
        and property_of(r, "w:vertAlign").get(q("val")) == "subscript"
    ]
    assert italic, "the italic span must reach the document as a run property"
    assert subscript, "the tilde span must reach the document as a subscript run"


def test_the_cell_keeps_its_own_properties(tmp_path: Path) -> None:
    """The row is the formatting template; replacing its text must not restyle it."""
    document = build_document(tmp_path / "in.docx", [("old", "old", "old")])
    table = the_table(document)
    before = [etree.tostring(find(cell, "w:tcPr")) for cell in findall(findall(table, "w:tr")[1], "w:tc")]

    abbreviations.rebuild_table(table, [("new", "definition", "定义")])

    after = [
        etree.tostring(find(cell, "w:tcPr"))
        for cell in findall(findall(table, "w:tr")[1], "w:tc")
    ]
    assert before == after


def test_the_header_is_never_touched(tmp_path: Path) -> None:
    document = build_document(tmp_path / "in.docx", [("old", "old", "old")])
    table = the_table(document)
    header_before = etree.tostring(findall(table, "w:tr")[0])

    abbreviations.rebuild_table(table, [("new", "definition", "定义")])

    assert etree.tostring(findall(table, "w:tr")[0]) == header_before


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------


def options(qmd: Path, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {"qmd": str(qmd), "columns": COLUMNS}
    values.update(overrides)
    return values


def test_the_stage_rebuilds_the_table(tmp_path: Path) -> None:
    source = build_document(tmp_path / "in.docx", [("old", "old", "old")])
    qmd = write_markdown(tmp_path / "abbr.qmd", [("ABC", "a spelled-out term", "术语释义")])
    destination = tmp_path / "out.docx"

    abbreviations.apply(source, destination, style=DEFAULT_STYLE, options=options(qmd))

    assert rows_of(the_table(destination)) == [COLUMNS, ["ABC", "a spelled-out term", "术语释义"]]


def test_the_stage_leaves_its_input_alone(tmp_path: Path) -> None:
    source = build_document(tmp_path / "in.docx", [("old", "old", "old")])
    qmd = write_markdown(tmp_path / "abbr.qmd", [("ABC", "a spelled-out term", "定义")])
    before = source.read_bytes()

    abbreviations.apply(source, tmp_path / "out.docx", style=DEFAULT_STYLE, options=options(qmd))

    assert source.read_bytes() == before


def test_applying_twice_changes_nothing_the_second_time(tmp_path: Path) -> None:
    source = build_document(tmp_path / "in.docx", [("old", "old", "old")])
    qmd = write_markdown(tmp_path / "abbr.qmd", [("ABC", "a spelled-out term", "定义")])
    first = tmp_path / "first.docx"
    second = tmp_path / "second.docx"

    abbreviations.apply(source, first, style=DEFAULT_STYLE, options=options(qmd))
    abbreviations.apply(first, second, style=DEFAULT_STYLE, options=options(qmd))

    with Package(first) as left, Package(second) as right:
        assert left.read(DOCUMENT) == right.read(DOCUMENT)


def test_a_header_that_disagrees_with_the_configuration_is_refused(tmp_path: Path) -> None:
    """Both are statements about the same table; a disagreement means one is stale."""
    source = build_document(tmp_path / "in.docx", [("old", "old", "old")])
    qmd = write_markdown(tmp_path / "abbr.qmd", [("ABC", "x", "y")], header=["A", "B", "C"])

    with pytest.raises(OptionsError, match="out of date"):
        abbreviations.apply(
            source, tmp_path / "out.docx", style=DEFAULT_STYLE, options=options(qmd)
        )


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


def test_an_unknown_option_is_refused_and_the_known_ones_named(tmp_path: Path) -> None:
    with pytest.raises(OptionsError, match="known option") as excinfo:
        abbreviations.apply(
            tmp_path / "in.docx",
            tmp_path / "out.docx",
            style=DEFAULT_STYLE,
            options={"qmd": "x.qmd", "columns": COLUMNS, "colums": COLUMNS},
        )
    assert "colums" in str(excinfo.value)


def test_a_missing_required_option_says_what_to_write(tmp_path: Path) -> None:
    with pytest.raises(OptionsError, match="needs"):
        abbreviations.apply(
            tmp_path / "in.docx",
            tmp_path / "out.docx",
            style=DEFAULT_STYLE,
            options={"columns": COLUMNS},
        )


def test_a_scalar_where_columns_are_expected_is_refused(tmp_path: Path) -> None:
    with pytest.raises(OptionsError, match="list of column headings"):
        abbreviations.apply(
            tmp_path / "in.docx",
            tmp_path / "out.docx",
            style=DEFAULT_STYLE,
            options={"qmd": "x.qmd", "columns": "缩略语"},
        )


def test_no_options_at_all_says_what_is_missing(tmp_path: Path) -> None:
    with pytest.raises(OptionsError, match="needs"):
        abbreviations.apply(
            tmp_path / "in.docx", tmp_path / "out.docx", style=DEFAULT_STYLE, options=None
        )
