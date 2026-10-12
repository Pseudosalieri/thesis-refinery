"""Tests for ``extract-prefix``.

The test that matters most is the one that opens the result. Both clean-ups this
command performs were found by *opening* a cut document rather than by reading the
code, and the failure they prevent — an OPC reader raising ``KeyError`` for a part
that is no longer in the package — is invisible to any assertion that only looks at
the parts the command chose to keep.

So the fixture reproduces the incident: a source document that carries a part the
front pages do not use, with an override and a relationship naming it. Cutting those
pages must leave a document that still opens.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from lxml import etree

from thesis_refinery.commands import prefix
from thesis_refinery.docxlib.pkgs import Package
from thesis_refinery.docxlib.synth import (
    build_docx,
    drawing_xml,
    paragraph_xml,
    parse_fragment,
    style_xml,
    styles_xml,
)
from thesis_refinery.docxlib.xmlq import Element, text_of
from thesis_refinery.exitcodes import ExitCode

DOCUMENT = "word/document.xml"
PAGE_BREAK = '<w:r><w:br w:type="page"/></w:r>'

STYLES = styles_xml(
    [
        style_xml("Normal", "Normal", default=True),
        style_xml("BodyText", "Body Text", based_on="Normal"),
    ]
)

IMAGE = b"\x89PNG\r\n\x1a\n" + b"synthetic image bytes"
"""Not a valid PNG. The command copies bytes and never decodes them, and a fixture
that needed a real image would be a fixture with a binary in the repository."""


def build_source(path: Path, *, extra_parts: dict[str, bytes] | None = None) -> Path:
    """A document with three pages: cover, a picture, then two chapters.

    A page break means "start a new page here", so the layout is

        page 1: the cover paragraph and the picture
        page 2: Chapter One
        page 3: Chapter Two

    The picture is on page one, so a one-page cut has to keep the media part. The
    extra parts stand in for the ``word/webextensions/`` directory whose appearance
    in a later source broke the original tool.
    """
    body = "\n".join(
        [
            paragraph_xml("A cover paragraph.", style="BodyText"),
            f'<w:p><w:r>{drawing_xml("rIdImage", width_emu=914400, height_emu=914400)}</w:r></w:p>',
            f"<w:p>{PAGE_BREAK}</w:p>",
            paragraph_xml("Chapter One", style="BodyText"),
            f"<w:p>{PAGE_BREAK}</w:p>",
            paragraph_xml("Chapter Two", style="BodyText"),
            "<w:sectPr/>",
        ]
    )
    parts = {
        "word/media/image1.png": IMAGE,
        "word/webextensions/taskpanes.xml": b"<?xml version='1.0'?><taskpanes/>",
    }
    parts.update(extra_parts or {})
    overrides = {
        "/word/webextensions/taskpanes.xml": "application/vnd.ms-office.webextensiontaskpanes+xml",
    }
    return build_docx(
        path,
        body=body,
        styles=STYLES,
        extra_parts=parts,
        extra_defaults={"png": "image/png"},
        extra_overrides=overrides,
        document_relationships=[
            ("rIdImage", "image", "media/image1.png"),
            ("rIdTaskpanes", "webextensiontaskpanes", "webextensions/taskpanes.xml"),
        ],
    )


def parts_of(path: Path) -> dict[str, bytes]:
    with Package(path) as package:
        return {name: package.read(name) for name in package.names()}


# ---------------------------------------------------------------------------
# Counting the pages
# ---------------------------------------------------------------------------


def paragraph_with_break() -> Element:
    return parse_fragment(f"<w:p>{PAGE_BREAK}</w:p>")


def test_a_cut_lands_just_past_the_nth_break() -> None:
    children = [parse_fragment("<w:p/>"), paragraph_with_break(), parse_fragment("<w:p/>")]
    assert prefix.page_cut(children, 1) == 2


def test_asking_for_more_pages_than_the_document_has_is_refused() -> None:
    """A short cut produces a cover missing its last page, and nothing says so."""
    with pytest.raises(prefix.PrefixError, match=r"fewer than the 5 asked for"):
        prefix.page_cut([parse_fragment("<w:p/>")], 5)


def test_the_break_count_is_reported_when_it_is_short() -> None:
    with pytest.raises(prefix.PrefixError, match="only 1 page break"):
        prefix.page_cut([paragraph_with_break()], 3)


def test_a_break_inside_a_table_is_counted() -> None:
    """The original counted only direct children's breaks, which misses a break in a cell."""
    cell_break = parse_fragment(f"<w:tbl><w:tr><w:tc><w:p>{PAGE_BREAK}</w:p></w:tc></w:tr></w:tbl>")
    assert prefix.page_cut([cell_break, parse_fragment("<w:p/>")], 1) == 1


# ---------------------------------------------------------------------------
# The cut
# ---------------------------------------------------------------------------


def test_the_cut_keeps_the_pages_it_was_asked_for(tmp_path: Path) -> None:
    source = build_source(tmp_path / "in.docx")
    destination = tmp_path / "cover.docx"

    prefix.extract(source, destination, pages=1)

    with Package(destination) as package:
        text = text_of(package.xml(DOCUMENT))
    assert "A cover paragraph." in text
    assert "Chapter One" not in text


def test_the_result_opens_and_every_part_it_declares_is_there(tmp_path: Path) -> None:
    """The regression the two clean-ups exist for.

    An override or a relationship naming a dropped part makes a strict OPC reader
    raise on open — which is how the original tool's bug presented, and why this
    test walks the result rather than trusting the keep list.
    """
    source = build_source(tmp_path / "in.docx")
    destination = tmp_path / "cover.docx"

    prefix.extract(source, destination, pages=1)

    with Package(destination) as package:
        names = set(package.names())
        for relationship in package.relationships(prefix.DOCUMENT_PART):
            if relationship.get("TargetMode") == "External":
                continue
            target = prefix.relative_target(prefix.DOCUMENT_PART, relationship.get("Target") or "")
            assert target in names, f"{relationship.get('Id')} points at missing {target}"

        content_types = package.xml(prefix.CONTENT_TYPES)
        for override in content_types:
            if etree.QName(override).localname != "Override":
                continue
            declared = (override.get("PartName") or "").lstrip("/")
            assert declared in names, f"an override names missing {declared}"


def test_a_part_the_front_pages_do_not_use_is_dropped(tmp_path: Path) -> None:
    source = build_source(tmp_path / "in.docx")
    destination = tmp_path / "cover.docx"

    report = prefix.extract(source, destination, pages=1)

    assert "word/webextensions/taskpanes.xml" in report.dropped
    assert "word/webextensions/taskpanes.xml" not in parts_of(destination)


def test_the_picture_on_a_kept_page_is_kept(tmp_path: Path) -> None:
    """One level of reachability would leave the picture declared but absent."""
    source = build_source(tmp_path / "in.docx")
    destination = tmp_path / "cover.docx"

    prefix.extract(source, destination, pages=1)

    assert parts_of(destination)["word/media/image1.png"] == IMAGE


def test_the_core_parts_are_kept_whether_or_not_the_cover_uses_them(tmp_path: Path) -> None:
    """A cover with no styles part opens as unstyled text."""
    source = build_source(tmp_path / "in.docx")
    destination = tmp_path / "cover.docx"

    prefix.extract(source, destination, pages=1)

    names = parts_of(destination)
    for required in ("[Content_Types].xml", "_rels/.rels", DOCUMENT, "word/styles.xml"):
        assert required in names


def test_the_overrides_for_dropped_parts_are_removed(tmp_path: Path) -> None:
    source = build_source(tmp_path / "in.docx")
    destination = tmp_path / "cover.docx"

    report = prefix.extract(source, destination, pages=1)

    assert "word/webextensions/taskpanes.xml" in report.removed_overrides
    with Package(destination) as package:
        declared = {
            (override.get("PartName") or "").lstrip("/")
            for override in package.xml(prefix.CONTENT_TYPES)
            if etree.QName(override).localname == "Override"
        }
    assert "word/webextensions/taskpanes.xml" not in declared


def test_default_content_types_are_never_removed(tmp_path: Path) -> None:
    """`Default` is keyed by extension and describes parts this command never sees."""
    source = build_source(tmp_path / "in.docx")
    destination = tmp_path / "cover.docx"

    prefix.extract(source, destination, pages=1)

    with Package(destination) as package:
        defaults = {
            etree.QName(node).localname
            for node in package.xml(prefix.CONTENT_TYPES)
            if etree.QName(node).localname == "Default"
        }
    assert defaults, "the png default must survive, or the kept image loses its content type"


def test_the_relationship_to_a_dropped_part_is_removed(tmp_path: Path) -> None:
    source = build_source(tmp_path / "in.docx")
    destination = tmp_path / "cover.docx"

    report = prefix.extract(source, destination, pages=1)

    removed = [target for _, target in report.removed_relationships]
    assert "word/webextensions/taskpanes.xml" in removed


def test_the_relationship_to_a_kept_core_part_survives(tmp_path: Path) -> None:
    """A styles part the document has no relationship to is a styles part Word does
    not load, and the cover's paragraphs carry style ids."""
    source = build_source(tmp_path / "in.docx")
    destination = tmp_path / "cover.docx"

    prefix.extract(source, destination, pages=1)

    with Package(destination) as package:
        targets = {
            prefix.relative_target(prefix.DOCUMENT_PART, r.get("Target") or "")
            for r in package.relationships(prefix.DOCUMENT_PART)
            if r.get("TargetMode") != "External"
        }
    assert "word/styles.xml" in targets


def test_an_external_relationship_is_never_removed() -> None:
    """It points outside the package; a hyperlink that resolves is not dangling.

    Asserted against the function that does the pruning rather than through a whole
    extraction, because through an extraction the relationship would be dropped for
    the unrelated reason that the kept pages never referenced it — and the test
    would then pass whether or not the rule it names is implemented.
    """
    rels = parse_fragment(
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdExternal" Type=".../hyperlink" Target="https://example.invalid/" '
        'TargetMode="External"/>'
        '<Relationship Id="rIdGone" Type=".../image" Target="media/image9.png"/>'
        "</Relationships>"
    )

    removed = prefix.prune_dangling_rels(rels, prefix.DOCUMENT_RELS, keep={"word/styles.xml"})

    assert removed == ["word/media/image9.png"]
    assert [r.get("Id") for r in rels] == ["rIdExternal"]


def test_the_source_is_never_modified(tmp_path: Path) -> None:
    source = build_source(tmp_path / "in.docx")
    before = source.read_bytes()

    prefix.extract(source, tmp_path / "cover.docx", pages=1)

    assert source.read_bytes() == before


def test_cutting_two_pages_reaches_the_second(tmp_path: Path) -> None:
    """The control for the cut itself: one more page, one more chapter.

    Without it, a `page_cut` that always returned the first break would satisfy
    every other test in this file.
    """
    source = build_source(tmp_path / "in.docx")
    destination = tmp_path / "cover.docx"

    prefix.extract(source, destination, pages=2)

    with Package(destination) as package:
        text = text_of(package.xml(DOCUMENT))
    assert "A cover paragraph." in text
    assert "Chapter One" in text
    assert "Chapter Two" not in text, "the third page is a page too far"


def test_zero_pages_is_refused(tmp_path: Path) -> None:
    source = build_source(tmp_path / "in.docx")
    with pytest.raises(prefix.PrefixError, match="at least 1"):
        prefix.extract(source, tmp_path / "cover.docx", pages=0)


def test_a_source_with_no_document_part_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "broken.docx"
    build_docx(broken, body="<w:p/>", styles=STYLES)
    with Package(broken) as package:
        package.delete(DOCUMENT)
        package.save(broken, allow_in_place=True)

    with pytest.raises(prefix.PrefixError, match=r"no word/document\.xml"):
        prefix.extract(broken, tmp_path / "cover.docx")


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_run_reports_and_returns_ok(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    source = build_source(tmp_path / "in.docx")
    out = tmp_path / "cover.docx"

    args = argparse.Namespace(source=str(source), out=str(out), pages=1, quiet=False)
    assert prefix.run(args) == ExitCode.OK

    printed = capsys.readouterr().out
    assert "kept" in printed and "dropped" in printed
    assert out.is_file()


def test_quiet_prints_nothing(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    source = build_source(tmp_path / "in.docx")
    args = argparse.Namespace(source=str(source), out=str(tmp_path / "c.docx"), pages=1, quiet=True)

    assert prefix.run(args) == ExitCode.OK
    assert capsys.readouterr().out == ""


def test_a_refused_cut_returns_a_usage_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    source = build_source(tmp_path / "in.docx")
    args = argparse.Namespace(source=str(source), out=str(tmp_path / "c.docx"), pages=99, quiet=False)

    assert prefix.run(args) == ExitCode.USAGE
    assert "FAIL" in capsys.readouterr().out


def test_the_cli_supplies_every_argument_the_command_reads() -> None:
    """The CLI contract, from the side that can actually break it.

    The argument specification lives in `thesis_refinery.cli`; this asserts that
    every attribute `run` reads is one the parser produces. A flag the parser
    accepts and `run` ignores is a no-op, and an attribute `run` reads that the
    parser never sets is an AttributeError on the happy path.
    """
    from thesis_refinery.cli import build_parser

    parsed = build_parser().parse_args(
        ["extract-prefix", "--source", "a.docx", "--out", "b.docx", "--pages", "3"]
    )
    for name in ("source", "out", "pages", "quiet"):
        assert hasattr(parsed, name), name
    assert parsed.pages == 3
    assert parsed.quiet is False
