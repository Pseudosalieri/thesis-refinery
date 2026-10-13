"""Finding, measuring and resizing drawings.

"Does this paragraph contain a picture?" is one question, and it used to be asked
in three different spellings of the same namespace test. Changing one OOXML
predicate meant changing every site and hoping. Here it is one function.

A related trap: a paragraph can hold a picture two ways. Modern Word writes
``w:drawing``; older files and anything produced by WPS may still carry a VML
``w:pict``. Both count as a drawing, and a check that only knows about the first
will quietly pass documents that have images where they should not.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from thesis_refinery.docxlib.ns import q, qn
from thesis_refinery.docxlib.xmlq import Element, find, iter_descendants

__all__ = [
    "Drawing",
    "extent_of",
    "has_drawing",
    "images_in",
    "iter_drawings",
    "relationship_ids",
    "replace_relationship_id",
    "rotation_of",
    "set_extent_emu",
    "set_rotation",
    "tables_containing_drawings",
]

_DRAWING_TAGS = (q("drawing"), q("pict"), q("object"))
_IMAGE_RELATIONSHIP_ATTRIBUTES = (qn("r:embed"), qn("r:link"), qn("r:id"))


@dataclass(frozen=True)
class Drawing:
    """A picture found in a document part."""

    element: Element
    """The ``w:drawing`` (or legacy ``w:pict``) element itself."""

    anchored: bool
    """True for a floating picture (``wp:anchor``), false for inline."""

    extent_emu: tuple[int, int] | None
    """Width and height in EMU, when we can determine them."""

    relationship_id: str | None
    """The ``rId`` of the image part, when the drawing references one."""

    @property
    def is_inline(self) -> bool:
        return not self.anchored


def has_drawing(element: Element) -> bool:
    """True when *element* or anything inside it is a picture.

    Covers inline and floating DrawingML as well as legacy VML, which is the
    combination that makes the answer trustworthy across Word and WPS.
    """
    return any(node.tag in _DRAWING_TAGS for node in element.iter())


def iter_drawings(element: Element) -> Iterator[Drawing]:
    """Every picture inside *element*, in document order."""
    for node in element.iter():
        if node.tag not in _DRAWING_TAGS:
            continue
        yield Drawing(
            element=node,
            anchored=find(node, "wp:anchor") is not None,
            extent_emu=extent_of(node),
            relationship_id=_first_relationship_id(node),
        )


def images_in(element: Element) -> list[Drawing]:
    """Pictures inside *element* as a list, for callers that want to count them."""
    return list(iter_drawings(element))


def extent_of(drawing: Element) -> tuple[int, int] | None:
    """Width and height of *drawing* in EMU, or ``None`` if it declares none.

    ``wp:extent`` is the size the layout engine uses; ``a:ext`` inside
    ``a:xfrm`` is the size of the shape's own transform. Word keeps them in
    step, but a document edited by another tool may not, so the layout extent
    wins where both exist.

    Both live below the drawing, not directly under it — ``wp:extent`` is a child
    of ``wp:inline`` or ``wp:anchor`` — so this searches descendants.
    """
    for extent in iter_descendants(drawing, "wp:extent"):
        if extent.get("cx") and extent.get("cy"):
            return int(extent.get("cx")), int(extent.get("cy"))

    for transform in iter_descendants(drawing, "a:xfrm"):
        inner = find(transform, "a:ext")
        if inner is not None and inner.get("cx") and inner.get("cy"):
            return int(inner.get("cx")), int(inner.get("cy"))

    return None


def set_extent_emu(drawing: Element, width: int, height: int) -> None:
    """Resize *drawing* to *width* by *height* EMU.

    Both the layout extent and the shape transform are updated. Setting only one
    is the classic reason a resized picture looks right in one renderer and
    wrong in another.
    """
    for extent in iter_descendants(drawing, "wp:extent"):
        extent.set("cx", str(width))
        extent.set("cy", str(height))

    for transform in iter_descendants(drawing, "a:xfrm"):
        inner = find(transform, "a:ext")
        if inner is not None:
            inner.set("cx", str(width))
            inner.set("cy", str(height))


def relationship_ids(element: Element) -> set[str]:
    """Every image relationship id referenced inside *element*.

    This is how a stage knows which parts of the package are still in use: a
    media part nobody references is a media part that should not be shipped.
    """
    found: set[str] = set()
    for node in element.iter():
        for attribute in _IMAGE_RELATIONSHIP_ATTRIBUTES:
            value = node.get(attribute)
            if value:
                found.add(value)
    return found


def replace_relationship_id(element: Element, old: str, new: str) -> int:
    """Rewrite every reference from *old* to *new*. Returns how many changed."""
    changed = 0
    for node in element.iter():
        for attribute in _IMAGE_RELATIONSHIP_ATTRIBUTES:
            if node.get(attribute) == old:
                node.set(attribute, new)
                changed += 1
    return changed


def _first_relationship_id(drawing: Element) -> str | None:
    for node in drawing.iter():
        for attribute in _IMAGE_RELATIONSHIP_ATTRIBUTES:
            value = node.get(attribute)
            if value:
                return value
    return None


def rotation_of(drawing: Element) -> float:
    """Rotation in degrees, as reported by ``a:xfrm/@rot``."""
    for transform in iter_descendants(drawing, "a:xfrm"):
        raw = transform.get("rot")
        if raw:
            # OOXML stores rotation in 60000ths of a degree.
            return int(raw) / 60000
    return 0.0


def set_rotation(drawing: Element, degrees: float) -> None:
    """Rotate *drawing* to *degrees*.

    Applied to every ``a:xfrm`` in the drawing, which is what keeps a rotated
    scan rotated in both the shape transform and any enclosing group.
    """
    encoded = str(round((degrees % 360) * 60000))
    for transform in iter_descendants(drawing, "a:xfrm"):
        transform.set("rot", encoded)


def tables_containing_drawings(element: Element) -> list[Element]:
    """Every ``w:tbl`` at any depth that contains a picture."""
    return [table for table in iter_descendants(element, "w:tbl") if has_drawing(table)]
