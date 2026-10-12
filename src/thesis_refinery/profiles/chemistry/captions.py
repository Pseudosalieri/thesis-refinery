"""The run shape of a figure caption: bold prefix, superscript ordinal, plain rest.

A conforming caption is not one run of text. ``Fig. 3-1`` is bold up to the
figure number, the chapter ordinal that follows is a superscript, and the wording
after it is plain — and a spectrum caption adds a third trick, because the ``1``
of ``1H-NMR`` is a superscript too. Word stores that as several ``w:r``
elements, each with its own ``w:rPr``, so the shape of a caption is a *run
layout* and not a string.

Two properties decide whether this module is usable at all.

**It is idempotent.** Running it twice must leave both the text and the XML
byte-for-byte identical. A splitter that is not idempotent grows the document on
every build: each pass divides the runs again, the file size climbs, Word's
"compare documents" fills with run-level noise, and the ledger diff — which is
how a build proves it changed only what it meant to — becomes unreadable. So the
layout is *recomputed from the text* rather than refined in place: the same text
always yields the same runs, whatever the runs looked like when the call began.
:func:`format_figure_caption` therefore reports ``False`` on a caption it has
already formatted, which is also how a caller can see that a document was already
in shape.

**It is a function of the text, so the text has to be the runs.** The character
offsets that decide where one run ends and the next begins are offsets into the
paragraph's text, and they are only meaningful if that text is exactly the
concatenation of its runs' ``w:t`` children. A paragraph holding a field, a
bookmark-delimited content control or an inline picture is refused rather than
guessed at (see :func:`_text_runs`). Refusing costs nothing: the caption keeps
the text it had, and the caller can see the return value.

The two public functions are not symmetric. :func:`format_spectrum_caption` does
everything :func:`format_figure_caption` does and then superscripts the nucleus
prefix, so it is safe to call the spectrum form after the figure form and *not*
the other way round for a run that is already split. A caller formats spectra
with the spectrum form; the figure form is for figures, and for captions whose
kind names no nucleus.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from lxml import etree

from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.xmlq import (
    Element,
    attr,
    deepcopy,
    ensure_ordered,
    find,
    local_name,
    new,
    ordered_insert,
    text_of,
)

__all__ = ["format_figure_caption", "format_spectrum_caption"]

_BOLD = "bold"
_SUPERSCRIPT = "superscript"
_PLAIN = "plain"

_RUN_PROPERTIES = "w:rPr"
_RUN_PROPERTIES_LOCAL = "rPr"
_TEXT_LOCAL = "t"
_PARAGRAPH_PROPERTIES_LOCAL = "pPr"
_RUN_LOCAL = "r"
_VERT_ALIGN = "w:vertAlign"
_BOLD_TOKENS = ("w:b", "w:bCs")

_TRUE_SPELLINGS = ("true", "1", "on")
"""What OOXML accepts for a boolean that is on, when it is spelled out at all."""

_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

_ORDINAL = r"(?P<ordinal>(?:[-.]\d+)+)"
"""The chapter ordinal of a figure number: ``3-1``, ``3.1``, ``3-1-2``.

Both separators are accepted because both are published: a thesis numbers
``Fig. 3-1``, another ``Fig. 3.1``. The separators are inside the optional group
so neither can match on its own — ``Fig. 3. Title`` ends the number at the ``3``
and leaves the full stop in the wording, where the author put it.
"""

_OPENING = r"(?P<head>\s*{prefix}\s*\d+){ordinal}?"
"""A caption's opening: the prefix, the figure number, and the ordinal if any."""


@dataclass(frozen=True)
class _Span:
    """One role's character range within a caption's text."""

    start: int
    end: int
    role: str


def format_figure_caption(paragraph: Element, *, prefix: str) -> bool:
    """Give *paragraph* the shape of a figure caption, returning whether it changed.

    *prefix* is the caption's marker as the document spells it — ``Fig.``,
    ``Figure``, ``图`` — matched case-insensitively at the start of the paragraph,
    after any leading whitespace. A prefix is passed in rather than assumed
    because the specification already carries the accepted spellings
    (:attr:`~thesis_refinery.style.CaptionSpec.image_prefixes`), and a second
    copy of them here is a second answer to the same question.

    On success the runs are replaced so that ``prefix N`` is bold, a following
    ``-N`` or ``.N`` ordinal is a superscript, and everything else keeps the
    properties its run had, minus bold. ``False`` means the paragraph was not
    touched: it does not open with *prefix*, or it is not a plain-text paragraph,
    or it already had exactly this shape.
    """
    return _format(paragraph, prefix=prefix, superscript_tokens=())


def format_spectrum_caption(
    paragraph: Element,
    *,
    prefix: str,
    superscript_tokens: Sequence[str],
) -> bool:
    """As :func:`format_figure_caption`, and superscript a spectrum kind's digits.

    ``1H-NMR`` is written ``¹H-NMR``: the nucleus's mass number is a superscript
    while the element symbol is not. *superscript_tokens* names the kinds to look
    for — a manifest's ``order`` is exactly that list — and, for each occurrence
    found in the caption after the figure number, the leading digits of that
    occurrence are superscripted. That single rule covers both ways a caller
    spells the list:

    * ``("1H-NMR", "13C-NMR")`` — the whole kind; its leading digits are
      superscripted and the rest is left alone;
    * ``("1", "13")`` — just the mass numbers; the digit run at the match is
      taken whole, so ``1`` matches inside ``13C`` at the same offset and yields
      the same ``13``.

    The digits are superscripted only when an upper-case letter follows them,
    because that is what makes them a nucleus and not a number in the sentence:
    ``13C-NMR`` qualifies, ``1 μM`` does not.

    Calling this on a caption already formatted by :func:`format_figure_caption`
    is the intended order and safe. The reverse order strips the nucleus
    superscript, since the figure form has no tokens to place and treats the
    whole tail as plain — which is why the two functions are documented together
    rather than as independent tools.
    """
    return _format(paragraph, prefix=prefix, superscript_tokens=tuple(superscript_tokens))


# ---------------------------------------------------------------------------
# The shape, as character ranges
# ---------------------------------------------------------------------------


def _format(paragraph: Element, *, prefix: str, superscript_tokens: Sequence[str]) -> bool:
    """Recompute *paragraph*'s run layout, returning whether anything changed."""
    runs = _text_runs(paragraph)
    if not runs:
        return False

    # The text is read *from the runs*, so the offsets below are offsets into the
    # characters this function is about to cut up. Reading it from the paragraph
    # instead would be equivalent only as long as `_text_runs` keeps refusing
    # everything that contributes text some other way -- an invariant worth not
    # depending on.
    text = "".join(text_of(run) for run in runs)
    spans = _spans(text, prefix=prefix, superscript_tokens=superscript_tokens)
    if spans is None:
        return False

    replacements = _rebuild(runs, spans)
    if not replacements:
        return False

    before = _serialised(paragraph)
    anchor = runs[0]
    for run in replacements:
        anchor.addprevious(run)
    for run in runs:
        paragraph.remove(run)
    return _serialised(paragraph) != before


def _spans(
    text: str,
    *,
    prefix: str,
    superscript_tokens: Sequence[str],
) -> list[_Span] | None:
    """Every role's range in *text*, or ``None`` when *prefix* does not open it.

    The ranges tile the whole string: the gaps between the ranges the roles claim
    are filled with :data:`_PLAIN`, so the rebuild has a role for every character
    and no character is left to be silently dropped or duplicated.
    """
    opening = re.compile(
        _OPENING.format(prefix=re.escape(prefix), ordinal=_ORDINAL),
        re.IGNORECASE,
    )
    match = opening.match(text)
    if match is None:
        return None

    spans = [_Span(*match.span("head"), _BOLD)]
    if match.group("ordinal"):
        spans.append(_Span(*match.span("ordinal"), _SUPERSCRIPT))
    spans.extend(_nucleus_spans(text, match.end(), superscript_tokens))
    return _with_gaps(text, spans)


def _nucleus_spans(text: str, start: int, tokens: Sequence[str]) -> list[_Span]:
    """Superscript ranges for the mass numbers named by *tokens*, after *start*.

    Only the text after the figure number is searched. The number itself is a
    bold region, and a token that names the bare digit ``1`` would otherwise
    claim the ``1`` of ``Fig. 1`` — superscripting the figure's own number, which
    is a defect that reads as a rendering quirk rather than as a rule that fired
    in the wrong place.
    """
    tail = text[start:]
    claimed: list[tuple[int, int]] = []

    for token in sorted({token for token in tokens if token}, key=len, reverse=True):
        if not token[:1].isdigit():
            # A kind with no mass number (`MS`, `IR`) has nothing to superscript.
            continue
        for occurrence in re.finditer(re.escape(token), tail):
            end = occurrence.start()
            while end < len(tail) and tail[end].isdigit():
                end += 1
            if end >= len(tail) or not "A" <= tail[end] <= "Z":
                continue
            claimed.append((start + occurrence.start(), start + end))

    kept: list[tuple[int, int]] = []
    for span in sorted(claimed, key=lambda span: (span[0], -span[1])):
        if kept and span[0] < kept[-1][1]:
            # Longest wins at a given offset, so `1` and `13` both naming the same
            # nucleus agree instead of producing two overlapping runs.
            continue
        kept.append(span)
    return [_Span(span_start, span_end, _SUPERSCRIPT) for span_start, span_end in kept]


def _with_gaps(text: str, spans: Sequence[_Span]) -> list[_Span]:
    """*spans* plus a plain span for every character they leave uncovered."""
    filled: list[_Span] = []
    position = 0
    for span in sorted(spans, key=lambda span: span.start):
        if span.start > position:
            filled.append(_Span(position, span.start, _PLAIN))
        filled.append(span)
        position = span.end
    if position < len(text):
        filled.append(_Span(position, len(text), _PLAIN))
    return filled


# ---------------------------------------------------------------------------
# The shape, as runs
# ---------------------------------------------------------------------------


def _text_runs(paragraph: Element) -> list[Element]:
    """The paragraph's runs when the paragraph is nothing but plain text runs.

    The returned list is empty for a paragraph this module must not touch: one
    with a ``w:hyperlink``, a ``w:fldSimple``, a bookmark, a proofing mark or a
    content control as a direct child, or a run that carries anything besides
    ``w:rPr`` and ``w:t`` (a field char, an inline picture, a tab, a footnote
    reference). Those all contribute text or content this module cannot map back
    onto character offsets, and a caption whose ``w:tab`` was rewritten as a
    literal tab character in a ``w:t`` is a caption Word renders differently.

    It is the *presence* of those children that makes "the text of this
    paragraph" ambiguous, not their decoration, so there is no allow-list to
    maintain here: a caption this refuses keeps the text it had and reports no
    change, which is a visible outcome rather than a silently mangled one.
    """
    runs: list[Element] = []
    for child in paragraph:
        name = local_name(child.tag)
        if name == _PARAGRAPH_PROPERTIES_LOCAL:
            continue
        if name != _RUN_LOCAL or not _plain_run(child):
            return []
        runs.append(child)
    return runs


def _plain_run(run: Element) -> bool:
    """True when the run holds only text: ``w:rPr`` and ``w:t`` children."""
    return all(
        local_name(child.tag) in (_RUN_PROPERTIES_LOCAL, _TEXT_LOCAL) for child in run
    )


def _rebuild(runs: Sequence[Element], spans: Sequence[_Span]) -> list[Element]:
    """The runs that express *spans* over *runs*, in document order.

    Each new run is the intersection of one span with one old run, which is what
    preserves run-level formatting the author set inside a caption — an
    italicised species name, a font the caption style does not name. A span that
    crosses an old run boundary becomes two new runs with different properties,
    and the rebuild is still a pure function of the text and the old runs, so a
    second pass cuts at the same offsets and produces the same elements.
    """
    texts = [text_of(run) for run in runs]
    rebuilt: list[Element] = []
    position = 0

    # `strict=False` preserves the existing behaviour exactly: `texts` is derived
    # from `runs`, so the two are the same length by construction and the strict
    # check could only ever fire on a bug in this function.
    for run, text in zip(runs, texts, strict=False):
        end = position + len(text)
        for span in spans:
            start = max(span.start, position)
            stop = min(span.end, end)
            if start >= stop:
                continue
            rebuilt.append(
                _run_like(run, text[start - position : stop - position], role=span.role)
            )
        position = end
    return rebuilt


def _run_like(source: Element, text: str, *, role: str) -> Element:
    """A run carrying *text*, with *source*'s properties adjusted for *role*."""
    run = new("w:r")
    properties = _copy_properties(source)
    _set_role(properties, role)
    if len(properties) or properties.attrib:
        # An empty `w:rPr` is noise Word would not have written, and dropping it
        # is also what keeps the second pass byte-identical: a plain run built
        # from properties that carry nothing comes back as a run with no `w:rPr`.
        ordered_insert(run, properties)

    text_element = new("w:t")
    text_element.set(_XML_SPACE, "preserve")
    text_element.text = text
    ordered_insert(run, text_element)
    return run


def _copy_properties(source: Element) -> Element:
    """*source*'s ``w:rPr``, copied, or an empty one to build on."""
    properties = find(source, _RUN_PROPERTIES)
    return deepcopy(properties) if properties is not None else new(_RUN_PROPERTIES)


def _set_role(properties: Element, role: str) -> None:
    """Make *properties* say *role*, and stop saying anything that contradicts it.

    Bold is decided here: the prefix carries ``w:b`` and everything else gives it
    up, including ``w:bCs``, which governs complex-script text and would leave a
    Chinese caption's ordinal bold while the Latin wording beside it was not.

    ``w:vertAlign`` is only ever *written*, never cleared. Clearing it would make
    :func:`format_figure_caption` undo :func:`format_spectrum_caption` on a
    caption that has already been through both, and the cost of not clearing is
    that a superscript the author put in the caption's own wording survives,
    which is what an author who typed one wanted.
    """
    for token in _BOLD_TOKENS:
        existing = find(properties, token)
        if existing is not None and role != _BOLD:
            properties.remove(existing)

    if role == _BOLD:
        bold = ensure_ordered(properties, "w:b")
        if attr(bold, "w:val") not in (None, *_TRUE_SPELLINGS):
            bold.set(q("val"), "true")

    if role == _SUPERSCRIPT:
        ensure_ordered(properties, _VERT_ALIGN).set(q("val"), _SUPERSCRIPT)


def _serialised(element: Element) -> bytes:
    """The element's bytes, for asking "did the rewrite actually change this?"."""
    return etree.tostring(element)
