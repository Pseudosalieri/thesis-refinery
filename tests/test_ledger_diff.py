"""Tests for ``ledger.diff``.

The two tests that matter most are the ones the module's docstring argues for: an
inserted paragraph must not cascade into a hundred changed entries, and a
paragraph whose style changed while its text did not must still be reported. Both
are provoked here with documents built from ``docxlib.synth`` rather than with
committed binaries.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from thesis_refinery.docxlib.synth import (
    DOCUMENT_NAMESPACES,
    build_docx,
    paragraph_xml,
    part_xml,
    style_xml,
    styles_xml,
    table_xml,
)
from thesis_refinery.ledger import diff

STYLES = styles_xml(
    [
        style_xml("Normal", "Normal", default=True),
        # The id and the display name differ on purpose: the diff reports the id.
        style_xml("BodyText", "Body Text", based_on="Normal"),
        style_xml("Heading1", "Heading 1", based_on="Normal", outline_level=0),
    ]
)

BASELINE: tuple[tuple[str, str | None], ...] = (
    ("Introduction", "Heading1"),
    ("First body paragraph.", "BodyText"),
    ("Second body paragraph.", "BodyText"),
    ("Third body paragraph.", "BodyText"),
    ("Conclusion", "Heading1"),
)


def body(*specs: tuple[str, str | None]) -> list[str]:
    """Ready-made ``w:p`` fragments for ``(text, style)`` pairs."""
    return [paragraph_xml(text, style=style) for text, style in specs]


def document(path: Path, *fragments: str) -> Path:
    """A ``.docx`` whose body is the given fragments, built rather than committed."""
    return build_docx(path, body="\n".join(fragments), styles=STYLES)


def texts_of(report: diff.DiffReport) -> list[tuple[str, int | None, int | None]]:
    return [(change.kind, change.old_index, change.new_index) for change in report.changes]


# ---------------------------------------------------------------------------
# Reading paragraphs
# ---------------------------------------------------------------------------


def test_paragraphs_reports_text_and_style_in_document_order(tmp_path: Path) -> None:
    path = document(
        tmp_path / "sample.docx",
        paragraph_xml("A heading", style="Heading1"),
        paragraph_xml("Body text.", style="BodyText"),
        paragraph_xml("No explicit style."),
    )

    assert diff.paragraphs(path) == [
        diff.Paragraph(index=0, text="A heading", style="Heading1"),
        diff.Paragraph(index=1, text="Body text.", style="BodyText"),
        diff.Paragraph(index=2, text="No explicit style.", style=None),
    ]


def test_the_style_reported_is_the_id_not_the_display_name(tmp_path: Path) -> None:
    """The display name lives in ``styles.xml``, which this module never opens."""
    path = document(tmp_path / "sample.docx", paragraph_xml("Body text.", style="BodyText"))

    assert diff.paragraphs(path)[0].style == "BodyText"


def test_paragraphs_inside_tables_are_included_where_the_table_sits(tmp_path: Path) -> None:
    """A diff that skipped tables would miss a rewritten appendix."""
    path = document(
        tmp_path / "sample.docx",
        paragraph_xml("Before the table.", style="BodyText"),
        table_xml([["Medium", "Reading"], ["Example", "42"]]),
        paragraph_xml("After the table.", style="BodyText"),
    )

    assert [paragraph.text for paragraph in diff.paragraphs(path)] == [
        "Before the table.",
        "Medium",
        "Reading",
        "Example",
        "42",
        "After the table.",
    ]


def test_paragraphs_in_headers_and_footers_are_not_part_of_the_diff(tmp_path: Path) -> None:
    header = part_xml(
        root="w:hdr",
        namespaces=DOCUMENT_NAMESPACES,
        body=paragraph_xml("EXAMPLE THESIS", style="BodyText"),
    )
    footer = part_xml(
        root="w:ftr",
        namespaces=DOCUMENT_NAMESPACES,
        body=paragraph_xml("A page number", style="BodyText"),
    )
    path = build_docx(
        tmp_path / "sample.docx",
        body=paragraph_xml("Body text.", style="BodyText"),
        styles=STYLES,
        extra_parts={
            "word/header1.xml": header.encode("utf-8"),
            "word/footer1.xml": footer.encode("utf-8"),
        },
    )

    assert [paragraph.text for paragraph in diff.paragraphs(path)] == ["Body text."]


def test_a_document_with_no_body_paragraphs_reads_as_empty(tmp_path: Path) -> None:
    empty = document(tmp_path / "empty.docx")

    assert diff.paragraphs(empty) == []
    assert diff.diff(empty, empty).changes == ()


def test_a_missing_document_is_an_error(tmp_path: Path) -> None:
    """An empty paragraph list would report the other file as entirely added."""
    with pytest.raises(FileNotFoundError):
        diff.paragraphs(tmp_path / "nowhere.docx")


def test_a_file_that_is_not_a_package_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "not-really.docx"
    path.write_bytes(b"this is not a zip archive")

    with pytest.raises(zipfile.BadZipFile):
        diff.paragraphs(path)


# ---------------------------------------------------------------------------
# The diff itself
# ---------------------------------------------------------------------------


def test_identical_documents_produce_no_changes(tmp_path: Path) -> None:
    old = document(tmp_path / "old.docx", *body(*BASELINE))
    new = document(tmp_path / "new.docx", *body(*BASELINE))

    report = diff.diff(old, new)

    assert report.changes == ()
    assert report.counts == {"added": 0, "removed": 0, "changed": 0}
    assert (report.old_path, report.new_path) == (str(old), str(new))


def test_an_edited_paragraph_is_one_change(tmp_path: Path) -> None:
    revised: tuple[tuple[str, str | None], ...] = (
        *BASELINE[:2],
        ("Second body paragraph, revised.", "BodyText"),
        *BASELINE[3:],
    )
    old = document(tmp_path / "old.docx", *body(*BASELINE))
    new = document(tmp_path / "new.docx", *body(*revised))

    report = diff.diff(old, new)

    assert report.counts == {"added": 0, "removed": 0, "changed": 1}
    change = report.changes[0]
    assert (change.old_index, change.new_index) == (2, 2)
    assert change.old_text == "Second body paragraph."
    assert change.new_text == "Second body paragraph, revised."
    assert change.old_style == change.new_style == "BodyText"


def test_an_inserted_paragraph_does_not_cascade(tmp_path: Path) -> None:
    """The requirement the text-based matcher exists for."""
    inserted: tuple[str, str | None] = ("A paragraph inserted in the middle.", "BodyText")
    shifted: tuple[tuple[str, str | None], ...] = (*BASELINE[:2], inserted, *BASELINE[2:])
    old = document(tmp_path / "old.docx", *body(*BASELINE))
    new = document(tmp_path / "new.docx", *body(*shifted))

    report = diff.diff(old, new)

    assert report.counts == {"added": 1, "removed": 0, "changed": 0}
    assert texts_of(report) == [("added", None, 2)]
    assert report.changes[0].new_text == inserted[0]


def test_an_insertion_into_a_long_document_is_still_one_change(tmp_path: Path) -> None:
    long_body: tuple[tuple[str, str | None], ...] = tuple(
        (f"Paragraph number {index} of the body.", "BodyText") for index in range(30)
    )
    inserted: tuple[str, str | None] = ("Inserted halfway.", "BodyText")
    old = document(tmp_path / "old.docx", *body(*long_body))
    new = document(tmp_path / "new.docx", *body(*long_body[:15], inserted, *long_body[15:]))

    report = diff.diff(old, new)

    assert report.counts == {"added": 1, "removed": 0, "changed": 0}
    assert report.changes[0].new_index == 15


def test_a_removed_paragraph_is_one_removal(tmp_path: Path) -> None:
    trimmed: tuple[tuple[str, str | None], ...] = (*BASELINE[:2], *BASELINE[3:])
    old = document(tmp_path / "old.docx", *body(*BASELINE))
    new = document(tmp_path / "new.docx", *body(*trimmed))

    report = diff.diff(old, new)

    assert report.counts == {"added": 0, "removed": 1, "changed": 0}
    change = report.changes[0]
    assert (change.old_index, change.new_index) == (2, None)
    assert change.old_text == "Second body paragraph."
    assert change.new_text == ""


def test_a_style_change_with_unchanged_text_is_reported(tmp_path: Path) -> None:
    """The regression this project exists for, not just the edits a matcher sees."""
    promoted: tuple[tuple[str, str | None], ...] = (
        *BASELINE[:2],
        ("Second body paragraph.", "Heading1"),
        *BASELINE[3:],
    )
    old = document(tmp_path / "old.docx", *body(*BASELINE))
    new = document(tmp_path / "new.docx", *body(*promoted))

    report = diff.diff(old, new)

    assert report.counts == {"added": 0, "removed": 0, "changed": 1}
    change = report.changes[0]
    assert change.old_text == change.new_text == "Second body paragraph."
    assert (change.old_style, change.new_style) == ("BodyText", "Heading1")


def test_a_style_change_does_not_hide_a_later_text_change(tmp_path: Path) -> None:
    revised: tuple[tuple[str, str | None], ...] = (
        BASELINE[0],
        ("First body paragraph.", "Heading1"),
        BASELINE[2],
        ("Third body paragraph, revised.", "BodyText"),
        BASELINE[4],
    )
    old = document(tmp_path / "old.docx", *body(*BASELINE))
    new = document(tmp_path / "new.docx", *body(*revised))

    report = diff.diff(old, new)

    assert report.counts == {"added": 0, "removed": 0, "changed": 2}
    assert [
        (change.old_index, change.old_style, change.new_style) for change in report.changes
    ] == [
        (1, "BodyText", "Heading1"),
        (3, "BodyText", "BodyText"),
    ]


def test_a_replaced_block_is_paired_with_its_leftover_reported(tmp_path: Path) -> None:
    old = document(
        tmp_path / "old.docx",
        *body(
            ("Opening.", "BodyText"),
            ("Old one.", "BodyText"),
            ("Old two.", "BodyText"),
            ("Old three.", "BodyText"),
            ("Closing.", "BodyText"),
        ),
    )
    new = document(
        tmp_path / "new.docx",
        *body(
            ("Opening.", "BodyText"),
            ("New one.", "BodyText"),
            ("New two.", "BodyText"),
            ("Closing.", "BodyText"),
        ),
    )

    report = diff.diff(old, new)

    assert report.counts == {"added": 0, "removed": 1, "changed": 2}
    assert texts_of(report) == [("changed", 1, 1), ("changed", 2, 2), ("removed", 3, None)]


def test_changes_come_out_in_document_order(tmp_path: Path) -> None:
    revised: tuple[tuple[str, str | None], ...] = (
        ("Inserted at the top.", "BodyText"),
        BASELINE[0],
        ("First body paragraph, revised.", "BodyText"),
        *BASELINE[2:],
    )
    old = document(tmp_path / "old.docx", *body(*BASELINE))
    new = document(tmp_path / "new.docx", *body(*revised))

    report = diff.diff(old, new)

    assert [change.kind for change in report.changes] == ["added", "changed"]
    positions = [
        change.new_index if change.new_index is not None else change.old_index
        for change in report.changes
    ]
    assert positions == sorted(positions)


def test_repeated_paragraphs_do_not_defeat_the_matcher(tmp_path: Path) -> None:
    """difflib's junk heuristic calls a repeated short paragraph noise.

    A thesis is made of them: spacer lines, a repeated table header, a repeated
    continuation marker. With the heuristic on, one edited paragraph in the middle
    of three hundred comes back as a hundred and fifty changes.
    """
    spacers: tuple[tuple[str, str | None], ...] = tuple(("", None) for _ in range(150))
    old = document(
        tmp_path / "old.docx", *body(*spacers, ("The one real paragraph.", "BodyText"), *spacers)
    )
    new = document(
        tmp_path / "new.docx", *body(*spacers, ("An edited paragraph.", "BodyText"), *spacers)
    )

    report = diff.diff(old, new)

    assert report.counts == {"added": 0, "removed": 0, "changed": 1}
    assert report.changes[0].new_index == 150


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


def test_change_refuses_an_unknown_kind() -> None:
    """A kind outside KINDS would disappear from the report while still counting."""
    with pytest.raises(ValueError, match="unknown change kind"):
        diff.Change(
            kind="moved",
            old_index=0,
            new_index=0,
            old_text="a",
            new_text="a",
            old_style=None,
            new_style=None,
        )


def test_counts_always_carries_all_three_kinds() -> None:
    report = diff.DiffReport(old_path="old.docx", new_path="new.docx", changes=())

    assert report.counts == {"added": 0, "removed": 0, "changed": 0}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def changed_document(tmp_path: Path) -> diff.DiffReport:
    """A report with one of every kind of change."""
    old = document(tmp_path / "old.docx", *body(*BASELINE))
    new = document(
        tmp_path / "new.docx",
        *body(
            BASELINE[0],
            ("First body paragraph, revised.", "BodyText"),
            ("Inserted after the first paragraph.", "Heading1"),
            *BASELINE[2:],
        ),
    )
    return diff.diff(old, new)


def test_render_markdown_groups_by_kind_and_shows_both_texts(tmp_path: Path) -> None:
    report = changed_document(tmp_path)

    text = diff.render_markdown(report)

    assert text.startswith("# Paragraph diff\n")
    assert "**1 added, 0 removed, 1 changed**" in text
    assert "## Added (1)" in text
    assert "## Removed (0)" in text
    assert "_None._" in text
    assert "## Changed (1)" in text
    assert "**Before**" in text
    assert "**After**" in text
    assert "> First body paragraph." in text
    assert "> First body paragraph, revised." in text
    assert text.endswith("\n")


def test_render_markdown_shows_a_style_only_change_as_unchanged_text(tmp_path: Path) -> None:
    promoted: tuple[tuple[str, str | None], ...] = (
        *BASELINE[:2],
        ("Second body paragraph.", "Heading1"),
        *BASELINE[3:],
    )
    old = document(tmp_path / "old.docx", *body(*BASELINE))
    new = document(tmp_path / "new.docx", *body(*promoted))

    text = diff.render_markdown(diff.diff(old, new))

    assert "- Style: `BodyText` -> `Heading1`" in text
    assert "- Text: unchanged" in text


def test_render_markdown_keeps_line_breaks_out_of_table_rows(tmp_path: Path) -> None:
    broken = paragraph_xml(runs=["<w:r><w:t>A</w:t><w:br/><w:t>B</w:t></w:r>"])
    base = document(tmp_path / "base.docx", paragraph_xml("Before.", style="BodyText"))
    with_break = document(
        tmp_path / "with-break.docx",
        paragraph_xml("Before.", style="BodyText"),
        broken,
    )

    report = diff.diff(base, with_break)

    assert report.changes[0].new_text == "A\nB"
    assert "| 1 | - | A\\nB |" in diff.render_markdown(report)


def test_render_markdown_quotes_a_paragraph_line_by_line(tmp_path: Path) -> None:
    broken = paragraph_xml(runs=["<w:r><w:t>A</w:t><w:br/><w:t>B</w:t></w:r>"])
    old = document(tmp_path / "old.docx", paragraph_xml("A", style="BodyText"))
    new = document(tmp_path / "new.docx", broken)

    text = diff.render_markdown(diff.diff(old, new))

    assert "> A\n> B" in text


def test_render_markdown_truncates_a_very_long_paragraph(tmp_path: Path) -> None:
    long_text = "x" * (diff.SNIPPET_CHARS + 100)
    old = document(tmp_path / "old.docx", paragraph_xml("Short.", style="BodyText"))
    new = document(tmp_path / "new.docx", paragraph_xml(long_text, style="BodyText"))

    text = diff.render_markdown(diff.diff(old, new))

    assert "x" * diff.SNIPPET_CHARS in text
    assert "x" * (diff.SNIPPET_CHARS + 1) not in text


def test_render_markdown_of_an_unchanged_document_says_so(tmp_path: Path) -> None:
    same = document(tmp_path / "same.docx", *body(*BASELINE))

    text = diff.render_markdown(diff.diff(same, same))

    assert "**0 added, 0 removed, 0 changed**" in text
    assert text.count("_None._") == len(diff.KINDS)


def test_render_markdown_escapes_a_pipe_in_a_table_cell(tmp_path: Path) -> None:
    old = document(tmp_path / "old.docx", paragraph_xml("Before.", style="BodyText"))
    new = document(
        tmp_path / "new.docx",
        paragraph_xml("Before.", style="BodyText"),
        paragraph_xml("a | b", style="BodyText"),
    )

    text = diff.render_markdown(diff.diff(old, new))

    assert "a \\| b" in text
