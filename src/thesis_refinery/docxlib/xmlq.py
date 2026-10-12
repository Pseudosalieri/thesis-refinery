"""Querying and building OOXML elements without magic strings.

Three things this module is opinionated about.

**Element order is part of the schema.** OOXML is sequence-typed: ``w:pPr``'s
children must appear in a fixed order, and Word rejects a document where they do
not — usually with the unhelpful message that the file is corrupt. Hand-written
``parent.append(child)`` is therefore a latent bug. Use :func:`ordered_insert` or
:func:`ensure_ordered`, which place the child where the schema requires.

**Text extraction is defined, not incidental.** :func:`text_of` says exactly what
it counts as text, so two callers cannot disagree about whether a tab is a
character.

**Unknown is an error, not a guess.** :func:`ordered_insert` raises when it does
not know the parent's child order, rather than appending and hoping. Pass
``on_unknown="append"`` when appending really is what you mean.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Sequence

from lxml import etree

from thesis_refinery.docxlib.ns import q, qn
from thesis_refinery.docxlib.parse import parser

__all__ = [
    "SCHEMA_ORDER",
    "UnsupportedElementOrder",
    "attr",
    "children",
    "deepcopy",
    "ensure_ordered",
    "find",
    "findall",
    "has_schema_order",
    "iter_descendants",
    "local_name",
    "new",
    "ordered_insert",
    "paragraph_text",
    "remove",
    "sub",
    "text_of",
]

Element = etree._Element


class UnsupportedElementOrder(ValueError):
    """Raised when a child cannot be placed because the order is not known."""


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def local_name(tag: str) -> str:
    """``'{http://...}pPr'`` → ``'pPr'``."""
    return tag.rpartition("}")[2]


# ---------------------------------------------------------------------------
# Element order, per ECMA-376
# ---------------------------------------------------------------------------
# Only the parents this pipeline actually modifies are listed. Adding a parent
# here is how you make it safe to insert into; leaving one out is what makes
# `ordered_insert` refuse rather than guess.
SCHEMA_ORDER: dict[str, tuple[str, ...]] = {
    # CT_PPr
    "pPr": (
        "pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl",
        "numPr", "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens",
        "kinsoku", "wordWrap", "overflowPunct", "topLinePunct", "autoSpaceDE",
        "autoSpaceDN", "bidi", "adjustRightInd", "snapToGrid", "spacing", "ind",
        "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc", "textDirection",
        "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr",
        "sectPr", "pPrChange",
    ),
    # CT_RPr
    "rPr": (
        "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike",
        "dstrike", "outline", "shadow", "emboss", "imprint", "noProof", "snapToGrid",
        "vanish", "webHidden", "color", "spacing", "w", "kern", "position", "sz",
        "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText", "vertAlign",
        "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath",
        "rPrChange",
    ),
    # CT_SectPr
    "sectPr": (
        "footnotePr", "endnotePr", "type", "pgSz", "pgMar", "paperSrc", "pgBorders",
        "lnNumType", "pgNumType", "cols", "formProt", "vAlign", "noEndnote",
        "titlePg", "textDirection", "bidi", "rtlGutter", "docGrid", "printerSettings",
        "sectPrChange",
    ),
    # CT_TblPr
    "tblPr": (
        "tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize",
        "tblStyleColBandSize", "tblW", "jc", "tblCellSpacing", "tblInd",
        "tblBorders", "shd", "tblLayout", "tblCellMar", "tblLook", "tblCaption",
        "tblDescription", "tblPrChange",
    ),
    # CT_Tbl: tblPr, tblGrid, then any number of rows
    "tbl": ("tblPr", "tblGrid", "tr", "customXml", "sdt", "bookmarkStart",
            "bookmarkEnd", "commentRangeStart", "commentRangeEnd", "proofErr",
            "permStart", "permEnd", "ins", "del", "moveFromRangeStart",
            "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd"),
    # CT_TcPr
    "tcPr": (
        "cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders", "shd",
        "noWrap", "tcMar", "textDirection", "tcFitText", "vAlign", "hideMark",
        "headers", "cellIns", "cellDel", "cellMerge", "tcPrChange",
    ),
    # CT_TblBorders. `start`/`end` are the later-edition spellings of `left`/`right`;
    # both are accepted because documents in the wild contain both.
    "tblBorders": ("top", "start", "left", "bottom", "end", "right", "insideH",
                   "insideV", "tl2br", "tr2bl"),
    # CT_TcBorders
    "tcBorders": ("top", "start", "left", "bottom", "end", "right", "insideH",
                  "insideV", "tl2br", "tr2bl"),
    # CT_PBdr
    "pBdr": ("top", "left", "bottom", "right", "between", "bar"),
    # CT_NumPr
    "numPr": ("ilvl", "numId", "numberingChange", "ins"),
    # CT_Types: every Default must precede every Override, or Word rejects the
    # package even though the content is otherwise fine.
    "Types": ("Default", "Override"),
    # CT_Relationships is a homogeneous sequence; listed so inserts are explicit
    "Relationships": ("Relationship",),
    # CT_DocGrid is attribute-only, but listing it documents that it has no children
    "docGrid": (),
}


def has_schema_order(parent: Element) -> bool:
    """True when :func:`ordered_insert` knows where children of *parent* go."""
    return local_name(parent.tag) in SCHEMA_ORDER


# ---------------------------------------------------------------------------
# Constructing and finding
# ---------------------------------------------------------------------------


def new(prefixed_name: str, **attributes: str) -> Element:
    """Create a detached element, e.g. ``new("w:spacing", **{"w:line": "360"})``.

    Attribute names are prefixed names (``w:val``) or plain names for
    attributes in no namespace.

    The element carries **no namespace declaration of its own**. python-docx's
    ``OxmlElement`` cannot be used here: it only knows python-docx's prefixes,
    and this project also writes OPC parts (``ct:``, ``pr:``) whose prefixes
    python-docx keeps private. Leaving the declaration out is also what we want,
    because once the element sits in a tree that already declares the namespace,
    lxml serialises it with that declaration instead of adding a redundant one.
    """
    element = parser().makeelement(qn(prefixed_name))
    for name, value in attributes.items():
        element.set(qn(name) if ":" in name else name, value)
    return element


def find(parent: Element, prefixed_name: str, **attributes: str) -> Element | None:
    """First direct child matching name and attributes."""
    for child in parent:
        if child.tag == qn(prefixed_name) and _matches(child, attributes):
            return child
    return None


def findall(parent: Element, prefixed_name: str, **attributes: str) -> list[Element]:
    """All direct children matching name and attributes."""
    tag = qn(prefixed_name)
    return [c for c in parent if c.tag == tag and _matches(c, attributes)]


def iter_descendants(parent: Element, prefixed_name: str | None = None) -> Iterator[Element]:
    """Every descendant, optionally filtered by prefixed name."""
    tag = qn(prefixed_name) if prefixed_name else None
    for element in parent.iter():
        if element is parent:
            continue
        if tag is None or element.tag == tag:
            yield element


def _matches(element: Element, attributes: dict[str, str]) -> bool:
    for name, value in attributes.items():
        if element.get(qn(name) if ":" in name else name) != value:
            return False
    return True


def sub(parent: Element, prefixed_name: str, **attributes: str) -> Element:
    """Create a child, insert it in schema order, and return it."""
    return ordered_insert(parent, new(prefixed_name, **attributes))


def ensure_ordered(parent: Element, prefixed_name: str, **attributes: str) -> Element:
    """Return the matching child, creating (in schema order) if absent."""
    existing = find(parent, prefixed_name, **attributes)
    if existing is not None:
        return existing
    return sub(parent, prefixed_name, **attributes)


def ordered_insert(
    parent: Element, child: Element, *, on_unknown: str = "error"
) -> Element:
    """Insert *child* into *parent* at the position the schema requires.

    Children whose order is unknown are stepped over rather than used as an
    anchor, so an unexpected extension element cannot push the new child to an
    invalid position. When no later sibling exists, the child is appended, which
    is correct for the sequence-typed content models here.

    ``on_unknown`` controls what happens when the *parent* or the *child* is not
    in :data:`SCHEMA_ORDER`: ``"error"`` (default) raises, ``"append"`` appends.
    """
    order = SCHEMA_ORDER.get(local_name(parent.tag))
    if order is None:
        if on_unknown == "append":
            parent.append(child)
            return child
        raise UnsupportedElementOrder(
            f"do not know the child order of <{local_name(parent.tag)}>; "
            "add it to xmlq.SCHEMA_ORDER or pass on_unknown='append'"
        )

    key = local_name(child.tag)
    try:
        wanted = order.index(key)
    except ValueError:
        if on_unknown == "append":
            parent.append(child)
            return child
        raise UnsupportedElementOrder(
            f"<{key}> is not a known child of <{local_name(parent.tag)}>"
        ) from None

    for existing in parent:
        name = local_name(existing.tag)
        if name not in order:
            continue
        if order.index(name) > wanted:
            existing.addprevious(child)
            return child

    parent.append(child)
    return child


# ---------------------------------------------------------------------------
# Attributes
# ---------------------------------------------------------------------------


def attr(element: Element, name: str, default: str | None = None) -> str | None:
    """Read an attribute by prefixed (``w:val``) or plain name."""
    return element.get(qn(name) if ":" in name else name, default)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
# Defined explicitly so that a checker and an applier can never disagree about
# whether a tab or a line break counts as text.
_TEXT_TAGS: dict[str, str] = {
    q("t"): "",
    q("tab"): "\t",
    q("br"): "\n",
    q("cr"): "\n",
    q("noBreakHyphen"): "-",
    q("softHyphen"): "",
}


def text_of(element: Element) -> str:
    """Concatenated text of *element*.

    ``w:t`` contributes its characters, ``w:tab`` a tab, ``w:br``/``w:cr`` a
    newline, ``w:noBreakHyphen`` a hyphen. Everything else, including field codes
    and deleted revisions, is ignored.
    """
    parts: list[str] = []
    for node in element.iter():
        replacement = _TEXT_TAGS.get(node.tag)
        if replacement is None:
            continue
        parts.append(replacement if replacement else (node.text or ""))
    return "".join(parts)


def paragraph_text(paragraph: Element) -> str:
    """Text of a paragraph.

    An alias for :func:`text_of` that documents intent at the call site; kept
    because "the text of this paragraph" is what callers are actually asking for.
    """
    return text_of(paragraph)


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def remove(element: Element) -> None:
    """Detach *element* from its parent, if it has one."""
    parent = element.getparent()
    if parent is not None:
        parent.remove(element)


def deepcopy(element: Element) -> Element:
    """Copy an element, including its custom python-docx class if it has one."""
    return copy.deepcopy(element)


def children(element: Element) -> Sequence[Element]:
    """Direct children as a list (``element`` is iterable, but this is typed)."""
    return list(element)
