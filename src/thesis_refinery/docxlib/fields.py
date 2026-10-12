"""Field codes: ``STYLEREF``, ``PAGE``, ``NUMPAGES``, and ``w:updateFields``.

A field in WordprocessingML is either a ``w:fldSimple`` element or a run
sequence of ``begin`` / instruction / ``separate`` / cached result / ``end``.
Both are produced here, because they differ in ways that matter:

* ``w:fldSimple`` is one element and cannot carry run formatting on its cached
  result. Fine for a page number in a footer.
* The complex form lets the cached result carry its own ``w:rPr``, which is what
  a running head needs: the header text has a font size, and a simple field would
  render at the paragraph default until Word refreshed it.

The instruction text always carries ``xml:space="preserve"``. Word delimits
field arguments by spaces, so a field whose instruction was stripped of its
leading and trailing spaces is a field Word may refuse to parse — and it will
show the instruction instead of the result.

One warning about :func:`set_update_fields`: it makes Word refresh every field
when the document is opened. That is usually what you want for a table of
contents, and it is also how a hand-placed form placeholder (a literal ``XXX``
the author intends to fill in by hand) gets replaced by an empty field result.
Turn it on deliberately, not by habit.
"""

from __future__ import annotations

from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.xmlq import (
    Element,
    deepcopy,
    ensure_ordered,
    find,
    findall,
    iter_descendants,
    new,
)

__all__ = [
    "FIELD_BEGIN",
    "FIELD_END",
    "FIELD_SEPARATE",
    "complex_field",
    "contains_field",
    "field_char",
    "field_simple",
    "iter_instructions",
    "num_pages_field",
    "page_number_field",
    "paragraphs_with_fields",
    "remove_update_fields",
    "set_update_fields",
    "styleref_field",
    "update_fields_enabled",
]

FIELD_BEGIN = "begin"
FIELD_SEPARATE = "separate"
FIELD_END = "end"

_INSTRUCTION_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def field_char(kind: str, *, dirty: bool = False) -> Element:
    """A ``w:r`` wrapping a ``w:fldChar`` of *kind*."""
    if kind not in (FIELD_BEGIN, FIELD_SEPARATE, FIELD_END):
        raise ValueError(f"field char type must be begin/separate/end, got {kind!r}")

    run = new("w:r")
    char = new("w:fldChar", **{"w:fldCharType": kind})
    if dirty:
        char.set(q("dirty"), "true")
    run.append(char)
    return run


def _instruction_run(instruction: str) -> Element:
    run = new("w:r")
    text = new("w:instrText")
    # Word delimits field arguments by spaces; stripping them changes the field.
    text.set(_INSTRUCTION_XML_SPACE, "preserve")
    text.text = f" {instruction.strip()} "
    run.append(text)
    return run


def _result_run(result: str, run_properties: Element | None = None) -> Element:
    run = new("w:r")
    if run_properties is not None:
        run.append(deepcopy(run_properties))
    text = new("w:t")
    text.set(_INSTRUCTION_XML_SPACE, "preserve")
    text.text = result
    run.append(text)
    return run


def complex_field(
    instruction: str,
    result: str = "",
    *,
    run_properties: Element | None = None,
) -> list[Element]:
    """The run sequence for a complex field, ready to append to a paragraph.

    A cached *result* is included on purpose. Leaving it out produces a field
    whose value is blank until Word refreshes it, which means the document as
    committed does not show what the author will see.
    """
    return [
        field_char(FIELD_BEGIN),
        _instruction_run(instruction),
        field_char(FIELD_SEPARATE),
        _result_run(result, run_properties),
        field_char(FIELD_END),
    ]


def field_simple(instruction: str, result: str = "") -> Element:
    """A ``w:fldSimple`` element, ready to append to a paragraph."""
    element = new("w:fldSimple", **{"w:instr": f" {instruction.strip()} "})
    element.append(_result_run(result))
    return element


# ---------------------------------------------------------------------------
# The fields this project actually uses
# ---------------------------------------------------------------------------


def styleref_field(style_name: str, *, result: str = "", run_properties: Element | None = None) -> list[Element]:
    """A ``STYLEREF`` field, i.e. "the text of the last heading of this style".

    This is how an even-page running head shows a chapter title: the field is
    resolved by Word as it lays pages out, so the header tracks the content
    instead of being maintained by hand.

    The instruction is quoted because a style name may contain spaces, and is
    escaped because a style name may contain a double quote.
    """
    escaped = style_name.replace('"', '\\"')
    return complex_field(
        f'STYLEREF "{escaped}" \\* MERGEFORMAT',
        result,
        run_properties=run_properties,
    )


def page_number_field(*, result: str = "1", run_properties: Element | None = None) -> list[Element]:
    """A ``PAGE`` field."""
    return complex_field("PAGE \\* MERGEFORMAT", result, run_properties=run_properties)


def num_pages_field(*, result: str = "", run_properties: Element | None = None) -> list[Element]:
    """A ``NUMPAGES`` field, i.e. the total page count."""
    return complex_field("NUMPAGES \\* MERGEFORMAT", result, run_properties=run_properties)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def iter_instructions(element: Element) -> list[str]:
    """Every field instruction in *element*, in document order.

    Covers both spellings: the ``w:instrText`` runs of a complex field and the
    ``w:instr`` attribute of a ``w:fldSimple``.
    """
    instructions: list[str] = []

    for instruction in iter_descendants(element, "w:instrText"):
        instructions.append(instruction.text or "")

    for simple in iter_descendants(element, "w:fldSimple"):
        instructions.append(simple.get(q("instr"), ""))

    return instructions


def contains_field(element: Element, needle: str) -> bool:
    """True when any field instruction in *element* mentions *needle*."""
    return any(needle in instruction for instruction in iter_instructions(element))


# ---------------------------------------------------------------------------
# Document settings
# ---------------------------------------------------------------------------


def set_update_fields(settings: Element, enabled: bool = True) -> Element:
    """Declare ``w:updateFields`` in a ``w:settings`` part.

    This always *writes* the element, including for ``enabled=False``, so that
    "explicitly off" is a state the document can be in. Use
    :func:`remove_update_fields` to go back to "not declared at all"; the two are
    different, and :func:`update_fields_enabled` reports which one you have.

    Read the module docstring before turning this on: it refreshes *every* field
    on open, including literals an author intends to fill in by hand.
    """
    element = ensure_ordered(settings, "w:updateFields")
    element.set(q("val"), "true" if enabled else "false")
    return settings


def remove_update_fields(settings: Element) -> Element:
    """Remove the ``w:updateFields`` declaration, returning to the inherited default."""
    element = find(settings, "w:updateFields")
    if element is not None:
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
    return settings


def update_fields_enabled(settings: Element) -> bool | None:
    """``True``/``False`` when ``w:updateFields`` is declared, ``None`` when absent.

    Three states rather than two: "absent" and "explicitly false" mean different
    things, and collapsing them hides a setting somebody deliberately turned off.
    """
    element = find(settings, "w:updateFields")
    if element is None:
        return None
    return element.get(q("val"), "true") not in ("false", "0", "off")


def paragraphs_with_fields(root: Element) -> list[Element]:
    """Direct ``w:p`` children of *root* that contain at least one field."""
    return [p for p in findall(root, "w:p") if iter_instructions(p)]
