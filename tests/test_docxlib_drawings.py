"""Tests for docxlib.drawings."""

from __future__ import annotations

import pytest
from lxml import etree

from thesis_refinery.docxlib.drawings import (
    extent_of,
    has_drawing,
    images_in,
    relationship_ids,
    replace_relationship_id,
    rotation_of,
    set_extent_emu,
    set_rotation,
    tables_containing_drawings,
)
from thesis_refinery.docxlib.ns import NSMAP, q
from thesis_refinery.docxlib.parse import parse
from thesis_refinery.docxlib.synth import (
    DOCUMENT_NAMESPACES,
    drawing_xml,
    paragraph_xml,
    part_xml,
    table_xml,
)
from thesis_refinery.docxlib.xmlq import iter_descendants

CW, CH = 5_669_280, 3_780_000  # 6.2 in x 4.13 in, i.e. a full-width figure


def document(body: str) -> etree._Element:
    return parse(
        part_xml(
            root="w:document",
            namespaces=DOCUMENT_NAMESPACES,
            body=f"<w:body>{body}</w:body>",
        )
    )


def drawing_element(**kwargs) -> etree._Element:
    root = document(drawing_xml("rId9", width_emu=CW, height_emu=CH, **kwargs))
    return next(iter_descendants(root, "w:drawing"))


# ---------------------------------------------------------------------------
# The predicate that used to be written eight times
# ---------------------------------------------------------------------------


def test_has_drawing_finds_an_inline_picture() -> None:
    assert has_drawing(document(drawing_xml("rId9", width_emu=CW, height_emu=CH)))


def test_has_drawing_finds_a_legacy_vml_picture() -> None:
    """WPS and older Word still emit VML. A DrawingML-only check would miss it."""
    body = '<w:p><w:r><w:pict><v:shape style="width:1pt;height:1pt"/></w:pict></w:r></w:p>'
    root = parse(
        part_xml(
            root="w:document",
            namespaces={**DOCUMENT_NAMESPACES, "v": NSMAP["v"]},
            body=f"<w:body>{body}</w:body>",
        )
    )
    assert has_drawing(root)


def test_has_drawing_is_false_for_plain_text() -> None:
    assert not has_drawing(document(paragraph_xml("No pictures here.")))


def test_has_drawing_is_false_for_an_empty_body() -> None:
    assert not has_drawing(document(""))


def test_has_drawing_looks_inside_tables() -> None:
    table = table_xml([["caption text", "value"]])
    embedded = table.replace(
        "<w:p>",
        f"<w:p>{drawing_xml('rId9', width_emu=CW, height_emu=CH)}",
        1,
    )
    assert has_drawing(document(embedded))


# ---------------------------------------------------------------------------
# Reading drawings
# ---------------------------------------------------------------------------


def test_iter_drawings_reports_an_inline_picture() -> None:
    found = images_in(document(drawing_xml("rId9", width_emu=CW, height_emu=CH)))

    assert len(found) == 1
    assert found[0].is_inline
    assert not found[0].anchored
    assert found[0].extent_emu == (CW, CH)
    assert found[0].relationship_id == "rId9"


def test_iter_drawings_counts_every_picture() -> None:
    body = drawing_xml("rId9", width_emu=CW, height_emu=CH) + drawing_xml(
        "rId10", width_emu=CW, height_emu=CH, drawing_id=2
    )
    assert len(images_in(document(body))) == 2


def test_extent_is_none_when_the_drawing_declares_no_size() -> None:
    assert extent_of(etree.Element(q("drawing"))) is None


def test_extent_falls_back_to_the_shape_transform() -> None:
    root = document(drawing_xml("rId9", width_emu=CW, height_emu=CH))
    drawing = next(iter_descendants(root, "w:drawing"))
    extent = next(iter_descendants(drawing, "wp:extent"))
    extent.getparent().remove(extent)

    assert extent_of(drawing) == (CW, CH)


def test_relationship_ids_collects_every_reference() -> None:
    body = drawing_xml("rId9", width_emu=CW, height_emu=CH) + drawing_xml(
        "rId10", width_emu=CW, height_emu=CH, drawing_id=2
    )
    assert relationship_ids(document(body)) == {"rId9", "rId10"}


def test_relationship_ids_is_empty_without_pictures() -> None:
    assert relationship_ids(document(paragraph_xml("text"))) == set()


# ---------------------------------------------------------------------------
# Writing drawings
# ---------------------------------------------------------------------------


def test_set_extent_updates_both_the_layout_and_the_transform() -> None:
    """Setting only one is why a resized picture looks wrong in one reader."""
    root = document(drawing_xml("rId9", width_emu=CW, height_emu=CH))
    drawing = next(iter_descendants(root, "w:drawing"))

    set_extent_emu(drawing, 1_000_000, 2_000_000)

    assert extent_of(drawing) == (1_000_000, 2_000_000)
    transform_extent = next(iter_descendants(drawing, "a:ext"))
    assert transform_extent.get("cx") == "1000000"
    assert transform_extent.get("cy") == "2000000"


def test_replace_relationship_id_rewrites_every_reference() -> None:
    root = document(drawing_xml("rId9", width_emu=CW, height_emu=CH))

    assert replace_relationship_id(root, "rId9", "rId42") == 1
    assert relationship_ids(root) == {"rId42"}


def test_replace_relationship_id_reports_zero_when_nothing_matched() -> None:
    root = document(drawing_xml("rId9", width_emu=CW, height_emu=CH))
    assert replace_relationship_id(root, "rIdNonexistent", "rId1") == 0


def test_rotation_round_trips_through_the_60000ths_encoding() -> None:
    root = document(drawing_xml("rId9", width_emu=CW, height_emu=CH, rotation_degrees=180))
    drawing = next(iter_descendants(root, "w:drawing"))

    assert rotation_of(drawing) == pytest.approx(180.0)

    set_rotation(drawing, 90)
    assert rotation_of(drawing) == pytest.approx(90.0)


def test_rotation_defaults_to_zero() -> None:
    assert rotation_of(drawing_element()) == 0.0


# ---------------------------------------------------------------------------
# Tables containing drawings
# ---------------------------------------------------------------------------


def test_tables_containing_drawings_finds_the_table() -> None:
    cells = table_xml([["a", "b"]])
    embedded = cells.replace("<w:p>", f"<w:p>{drawing_xml('rId9', width_emu=CW, height_emu=CH)}", 1)

    assert len(tables_containing_drawings(document(embedded))) == 1


def test_tables_containing_drawings_is_empty_without_pictures() -> None:
    assert tables_containing_drawings(document(table_xml([["a", "b"]]))) == []
