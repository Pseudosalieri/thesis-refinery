#!/usr/bin/env python3
"""Generate the demo thesis's ``reference.docx`` — from scratch, never copied.

Quarto and pandoc style their output from a ``reference.docx``: every style in that
document is carried into the document they render. The obvious way to obtain one is
to point at a document that already looks right, which means somebody's thesis, or
an institution's sample, or a publisher's template. All three are other people's
documents — copyrighted, personal, or both — and shipping one would drag a real
identity into a public repository (see ``docs/third-party.md``).

So the example renders against a template that is **generated**: the styles below are
written as OOXML in this file, in the open. Regenerating is one command, and the
claim "nothing here comes from a real thesis" is checkable by reading rather than by
trusting a binary.

The numbers that already have a home in :mod:`thesis_refinery.style` are read from
there — the same specification the build applies to the finished document. That is
deliberate: a template with its own copy of the indent and the caption size is a
second source of formatting truth, which is the failure this project was written to
end.

Run it with no arguments:

    python tools/make_reference_docx.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:  # Allow `python tools/make_reference_docx.py` on an uninstalled checkout.
    sys.path.insert(0, str(_SRC))

from thesis_refinery.docxlib.synth import (  # noqa: E402
    build_docx,
    paragraph_xml,
    style_xml,
    styles_xml,
)
from thesis_refinery.style import DEFAULT_STYLE  # noqa: E402

__all__ = ["DESTINATION", "build", "main", "style_definitions", "styles"]

REPO_ROOT = Path(__file__).resolve().parent.parent
DESTINATION = REPO_ROOT / "examples" / "demo-thesis" / "manuscript" / "reference.docx"

_BODY_FONT = "Times New Roman"
_BODY_EAST_ASIAN_FONT = "宋体"
_HEADING_EAST_ASIAN_FONT = "黑体"

_BODY_SIZE_HALF_POINTS = 24
"""12 pt, the conventional Chinese thesis body size, in the half-points OOXML uses."""

_HEADING_SIZE_HALF_POINTS = (32, 28, 24)
"""16 / 14 / 12 pt for levels 1 to 3, heaviest first."""

_TITLE_SIZE_HALF_POINTS = 44
"""22 pt for the document title on the first page."""

_JUSTIFY = '<w:jc w:val="both"/>'
_CENTRE = '<w:jc w:val="center"/>'

_FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
"""The timestamp every zip member is stamped with; see :func:`_freeze_timestamps`."""


# ---------------------------------------------------------------------------
# Property fragments
# ---------------------------------------------------------------------------
# These are raw XML on purpose. A typed builder per property would be a second,
# subtly different model of a schema that `synth.style_xml` already takes as a
# fragment, and the ordering rules below are exactly the part a typed builder tends
# to get wrong. What the builders do enforce is the *sequence*: CT_PPr and CT_RPr
# are sequence types, and Word rejects a document whose children are out of order
# with a message about corruption rather than a message about order.


def _fonts(*, font: str = _BODY_FONT, east_asian: str = _BODY_EAST_ASIAN_FONT) -> str:
    """``w:rFonts`` with the same face for Latin, high-ANSI and East Asian text."""
    return f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:eastAsia="{east_asian}"/>'


def _run_properties(
    *,
    size_half_points: int,
    font: str = _BODY_FONT,
    east_asian: str = _BODY_EAST_ASIAN_FONT,
    bold: bool = False,
) -> str:
    """``w:rPr`` children in CT_RPr order: fonts, then weight, then size."""
    parts = [_fonts(font=font, east_asian=east_asian)]
    if bold:
        parts.extend(("<w:b/>", "<w:bCs/>"))
    parts.extend(
        (f'<w:sz w:val="{size_half_points}"/>', f'<w:szCs w:val="{size_half_points}"/>')
    )
    return "".join(parts)


def _spacing(*, before: int = 0, after: int = 0, line: int | None = None) -> str:
    """``w:spacing`` in twips. *line* is an exact multiple, as ``lineRule="auto"``."""
    attributes = f'w:before="{before}" w:after="{after}"'
    if line is not None:
        attributes += f' w:line="{line}" w:lineRule="auto"'
    return f"<w:spacing {attributes}/>"


def _paragraph_properties(
    *,
    spacing: str = "",
    indent: str = "",
    justification: str = "",
    keep_next: bool = False,
    outline_level: int | None = None,
) -> str:
    """``w:pPr`` children in CT_PPr order.

    The outline level is placed here, last, rather than handed to
    ``synth.style_xml``: that parameter *prepends* ``w:outlineLvl``, and CT_PPr puts
    it after ``w:spacing`` and ``w:jc``, so a heading carrying both would be written
    in an order Word calls corrupt.
    """
    parts = []
    if keep_next:
        parts.append("<w:keepNext/>")
    parts.extend((spacing, indent, justification))
    if outline_level is not None:
        parts.append(f'<w:outlineLvl w:val="{outline_level}"/>')
    return "".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# The styles
# ---------------------------------------------------------------------------


def style_definitions() -> list[str]:
    """Every ``w:style`` the template defines, in a stable order.

    Exposed as a list rather than a finished document so that a test can compare the
    checked-in ``reference.docx`` against what this file produces *today*, and so
    catch a template that was hand-edited after it was generated.
    """
    spec = DEFAULT_STYLE

    body_spacing = _spacing(line=spec.body.line_spacing_twips)
    body_indent = f'<w:ind w:firstLine="{spec.body.first_line_indent_twips}"/>'
    body_run = _run_properties(size_half_points=_BODY_SIZE_HALF_POINTS)

    caption_run = _run_properties(
        size_half_points=spec.caption.size_half_points,
        font=spec.caption.font,
        east_asian=_BODY_EAST_ASIAN_FONT,
    )

    definitions = [
        style_xml("Normal", "Normal", default=True, run_properties=body_run),
        style_xml(
            "Title",
            "Title",
            based_on="Normal",
            paragraph_properties=_paragraph_properties(
                spacing=_spacing(after=240),
                justification=_CENTRE,
                keep_next=True,
            ),
            run_properties=_run_properties(
                size_half_points=_TITLE_SIZE_HALF_POINTS,
                east_asian=_HEADING_EAST_ASIAN_FONT,
                bold=True,
            ),
        ),
        style_xml(
            "TOCHeading",
            "TOC Heading",
            based_on="Normal",
            paragraph_properties=_paragraph_properties(
                spacing=_spacing(before=240, after=120),
                justification=_CENTRE,
                keep_next=True,
            ),
            run_properties=_run_properties(
                size_half_points=_HEADING_SIZE_HALF_POINTS[0],
                east_asian=_HEADING_EAST_ASIAN_FONT,
                bold=True,
            ),
        ),
        *(
            style_xml(
                f"Heading{level}",
                f"Heading {level}",
                based_on="Normal",
                next_style="BodyText",
                paragraph_properties=_paragraph_properties(
                    keep_next=True,
                    spacing=_spacing(
                        before=240 if level == 1 else 120,
                        after=120 if level == 1 else 60,
                    ),
                    justification=_CENTRE if level == 1 else "",
                    outline_level=level - 1,
                ),
                run_properties=_run_properties(
                    size_half_points=_HEADING_SIZE_HALF_POINTS[level - 1],
                    east_asian=_HEADING_EAST_ASIAN_FONT,
                    bold=True,
                ),
            )
            for level in (1, 2, 3)
        ),
        style_xml(
            "BodyText",
            "Body Text",
            based_on="Normal",
            paragraph_properties=_paragraph_properties(
                spacing=body_spacing,
                indent=body_indent,
                justification=_JUSTIFY,
            ),
            run_properties=body_run,
        ),
        style_xml(
            "FirstParagraph",
            "First Paragraph",
            based_on="BodyText",
            next_style="BodyText",
            paragraph_properties=_paragraph_properties(
                spacing=body_spacing,
                indent=body_indent,
                justification=_JUSTIFY,
            ),
            run_properties=body_run,
        ),
        style_xml(
            "Compact",
            "Compact",
            based_on="Normal",
            paragraph_properties=_paragraph_properties(
                spacing=body_spacing,
                indent='<w:ind w:firstLine="0"/>',
                justification=_JUSTIFY,
            ),
            run_properties=body_run,
        ),
        style_xml(
            "Figure",
            "Figure",
            based_on="Normal",
            paragraph_properties=_paragraph_properties(
                spacing=_spacing(before=120, after=60),
                justification=_CENTRE,
                keep_next=True,
            ),
            run_properties=body_run,
        ),
        style_xml(
            "CaptionedFigure",
            "Captioned Figure",
            based_on="Figure",
            paragraph_properties=_paragraph_properties(
                spacing=_spacing(before=120, after=60),
                justification=_CENTRE,
                keep_next=True,
            ),
            run_properties=body_run,
        ),
        style_xml(
            "ImageCaption",
            "Image Caption",
            based_on="Normal",
            paragraph_properties=_paragraph_properties(
                spacing=_spacing(after=240),
                justification=_CENTRE,
            ),
            run_properties=caption_run,
        ),
        style_xml(
            "TableCaption",
            "Table Caption",
            based_on="Normal",
            paragraph_properties=_paragraph_properties(
                spacing=_spacing(before=120, after=60),
                justification=_CENTRE,
                keep_next=True,
            ),
            run_properties=caption_run,
        ),
    ]

    return definitions


def styles() -> str:
    """The complete ``word/styles.xml`` part."""
    return styles_xml(style_definitions())


def _document_body() -> str:
    """The template's own (ignored) body.

    pandoc reads the styles out of a ``reference.docx`` and drops the content, but a
    document with an empty body invites the reader to wonder whether something was
    lost. One sentence is cheaper than that question.
    """
    note = (
        "Generated by tools/make_reference_docx.py. This document exists to define "
        "styles for pandoc; its text is not rendered into any thesis."
    )
    return paragraph_xml(note)


def _freeze_timestamps(source: Path, destination: Path) -> None:
    """Copy *source* to *destination*, stamping every zip member 1980-01-01.

    ``synth.build_docx`` stamps members with the wall-clock time, so two runs of this
    script produce different bytes for identical styles. A generated file whose bytes
    move when nothing changed cannot be diffed, cannot be checked in meaningfully, and
    cannot be asserted on — "run it twice, get the same file" is the property the
    demo's tests rely on. The temp file and the replace mirror
    ``docxlib.pkgs.Package.save``, so an interrupted write cannot leave half a
    template behind.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        dir=str(destination.parent), prefix=f".{destination.name}.", suffix=".tmp"
    )
    os.close(handle)

    try:
        with (
            zipfile.ZipFile(source) as archive,
            zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as out,
        ):
            for info in archive.infolist():
                fresh = zipfile.ZipInfo(info.filename, date_time=_FIXED_TIMESTAMP)
                fresh.compress_type = zipfile.ZIP_DEFLATED
                fresh.external_attr = info.external_attr
                out.writestr(fresh, archive.read(info.filename))
        Path(temporary).replace(destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def build(destination: Path = DESTINATION) -> Path:
    """Write the template to *destination* and return it."""
    destination = Path(destination)

    with tempfile.TemporaryDirectory() as scratch:
        staging = Path(scratch) / "reference.docx"
        build_docx(staging, body=_document_body(), styles=styles())
        _freeze_timestamps(staging, destination)

    return destination


def _style_name(style: str) -> str:
    """The human name out of one ``w:style`` fragment, for the log line."""
    marker = '<w:name w:val="'
    start = style.find(marker)
    if start < 0:
        return "?"
    start += len(marker)
    return style[start : style.find('"', start)]


def main() -> int:
    path = build()
    names = ", ".join(_style_name(style) for style in style_definitions())
    print(f"wrote {path} ({path.stat().st_size} bytes; styles: {names})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
