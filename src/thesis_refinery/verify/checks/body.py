"""Body paragraph formatting: first-line indent and line spacing.

The important behaviour here is the *empty* case. If no paragraph matches the
configured body styles, that is a **failure**, not a pass. Style names are
localised — Word in a Chinese locale calls it ``正文`` where pandoc wrote
``Body Text`` — and a check that iterates zero paragraphs and concludes "all
zero of them are correct" reports success on a document it never looked at. That
is exactly how the predecessor's audit went two months without noticing it was
inspecting the wrong thing.

The second important behaviour is that a value is resolved through the style
chain. pandoc puts the indent on the style, not on each paragraph, so a check
reading only the paragraph's own ``w:pPr`` would report every paragraph as
unindented. Each finding therefore names where the value came from.
"""

from __future__ import annotations

from collections.abc import Iterator

from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.units import twips_to_pt
from thesis_refinery.docxlib.xmlq import Element
from thesis_refinery.verify.checks import Check
from thesis_refinery.verify.context import Context
from thesis_refinery.verify.findings import Finding, failed, passed
from thesis_refinery.verify.styles import effective_property

CHECK = Check(
    name="body",
    description="first-line indent and line spacing of body paragraphs",
    run=lambda context: run(context),
)

_MAX_REPORTED = 10


def run(context: Context) -> Iterator[Finding]:
    paragraphs = [p for p in context.body_paragraphs() if context.is_body_style(p)]

    if not paragraphs:
        yield failed(
            "body.paragraphs",
            "no paragraph uses any of the configured body styles, so nothing was checked",
            (
                f"configured styles: {', '.join(context.style.body_style_ids)}",
                "a localised or renamed style makes this check match nothing; it fails "
                "rather than passing vacuously",
            ),
        )
        return

    yield from _count(context, paragraphs)
    yield from _indent(context, paragraphs)
    yield from _line_spacing(context, paragraphs)


def _count(context: Context, paragraphs: list[Element]) -> Iterator[Finding]:
    expected = context.expectations.body_paragraphs
    actual = len(paragraphs)

    if expected is None:
        yield passed(
            "body.paragraphs",
            f"{actual} body paragraph(s), derived from the document",
            ("set checks.expect_body_paragraphs to pin this",),
        )
        return

    if actual < expected:
        yield failed(
            "body.paragraphs",
            f"expected at least {expected} body paragraph(s), found {actual}",
            ("a drop like this usually means a style was renamed, not that text vanished",),
        )
        return

    yield passed("body.paragraphs", f"{actual} body paragraph(s), at least the expected {expected}")


def _indent(context: Context, paragraphs: list[Element]) -> Iterator[Finding]:
    wanted = context.style.body.first_line_indent_twips
    offenders: list[str] = []

    for index, paragraph in enumerate(paragraphs):
        element, source, style_name = effective_property(paragraph, context.styles, "w:ind")
        actual = _int_or_none(element.get(q("firstLine"))) if element is not None else None
        if actual != wanted:
            offenders.append(
                f"paragraph {index + 1}: firstLine={_describe(actual)} "
                f"({_origin(source, style_name)}), expected {wanted} twips"
            )

    if offenders:
        yield failed(
            "body.first-line-indent",
            f"{len(offenders)} of {len(paragraphs)} body paragraph(s) have the wrong "
            f"first-line indent ({wanted} twips = 24 pt = two full-width characters)",
            tuple(offenders[:_MAX_REPORTED])
            + (() if len(offenders) <= _MAX_REPORTED else (f"... and {len(offenders) - _MAX_REPORTED} more",)),
        )
        return

    yield passed(
        "body.first-line-indent",
        f"all {len(paragraphs)} body paragraph(s) indent {wanted} twips",
    )


def _line_spacing(context: Context, paragraphs: list[Element]) -> Iterator[Finding]:
    wanted = context.style.body.line_spacing_twips
    offenders: list[str] = []

    for index, paragraph in enumerate(paragraphs):
        element, source, style_name = effective_property(paragraph, context.styles, "w:spacing")
        actual = _int_or_none(element.get(q("line"))) if element is not None else None
        if actual != wanted:
            offenders.append(
                f"paragraph {index + 1}: line={_describe(actual)} "
                f"({_origin(source, style_name)}), expected {wanted} twips "
                f"({twips_to_pt(wanted):g} pt)"
            )

    if offenders:
        yield failed(
            "body.line-spacing",
            f"{len(offenders)} of {len(paragraphs)} body paragraph(s) have the wrong line spacing",
            tuple(offenders[:_MAX_REPORTED])
            + (() if len(offenders) <= _MAX_REPORTED else (f"... and {len(offenders) - _MAX_REPORTED} more",)),
        )
        return

    yield passed(
        "body.line-spacing",
        f"all {len(paragraphs)} body paragraph(s) spaced {wanted} twips ({twips_to_pt(wanted):g} pt)",
    )


def _origin(source: str | None, style_name: str | None) -> str:
    if source == "direct":
        return "set on the paragraph"
    if source == "style" and style_name:
        return f"inherited from style {style_name!r}"
    return "not set anywhere"


def _describe(value: int | None) -> str:
    return "unset" if value is None else str(value)


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
