"""Software-reported terms, with the descriptor set as a subscript.

A modelling package reports its results as flat text — ``ΔG_Bind``,
``ΔG_Bind_Coulomb``, ``ΔG_solv`` — and a thesis that transcribes those strings
verbatim prints a row of underscores where the notation calls for a subscript.
The correction is small, mechanical, and exactly the sort of edit a human makes
once and then forgets in the next revision, so it belongs in the build.

Three decisions shape this module.

**The characters do not change; the boundary does.** The transformation splits a
run at a character offset and marks the tail ``w:vertAlign``/``subscript``. Not
one character is added or deleted — not even an underscore the author typed as a
stand-in for the subscript. That constraint is what makes the stage compatible
with everything downstream: the ledger's paragraph diff, the caption text
comparison, and any check that asks whether the wording changed. A stage that
quietly deleted a separator would look like a content edit in every diff.

**The count assertion is not decoration.** This stage's failure mode is silence:
a pattern that matches nothing returns ``0``, which is indistinguishable from
"the document needed nothing". By the time a reader notices, the term is in the
printed thesis. ``expect`` therefore turns the caller's belief about the
document into something the build can check — *and it is checked before anything
is modified*, so a build that fails the assertion leaves the document exactly as
it found it. ``expect=0`` is a legitimate assertion too: "this document contains
no such term at all", which is the thing to assert immediately after a run.

**A run that is not simply text is left alone.** The split is computed from the
run's characters, which means the characters have to *be* the run's ``w:t``
children. A run that also carries a tab, a line break, a footnote reference or a
field is skipped rather than guessed at, and a skipped run shows up as an
``expect`` mismatch rather than as a wrong document.

The pattern is the module's only piece of domain knowledge, and it is a public
constant for that reason — see :data:`TERM_PATTERN`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.xmlq import (
    Element,
    deepcopy,
    ensure_ordered,
    find,
    iter_descendants,
    local_name,
    new,
    ordered_insert,
    text_of,
)

__all__ = [
    "SUBSCRIPT",
    "TERM_PATTERN",
    "SubscriptCountError",
    "apply_subscripts",
    "find_terms",
]

TERM_PATTERN = r"Δ[A-Za-z]_?(?P<subscript>[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)"
"""What a software-reported term looks like, as a regular expression.

This one constant is the whole of this module's domain knowledge; everything
else here is run surgery that would work just as well on a different notation.

It reads a Greek capital delta, one Latin letter (the quantity: ``G`` for a free
energy, ``E`` for an interaction energy, ``H``, ``S``), an optional underscore
the author typed as a separator, and then the descriptor, which may itself be
underscored — ``Bind``, ``Bind_Coulomb``, ``solv``.

The subscript boundary is the pattern's ``subscript`` group, and it is *not*
immediately after the Greek letter. ``ΔG`` is read as one symbol and what these
packages vary is the descriptor, so the notation a thesis sets is
``ΔG`` + subscripted descriptor. Subscripting from the delta would print a
``G`` inside the subscript, which is not the notation any of these packages'
results is transcribed into. A thesis whose notation really does subscript from
the Greek letter passes its own pattern; the boundary is the group, wherever the
group is. A pattern with no group at all subscripts its entire match.

Two consequences worth knowing before widening it:

* the leading ``Δ`` is required, so a chemical shift written ``δ 7.26`` is not a
  term and is left alone — widening the class to ``[Δδ]`` would subscript the
  nucleus of ``δH``, which is a different notation with a different meaning;
* only ASCII descriptors are matched, which is the right scope for text a
  modelling package reported. A term translated into Chinese is a term an author
  wrote, and no pattern should guess where its subscript ends.
"""

SUBSCRIPT = "subscript"
"""The ``w:vertAlign`` value that makes a run a subscript."""

_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
"""Written on every ``w:t`` because a segment may begin or end with a space."""

_RUN_PROPERTIES = "w:rPr"
_RUN_PROPERTIES_LOCAL = "rPr"
_TEXT = "w:t"
_TEXT_LOCAL = "t"


class SubscriptCountError(ValueError):
    """Raised when the number of terms found is not the number the caller expected.

    This is a *build* error rather than a document defect: it says the document
    does not contain what the caller believes it contains. The two ways to get
    here are a pattern that has stopped matching the document's notation and a
    document that has been re-rendered from sources where the terms moved — and
    both are silent without the assertion, because a stage that matches nothing
    exits successfully.
    """


def find_terms(
    root: Element,
    *,
    pattern: str | re.Pattern[str] = TERM_PATTERN,
) -> list[Element]:
    """Every run whose text matches *pattern*, in document order.

    A query, not a worklist: a run whose match is *already* subscripted is still
    returned. Reporting what the document contains and reporting what a run of
    :func:`apply_subscripts` would change are two different questions, and
    answering the first with the second would hide the fact that a term exists
    but is formatted the way the caller wants. After a successful
    :func:`apply_subscripts` the term is split across two runs, so no single run
    matches any more and this returns an empty list — which is the cheapest way
    to see that the pass did what it claimed.
    """
    compiled = _compile(pattern)
    return [run for run in iter_descendants(root, "w:r") if compiled.search(text_of(run))]


def apply_subscripts(
    root: Element,
    *,
    pattern: str | re.Pattern[str] = TERM_PATTERN,
    expect: int | None = None,
) -> int:
    """Set the descriptor of every matched term in *root* as a subscript.

    Returns how many runs were changed — runs, not terms: a paragraph whose
    sentence contains two terms in one run is one changed run, and a run whose
    text ends up identical to what it was (an already-subscripted match, for
    instance) is not counted at all. That makes the return value idempotent by
    construction: a second call returns ``0`` and writes nothing.

    *expect* is the number of runs the caller believes this pass will change.
    When it is given and the work found does not match, a
    :class:`SubscriptCountError` is raised **before anything is modified**, so
    the caller can retry against a document that is still the input.

    The revision count is deliberately *not* reported as "terms found". Runs are
    what this function rewrites, and the two numbers differ the moment a
    sentence holds two terms.
    """
    compiled = _compile(pattern)

    # Plan first, modify second. `expect` has to be able to fail a build without
    # leaving half a document behind, and a plan is also what makes the count
    # mean "changes" rather than "candidates": a run that would be rewritten into
    # exactly what it already is never enters the plan.
    plan: list[tuple[Element, list[Element]]] = []
    for run in iter_descendants(root, "w:r"):
        replacement = _replacement(run, compiled)
        if replacement is not None:
            plan.append((run, replacement))

    if expect is not None and len(plan) != expect:
        raise SubscriptCountError(
            f"expected {expect} run(s) to change, found {len(plan)}; "
            "a pattern that matches nothing is indistinguishable from a document that "
            "needed nothing, which is why this is an error rather than a warning"
        )

    for run, replacement in plan:
        _replace(run, replacement)
    return len(plan)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _compile(pattern: str | re.Pattern[str]) -> re.Pattern[str]:
    """Compile *pattern*, refusing anything that is neither text nor a pattern."""
    if isinstance(pattern, re.Pattern):
        return pattern
    if not isinstance(pattern, str):
        raise TypeError(f"pattern must be a string or a compiled pattern, got {type(pattern).__name__}")
    return re.compile(pattern)


def _replacement(run: Element, compiled: re.Pattern[str]) -> list[Element] | None:
    """The runs that should replace *run*, or ``None`` when it needs no change."""
    if not _text_only(run):
        return None

    text = text_of(run)
    spans = _spans(text, compiled)
    if not spans:
        return None

    segments = _segments(text, spans)
    if len(segments) == 1:
        _segment, subscript = segments[0]
        if not subscript or _is_subscript(run):
            # Either the whole run is already plain text, or the whole run is the
            # descriptor and it is subscript already: both are the state the
            # caller asked for, so neither is a change.
            return None

    properties = find(run, _RUN_PROPERTIES)
    return [
        _run_like(properties, segment, subscript=subscript)
        for segment, subscript in segments
    ]


def _spans(text: str, compiled: re.Pattern[str]) -> list[tuple[int, int]]:
    """The character ranges that must become subscript, ascending and disjoint.

    The range is the pattern's first capturing group when it has one — that is
    the module's convention for "this part is the descriptor" — and the whole
    match otherwise. An empty group is dropped: a zero-width subscript run would
    be a run with no text, which Word keeps and no reader can see.
    """
    found: list[tuple[int, int]] = []
    for match in compiled.finditer(text):
        start, end = match.span(1) if compiled.groups else match.span()
        if start < 0 or end <= start:
            continue
        if found and start < found[-1][1]:
            # Overlapping groups cannot be split into runs in order; the earlier
            # match wins, which keeps the result a function of the text alone.
            continue
        found.append((start, end))
    return found


def _segments(text: str, spans: Sequence[tuple[int, int]]) -> list[tuple[str, bool]]:
    """*text* cut at *spans*, as ``(segment, is_subscript)`` pairs.

    Empty segments are dropped rather than turned into empty runs, so a term at
    the very start or end of a run produces two runs and not three.
    """
    segments: list[tuple[str, bool]] = []
    position = 0

    def push(start: int, end: int, subscript: bool) -> None:
        if end > start:
            segments.append((text[start:end], subscript))

    for start, end in spans:
        push(position, start, False)
        push(start, end, True)
        position = end
    push(position, len(text), False)
    return segments


def _text_only(run: Element) -> bool:
    """True when the run's characters are exactly its ``w:t`` children.

    A run may legally hold a tab, a line break, a hyphen, a picture, a field or a
    footnote reference, and :func:`~thesis_refinery.docxlib.xmlq.text_of` counts
    several of them as text. Splitting such a run by character offset would move
    the boundary into a character the ``w:t`` element does not own, so it is
    skipped. Skipping is safe *because* it is visible: the run does not enter the
    plan, and a caller who passed ``expect`` gets a
    :class:`SubscriptCountError` instead of a document with an unformatted term.
    """
    if not text_of(run):
        return False
    for child in run:
        name = local_name(child.tag)
        if name not in (_RUN_PROPERTIES_LOCAL, _TEXT_LOCAL):
            return False
    return True


def _is_subscript(run: Element) -> bool:
    properties = find(run, _RUN_PROPERTIES)
    if properties is None:
        return False
    alignment = find(properties, "w:vertAlign")
    return alignment is not None and alignment.get(q("val")) == SUBSCRIPT


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def _run_like(source: Element | None, text: str, *, subscript: bool) -> Element:
    """A run carrying *text*, with *source*'s properties and the subscript set.

    The source properties are copied rather than rebuilt, because this stage is
    asked to change one thing. A term inside an italic sentence, or in a run that
    names a font the caption style does not, keeps every property it had; only
    ``w:vertAlign`` is added, and only on the descriptor.
    """
    run = new("w:r")
    properties = deepcopy(source) if source is not None else None
    if subscript:
        if properties is None:
            # The run had no properties at all, which is the common case for a
            # paragraph nobody has formatted by hand. The subscript is still a
            # property, so there has to be somewhere to write it.
            properties = new(_RUN_PROPERTIES)
        ensure_ordered(properties, "w:vertAlign").set(q("val"), SUBSCRIPT)
    if properties is not None:
        ordered_insert(run, properties)

    text_element = new(_TEXT)
    text_element.set(_XML_SPACE, "preserve")
    text_element.text = text
    ordered_insert(run, text_element)
    return run


def _replace(run: Element, replacement: Sequence[Element]) -> None:
    """Put *replacement* where *run* is, leaving the surrounding run order alone."""
    parent = run.getparent()
    if parent is None:
        return
    for element in replacement:
        run.addprevious(element)
    parent.remove(run)
