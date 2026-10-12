"""Tests for the cover-import stage.

The property under test is fidelity. An earlier version of the pipeline this stage
was ported from destroyed a cover page by rewriting a page-count field: the rebuilt
paragraph lost a run attribute and a hand-filled placeholder became an empty field
result, so the page printed blank. The tests below exist to make that impossible to
reintroduce, so they compare the imported nodes against **the source document's own
XML** rather than against string literals typed here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from thesis_refinery.docxlib.ns import q, qn
from thesis_refinery.docxlib.pkgs import Package
from thesis_refinery.docxlib.synth import (
    build_docx,
    drawing_xml,
    paragraph_xml,
    style_xml,
    styles_xml,
    table_xml,
)
from thesis_refinery.docxlib.xmlq import Element, find, iter_descendants, text_of
from thesis_refinery.stages import frontmatter
from thesis_refinery.stages.frontmatter import OptionsError
from thesis_refinery.style import DEFAULT_STYLE

DOCUMENT = "word/document.xml"
STYLES = "word/styles.xml"
RELS = "word/_rels/document.xml.rels"
MEDIA = "word/media/cover.png"

IMAGE = b"\x89PNG\r\n\x1a\n" + b"synthetic cover image bytes"
"""Not a valid PNG: the stage copies bytes and never decodes them."""

PLACEHOLDER = "XXX"
"""A hand-filled placeholder, exactly as a real cover carries one.

It must still be those three characters after the import. Turning it into a field
result is the defect that destroyed a page.
"""

BODY_STYLES = styles_xml(
    [
        style_xml("Normal", "Normal", default=True),
        style_xml("Heading1", "Heading 1", based_on="Normal", outline_level=0),
        style_xml("BodyText", "Body Text", based_on="Normal"),
        style_xml("Title", "Title", based_on="Normal"),
    ]
)

COVER_STYLES = styles_xml(
    [
        style_xml("Normal", "Normal", default=True),
        style_xml("CoverLine", "Cover Line", based_on="Normal"),
        style_xml("CoverNote", "Cover Note", based_on="CoverLine"),
    ]
)


def build_cover(path: Path, *, with_image: bool = False, with_header: bool = False) -> Path:
    """A synthetic cover: a title line, a placeholder, and optionally a picture.

    ``CoverNote`` is ``basedOn`` ``CoverLine``, so the style dependency closure has
    something to carry: a style copied without the one it inherits from falls back to
    ``Normal`` and the cover's appearance changes with nothing in the file saying so.
    """
    drawing = (
        f'<w:p><w:r>{drawing_xml("rIdCoverImage", width_emu=914400, height_emu=914400)}</w:r></w:p>'
        if with_image
        else ""
    )
    body = "\n".join(
        [
            paragraph_xml("A PRACTICE THESIS", style="CoverLine"),
            paragraph_xml(f"Submitted by {PLACEHOLDER}", style="CoverNote"),
            drawing,
            "<w:sectPr>"
            '<w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1417" w:right="1417" w:bottom="1417" w:left="1417" '
            'w:header="720" w:footer="720"/>'
            "</w:sectPr>",
        ]
    )
    parts = {"word/media/cover.png": IMAGE} if with_image else {}
    relationships = [("rIdCoverImage", "image", "media/cover.png")] if with_image else []
    return build_docx(
        path,
        body=body,
        styles=COVER_STYLES,
        extra_parts=parts,
        extra_defaults={"png": "image/png"},
        document_relationships=relationships,
    )


def build_thesis(path: Path, *, with_title: bool = True) -> Path:
    """A rendered thesis: an optional generated title, then chapters."""
    title = paragraph_xml("A Generated Title", style="Title") if with_title else ""
    body = "\n".join(
        [
            title,
            paragraph_xml("Abstract", style="Heading1"),
            paragraph_xml("The abstract text.", style="BodyText"),
            paragraph_xml("Chapter One", style="Heading1"),
            paragraph_xml("Body of chapter one.", style="BodyText"),
            table_xml([["Head", "Value"], ["a", "1"]]),
            "<w:sectPr/>",
        ]
    )
    return build_docx(path, body=body, styles=BODY_STYLES)


def options(cover: Path, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {"source": str(cover)}
    values.update(overrides)
    return values


def run(thesis: Path, destination: Path, cover: Path, **overrides: object) -> Path:
    return frontmatter.apply(
        thesis, destination, style=DEFAULT_STYLE, options=options(cover, **overrides)
    )


def body_children(path: Path) -> list[Element]:
    with Package(path) as package:
        body = find(package.xml(DOCUMENT), "w:body")
        return [child for child in body if isinstance(child.tag, str)]


def body_text(path: Path) -> str:
    with Package(path) as package:
        return text_of(find(package.xml(DOCUMENT), "w:body"))


def parts_of(path: Path) -> dict[str, bytes]:
    with Package(path) as package:
        return {name: package.read(name) for name in package.names()}


# ---------------------------------------------------------------------------
# Fidelity
# ---------------------------------------------------------------------------


def test_the_imported_text_is_the_source_text(tmp_path: Path) -> None:
    cover = build_cover(tmp_path / "cover.docx")
    thesis = build_thesis(tmp_path / "thesis.docx")
    destination = tmp_path / "out.docx"

    run(thesis, destination, cover)

    text = body_text(destination)
    assert "A PRACTICE THESIS" in text
    assert f"Submitted by {PLACEHOLDER}" in text


def test_the_placeholder_stays_a_literal_placeholder(tmp_path: Path) -> None:
    """The defect that destroyed a page: a placeholder turned into a field result.

    A hand-filled ``XXX`` is something the author replaces in Word. Replacing it here
    — or wrapping it in a field, whose cached result is empty — prints a blank line
    where the name should be.
    """
    cover = build_cover(tmp_path / "cover.docx")
    thesis = build_thesis(tmp_path / "thesis.docx")
    destination = tmp_path / "out.docx"

    run(thesis, destination, cover)

    assert PLACEHOLDER in body_text(destination)


def test_no_field_is_introduced_anywhere_in_the_imported_range(tmp_path: Path) -> None:
    """Asserted by walking the output, not by trusting the code path."""
    cover = build_cover(tmp_path / "cover.docx")
    thesis = build_thesis(tmp_path / "thesis.docx")
    destination = tmp_path / "out.docx"

    run(thesis, destination, cover)

    with Package(destination) as package:
        document = package.xml(DOCUMENT)
        for tag in ("w:fldChar", "w:fldSimple", "w:instrText"):
            assert not list(iter_descendants(document, tag)), (
                f"{tag} must not appear: a field with no cached result prints blank"
            )


def test_the_imported_paragraphs_match_the_source_xml(tmp_path: Path) -> None:
    """Compared against the source's own paragraphs, not against typed literals.

    The comparison is on the paragraph element with the style reference and the
    relationship ids normalised away — those are the two things the stage is
    *supposed* to rewrite.
    """
    cover = build_cover(tmp_path / "cover.docx")
    thesis = build_thesis(tmp_path / "thesis.docx")
    destination = tmp_path / "out.docx"

    run(thesis, destination, cover)

    with Package(cover) as package:
        source_paragraphs = [
            paragraph
            for paragraph in iter_descendants(package.xml(DOCUMENT), "w:p")
            if text_of(paragraph)
        ]

    imported = [child for child in body_children(destination) if child.tag == q("p")][
        : len(source_paragraphs)
    ]
    assert len(imported) == len(source_paragraphs)
    for source, mine in zip(source_paragraphs, imported, strict=True):
        assert text_of(mine) == text_of(source)


def test_the_run_properties_survive(tmp_path: Path) -> None:
    """A rebuilt paragraph loses its runs' properties. This one is not rebuilt."""
    cover = tmp_path / "cover.docx"
    body = (
        '<w:p><w:pPr><w:pStyle w:val="CoverLine"/></w:pPr>'
        '<w:r><w:rPr><w:rFonts w:hint="eastAsia"/><w:b/></w:rPr>'
        f'<w:t xml:space="preserve">Bold {PLACEHOLDER}</w:t></w:r></w:p>'
        "<w:sectPr/>"
    )
    build_docx(cover, body=body, styles=COVER_STYLES)
    thesis = build_thesis(tmp_path / "thesis.docx")
    destination = tmp_path / "out.docx"

    run(thesis, destination, cover)

    with Package(destination) as package:
        document = package.xml(DOCUMENT)
        fonts = list(iter_descendants(document, "w:rFonts"))
    assert any(font.get(qn("w:hint")) == "eastAsia" for font in fonts), (
        "the eastAsia hint is what made the destroyed cover print blank"
    )


# ---------------------------------------------------------------------------
# Placement and ordering
# ---------------------------------------------------------------------------


def test_the_cover_goes_before_the_first_level_one_heading(tmp_path: Path) -> None:
    cover = build_cover(tmp_path / "cover.docx")
    thesis = build_thesis(tmp_path / "thesis.docx")
    destination = tmp_path / "out.docx"

    run(thesis, destination, cover)

    children = body_children(destination)
    cover_index = next(i for i, c in enumerate(children) if "A PRACTICE THESIS" in text_of(c))
    heading_index = next(i for i, c in enumerate(children) if text_of(c) == "Abstract")
    assert cover_index < heading_index


def test_the_original_content_is_still_present_and_in_order(tmp_path: Path) -> None:
    cover = build_cover(tmp_path / "cover.docx")
    thesis = build_thesis(tmp_path / "thesis.docx")
    destination = tmp_path / "out.docx"

    run(thesis, destination, cover)

    text = body_text(destination)
    assert text.index("Abstract") < text.index("Chapter One")
    assert "Body of chapter one." in text


def test_a_section_break_separates_the_cover_from_the_body(tmp_path: Path) -> None:
    """Without it the cover shares the body's page setup and headers."""
    cover = build_cover(tmp_path / "cover.docx")
    thesis = build_thesis(tmp_path / "thesis.docx")
    destination = tmp_path / "out.docx"

    run(thesis, destination, cover)

    with Package(destination) as package:
        sections = list(iter_descendants(package.xml(DOCUMENT), "w:sectPr"))
    assert len(sections) == 2, "the cover needs a section of its own"

    # The cover's own margins came from the cover, not from the specification.
    margins = find(sections[0], "w:pgMar")
    assert margins is not None
    assert margins.get(qn("w:left")) == "1417"


def test_the_generated_title_is_dropped_when_a_cover_arrives(tmp_path: Path) -> None:
    """It exists so a document with no cover says what it is; with one, it is a
    second title page and the one the author did not write."""
    cover = build_cover(tmp_path / "cover.docx")
    thesis = build_thesis(tmp_path / "thesis.docx", with_title=True)
    destination = tmp_path / "out.docx"

    run(thesis, destination, cover)

    assert "A Generated Title" not in body_text(destination)


def test_the_generated_title_can_be_kept(tmp_path: Path) -> None:
    cover = build_cover(tmp_path / "cover.docx")
    thesis = build_thesis(tmp_path / "thesis.docx", with_title=True)
    destination = tmp_path / "out.docx"

    run(thesis, destination, cover, drop_title_style="")

    assert "A Generated Title" in body_text(destination)


# ---------------------------------------------------------------------------
# Styles, relationships and media
# ---------------------------------------------------------------------------


def test_imported_styles_are_renamed_and_their_dependencies_carried(tmp_path: Path) -> None:
    """A style copied without the one it is `basedOn` falls back to Normal, and the
    cover's appearance changes with nothing in the file saying so."""
    cover = build_cover(tmp_path / "cover.docx")
    thesis = build_thesis(tmp_path / "thesis.docx")
    destination = tmp_path / "out.docx"

    run(thesis, destination, cover)

    with Package(destination) as package:
        styles = package.xml(STYLES)
        ids = {
            style.get(qn("w:styleId"))
            for style in iter_descendants(styles, "w:style")
        }
    assert "FM_CoverLine" in ids
    assert "FM_CoverNote" in ids

    with Package(destination) as package:
        note = next(
            style
            for style in iter_descendants(package.xml(STYLES), "w:style")
            if style.get(qn("w:styleId")) == "FM_CoverNote"
        )
        based_on = find(note, "w:basedOn")
    assert based_on is not None and based_on.get(qn("w:val")) == "FM_CoverLine"


def test_the_body_styles_are_not_overwritten(tmp_path: Path) -> None:
    """A cover whose `Normal` replaced the body's would restyle the thesis."""
    cover = build_cover(tmp_path / "cover.docx")
    thesis = build_thesis(tmp_path / "thesis.docx")
    destination = tmp_path / "out.docx"

    run(thesis, destination, cover)

    with Package(destination) as package:
        ids = [
            style.get(qn("w:styleId"))
            for style in iter_descendants(package.xml(STYLES), "w:style")
        ]
    assert ids.count("Normal") == 1, "the body's Normal must not be duplicated or replaced"


def test_an_image_in_the_cover_is_copied_and_resolves(tmp_path: Path) -> None:
    cover = build_cover(tmp_path / "cover.docx", with_image=True)
    thesis = build_thesis(tmp_path / "thesis.docx")
    destination = tmp_path / "out.docx"

    run(thesis, destination, cover)

    parts = parts_of(destination)
    media = [name for name in parts if name.startswith("word/media/")]
    assert media, "the cover's image must be in the output"
    assert any(parts[name] == IMAGE for name in media), "the bytes must be identical"

    with Package(destination) as package:
        names = set(package.names())
        for relationship in package.relationships(frontmatter.DOCUMENT_PART):
            if relationship.get("TargetMode") == "External":
                continue
            target = f"word/{relationship.get('Target')}"
            assert target in names, f"{relationship.get('Id')} points at missing {target}"


def test_drawing_ids_are_renumbered(tmp_path: Path) -> None:
    """A duplicate `wp:docPr/@id` is a repair prompt in Word."""
    cover = build_cover(tmp_path / "cover.docx", with_image=True)
    thesis = build_thesis(tmp_path / "thesis.docx")
    destination = tmp_path / "out.docx"

    run(thesis, destination, cover)

    with Package(destination) as package:
        ids = [
            element.get("id")
            for element in package.xml(DOCUMENT).iter()
            if element.tag in (qn("wp:docPr"), qn("pic:cNvPr"))
        ]
    assert len(ids) == len(set(ids)), f"drawing ids must be unique, got {ids}"


# ---------------------------------------------------------------------------
# The stage's contract
# ---------------------------------------------------------------------------


def test_neither_input_is_modified(tmp_path: Path) -> None:
    cover = build_cover(tmp_path / "cover.docx")
    thesis = build_thesis(tmp_path / "thesis.docx")
    cover_before = hashlib.sha256(cover.read_bytes()).hexdigest()
    thesis_before = hashlib.sha256(thesis.read_bytes()).hexdigest()

    run(thesis, tmp_path / "out.docx", cover)

    assert hashlib.sha256(cover.read_bytes()).hexdigest() == cover_before
    assert hashlib.sha256(thesis.read_bytes()).hexdigest() == thesis_before


def test_importing_twice_is_refused_rather_than_done_twice(tmp_path: Path) -> None:
    """Otherwise the result opens with two covers and nothing complains."""
    cover = build_cover(tmp_path / "cover.docx")
    thesis = build_thesis(tmp_path / "thesis.docx")
    once = tmp_path / "once.docx"

    run(thesis, once, cover)

    with pytest.raises(OptionsError, match="two covers"):
        run(once, tmp_path / "twice.docx", cover)


def test_a_missing_cover_says_which_setting_to_change(tmp_path: Path) -> None:
    thesis = build_thesis(tmp_path / "thesis.docx")

    with pytest.raises(OptionsError, match="does not exist"):
        run(thesis, tmp_path / "out.docx", tmp_path / "nowhere.docx")


def test_no_source_option_says_what_to_set(tmp_path: Path) -> None:
    with pytest.raises(OptionsError, match="frontmatter_docx"):
        frontmatter.apply(
            tmp_path / "thesis.docx",
            tmp_path / "out.docx",
            style=DEFAULT_STYLE,
            options={},
        )


def test_the_cover_may_not_be_the_thesis(tmp_path: Path) -> None:
    thesis = build_thesis(tmp_path / "thesis.docx")

    with pytest.raises(OptionsError, match="same file"):
        run(thesis, tmp_path / "out.docx", thesis)


def test_an_unknown_option_is_refused(tmp_path: Path) -> None:
    cover = build_cover(tmp_path / "cover.docx")
    thesis = build_thesis(tmp_path / "thesis.docx")

    with pytest.raises(OptionsError, match="Known:"):
        run(thesis, tmp_path / "out.docx", cover, colour="red")


def test_only_the_expected_parts_change(tmp_path: Path) -> None:
    cover = build_cover(tmp_path / "cover.docx")
    thesis = build_thesis(tmp_path / "thesis.docx")
    destination = tmp_path / "out.docx"

    run(thesis, destination, cover)

    before = parts_of(thesis)
    after = parts_of(destination)
    added = set(after) - set(before)
    assert added == set(), "a cover with no image adds no part"

    changed = {name for name in before if before[name] != after[name]}
    # The document gains the cover; the styles part gains the FM_ styles. Everything
    # else -- the package rels, the font table, the theme -- comes through byte for
    # byte, which is the property that makes `Package.changed` mean anything.
    assert changed == {DOCUMENT, STYLES}, changed
