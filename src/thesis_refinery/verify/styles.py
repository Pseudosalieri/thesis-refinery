"""Effective formatting: what a paragraph actually ends up looking like.

The problem this module solves is the reason a naive checker is worthless. A
paragraph's formatting comes from three places, in increasing precedence:

1. the document's ``w:docDefaults``,
2. its paragraph or character **style**, possibly through a ``w:basedOn`` chain,
3. its own ``w:pPr`` / ``w:rPr``.

A checker that only looks at (3) reports every paragraph as unformatted, because
pandoc puts almost nothing there and relies on styles. A checker that only looks
at the style cannot see the one paragraph somebody overrode by hand. Both
mistakes produce confident, wrong answers.

So this resolves the chain and answers "what is this paragraph's first-line
indent, in fact", which is the question the checks actually have.

Deliberately *not* implemented: the full OOXML cascade with ``w:docDefaults``
merging and toggle-property XOR semantics. That machinery has its own long list
of subtleties, and getting it subtly wrong would be worse than the honest
approximation here. What is implemented is direct-then-style-then-parent-style,
which covers every document Word and pandoc actually produce, and the checks
report the *source* of each value so a disagreement is diagnosable rather than
mysterious.
"""

from __future__ import annotations

from dataclasses import dataclass

from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.xmlq import Element, find, iter_descendants, local_name

__all__ = [
    "StyleDefinition",
    "StyleIndex",
    "effective_property",
    "paragraph_style_id",
    "property_source",
    "run_properties",
]

MAX_INHERITANCE_DEPTH = 32
"""A ``w:basedOn`` loop would otherwise hang the checker; Word tolerates them."""


@dataclass(frozen=True)
class StyleDefinition:
    """One ``w:style`` reduced to what checks need."""

    style_id: str
    name: str
    based_on: str | None
    properties: Element | None
    """The style's ``w:pPr`` (or ``w:rPr`` for a character style)."""

    run_properties: Element | None


class StyleIndex:
    """The document's styles, indexed for inheritance-aware lookup."""

    def __init__(self, styles_root: Element | None) -> None:
        self._definitions: dict[str, StyleDefinition] = {}
        if styles_root is not None:
            self._index(styles_root)

    @property
    def definitions(self) -> dict[str, StyleDefinition]:
        return dict(self._definitions)

    def _index(self, styles_root: Element) -> None:
        for style in iter_descendants(styles_root, "w:style"):
            style_id = style.get(q("styleId"))
            if not style_id:
                continue
            name_element = find(style, "w:name")
            based_on = find(style, "w:basedOn")
            self._definitions[style_id] = StyleDefinition(
                style_id=style_id,
                name=name_element.get(q("val"), style_id) if name_element is not None else style_id,
                based_on=based_on.get(q("val")) if based_on is not None else None,
                properties=find(style, "w:pPr"),
                run_properties=find(style, "w:rPr"),
            )

    def definition(self, style_id: str) -> StyleDefinition | None:
        return self._definitions.get(style_id)

    def chain(self, style_id: str | None) -> list[str]:
        """Style ids from most derived to least, stopping before a cycle repeats."""
        if not style_id:
            return []
        seen: list[str] = []
        current: str | None = style_id
        while current and current not in seen and len(seen) < MAX_INHERITANCE_DEPTH:
            seen.append(current)
            definition = self._definitions.get(current)
            current = definition.based_on if definition is not None else None
        return seen

    def inherited_property(self, style_id: str | None, tag: str) -> tuple[Element | None, str | None]:
        """The property *tag* from the most derived style that declares it.

        Returns the property element and the style id it came from, so a finding
        can say where the value was decided instead of just that it is wrong.
        """
        for candidate in self.chain(style_id):
            definition = self._definitions.get(candidate)
            if definition is None or definition.properties is None:
                continue
            found = find(definition.properties, tag)
            if found is not None:
                return found, candidate
        return None, None

    def inherited_run_property(self, style_id: str | None, tag: str) -> tuple[Element | None, str | None]:
        """As :meth:`inherited_property`, but for ``w:rPr`` children."""
        for candidate in self.chain(style_id):
            definition = self._definitions.get(candidate)
            if definition is None or definition.run_properties is None:
                continue
            found = find(definition.run_properties, tag)
            if found is not None:
                return found, candidate
        return None, None


def paragraph_style_id(paragraph: Element) -> str | None:
    """The paragraph's ``w:pStyle``, or ``None`` when it has none."""
    properties = find(paragraph, "w:pPr")
    if properties is None:
        return None
    style = find(properties, "w:pStyle")
    if style is None:
        return None
    return style.get(q("val"))


def _direct_property(paragraph: Element, tag: str) -> Element | None:
    properties = find(paragraph, "w:pPr")
    if properties is None:
        return None
    return find(properties, tag)


def effective_property(
    paragraph: Element, styles: StyleIndex, tag: str
) -> tuple[Element | None, str | None, str | None]:
    """Resolve *tag* for *paragraph*.

    Returns ``(property, source, detail)`` where *source* is ``"direct"``,
    ``"style"``, or ``None``, and *detail* names the style when the value came
    from one. The three-part answer is the point: "wrong indent" is not
    actionable, "wrong indent, inherited from Body Text" is.
    """
    direct = _direct_property(paragraph, tag)
    if direct is not None:
        return direct, "direct", None

    style_id = paragraph_style_id(paragraph)
    inherited, source_style = styles.inherited_property(style_id, tag)
    if inherited is not None:
        return inherited, "style", source_style
    return None, None, None


def property_source(paragraph: Element, styles: StyleIndex, tag: str) -> str:
    """Where *tag* comes from for *paragraph*: ``direct``, ``style``, or ``none``."""
    _, source, _ = effective_property(paragraph, styles, tag)
    return source or "none"


def run_properties(paragraph: Element) -> Element | None:
    """The ``w:rPr`` of the paragraph mark, for paragraph-level run formatting.

    This is ``w:pPr/w:rPr`` — the formatting of the paragraph mark and the
    default for its runs — not the ``w:rPr`` inside any particular ``w:r``.
    """
    properties = find(paragraph, "w:pPr")
    if properties is None:
        return None
    return find(properties, "w:rPr")


def paragraph_text_style_ids(paragraph: Element) -> list[str]:
    """Run styles used inside a paragraph, in order.

    Used to find paragraphs that mix, say, a caption style with body text, which
    is a common and invisible defect: the paragraph looks like a caption, and one
    line of it is not.
    """
    found: list[str] = []
    for run in iter_descendants(paragraph, "w:r"):
        run_properties_element = find(run, "w:rPr")
        if run_properties_element is None:
            continue
        style = find(run_properties_element, "w:rStyle")
        if style is not None:
            found.append(style.get(q("val"), ""))
    return found


def child_names(element: Element | None) -> list[str]:
    """Local names of an element's children, for diagnostics."""
    if element is None:
        return []
    return [local_name(child.tag) for child in element]
