"""Captions: styled correctly, prefixed consistently, and still all there.

Two checks that look at the same paragraphs from opposite directions, and the
pair is the point.

A caption whose *text* starts with ``Fig.`` but which does not carry the caption
style will be skipped by the formatting stage and will render in body text: it is
a caption that looks like a caption to a human and not to the pipeline.

A paragraph with the caption style whose text does *not* start with a configured
prefix is either mislabelled or was written in a spelling the specification does
not list.

The count check carries a guard the predecessor learned the hard way: the number
of *formatted* captions must be at least the number of caption-looking
paragraphs. Without it, a stage that rewrote caption styles passes a count
assertion by lowering the count it was comparing against.
"""

from __future__ import annotations

from collections.abc import Iterator

from thesis_refinery.docxlib.xmlq import Element, text_of
from thesis_refinery.verify.checks import Check
from thesis_refinery.verify.context import Context
from thesis_refinery.verify.findings import Finding, failed, passed

CHECK = Check(
    name="captions",
    description="caption style, caption prefix, and caption count",
    run=lambda context: run(context),
)

_MAX_REPORTED = 10
_SNIPPET = 60


def run(context: Context) -> Iterator[Finding]:
    styled = context.caption_paragraphs()
    looking = context.paragraphs_with_caption_prefix()

    yield from _unstyled(context, styled, looking)
    yield from _unprefixed(context, styled)
    yield from _count(context, styled, looking)


def _unstyled(
    context: Context, styled: list[Element], looking: list[Element]
) -> Iterator[Finding]:
    styled_set = {id(paragraph) for paragraph in styled}
    offenders = [p for p in looking if id(p) not in styled_set]

    if offenders:
        yield failed(
            "captions.style",
            f"{len(offenders)} paragraph(s) read as captions but do not carry a caption style",
            tuple(_snippet(paragraph) for paragraph in offenders[:_MAX_REPORTED]),
        )
        return

    if not looking:
        yield passed("captions.style", "no caption-looking paragraph to check")
        return

    yield passed("captions.style", f"all {len(looking)} caption(s) carry a caption style")


def _unprefixed(context: Context, styled: list[Element]) -> Iterator[Finding]:
    prefixes = (*context.style.caption.image_prefixes, *context.style.caption.table_prefixes)
    offenders = [
        paragraph
        for paragraph in styled
        if not any(text_of(paragraph).strip().startswith(prefix) for prefix in prefixes)
    ]

    if offenders:
        yield failed(
            "captions.prefix",
            f"{len(offenders)} caption(s) do not start with any configured prefix",
            (
                f"configured prefixes: {', '.join(prefixes)}",
                *(_snippet(paragraph) for paragraph in offenders[:_MAX_REPORTED]),
            ),
        )
        return

    if not styled:
        yield passed("captions.prefix", "no styled caption to check")
        return

    yield passed("captions.prefix", f"all {len(styled)} styled caption(s) carry a known prefix")


def _count(context: Context, styled: list[Element], looking: list[Element]) -> Iterator[Finding]:
    if len(styled) < len(looking):
        yield failed(
            "captions.count",
            f"{len(looking)} paragraph(s) read as captions but only {len(styled)} carry the "
            "caption style",
            (
                "this is the shape a caption-style rewrite takes: the count drops, and an "
                "assertion that only compares against a configured number reports success",
            ),
        )
        return

    expected = context.expectations.captions
    if expected is None:
        yield passed(
            "captions.count",
            f"{len(styled)} caption(s), derived from the document",
            ("set checks.expect_captions to pin this",),
        )
        return

    if len(styled) < expected:
        yield failed(
            "captions.count",
            f"expected at least {expected} caption(s), found {len(styled)}",
        )
        return

    yield passed("captions.count", f"{len(styled)} caption(s), at least the expected {expected}")


def _snippet(paragraph: Element) -> str:
    text = " ".join(text_of(paragraph).split())
    if len(text) > _SNIPPET:
        text = f"{text[:_SNIPPET]}…"
    return f"style={paragraph_style(paragraph)!r} text={text!r}"


def paragraph_style(paragraph: Element) -> str | None:
    from thesis_refinery.verify.styles import paragraph_style_id

    return paragraph_style_id(paragraph)
