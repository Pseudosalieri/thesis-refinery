"""Tests for docxlib.xmlq."""

from __future__ import annotations

import pytest
from lxml import etree

from thesis_refinery.docxlib.ns import NSMAP, q, qn
from thesis_refinery.docxlib.parse import parse
from thesis_refinery.docxlib.xmlq import (
    SCHEMA_ORDER,
    UnsupportedElementOrder,
    attr,
    deepcopy,
    ensure_ordered,
    find,
    findall,
    has_schema_order,
    iter_descendants,
    local_name,
    new,
    ordered_insert,
    paragraph_text,
    remove,
    sub,
    text_of,
)

W_NS = NSMAP["w"]


def wml(body: str) -> str:
    """Wrap *body* in a namespace-declaring root element."""
    return f'<w:root xmlns:w="{W_NS}">{body}</w:root>'


def parse_wml(body: str) -> etree._Element:
    return parse(wml(body))


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def test_local_name_strips_the_namespace() -> None:
    assert local_name(qn("w:pPr")) == "pPr"
    assert local_name("plain") == "plain"


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def test_new_sets_namespaced_attributes() -> None:
    element = new("w:spacing", **{"w:line": "360", "w:lineRule": "auto"})
    assert element.get(qn("w:line")) == "360"
    assert element.get(qn("w:lineRule")) == "auto"


def test_new_sets_plain_attributes() -> None:
    element = new("w:val", **{"plain": "1"})
    assert element.get("plain") == "1"


def test_attr_reads_prefixed_and_plain_names() -> None:
    element = new("w:pgSz", **{"w:w": "11906"})
    assert attr(element, "w:w") == "11906"
    assert attr(element, "w:h") is None
    assert attr(element, "w:h", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# Schema order: the whole reason this module exists
# ---------------------------------------------------------------------------


def test_ordered_insert_places_children_in_schema_order_regardless_of_call_order() -> None:
    pPr = etree.Element(q("pPr"))

    # Deliberately demand them in reverse schema order.
    ordered_insert(pPr, etree.Element(q("rPr")))
    ordered_insert(pPr, etree.Element(q("ind")))
    ordered_insert(pPr, etree.Element(q("keepNext")))
    ordered_insert(pPr, etree.Element(q("spacing")))

    assert [local_name(c.tag) for c in pPr] == ["keepNext", "spacing", "ind", "rPr"]


def test_ordered_insert_handles_repeated_children() -> None:
    """A new row belongs after the existing rows, not before them."""
    tbl = etree.Element(q("tbl"))
    for name in ("tblPr", "tblGrid", "tr"):
        tbl.append(etree.Element(q(name)))

    ordered_insert(tbl, etree.Element(q("tr")))

    assert [local_name(c.tag) for c in tbl] == ["tblPr", "tblGrid", "tr", "tr"]


def test_ordered_insert_steps_over_unknown_siblings() -> None:
    """An extension element must not be used as an anchor."""
    pPr = etree.Element(q("pPr"))
    pPr.append(etree.Element(qn("w14:someExtension")))

    ordered_insert(pPr, etree.Element(q("rPr")))
    ordered_insert(pPr, etree.Element(q("keepNext")))

    names = [local_name(c.tag) for c in pPr]
    assert names.index("keepNext") < names.index("rPr")


def test_ordered_insert_refuses_an_unknown_parent() -> None:
    with pytest.raises(UnsupportedElementOrder) as excinfo:
        ordered_insert(etree.Element(q("madeUpParent")), etree.Element(q("x")))
    assert "madeUpParent" in str(excinfo.value)


def test_ordered_insert_refuses_an_unknown_child() -> None:
    with pytest.raises(UnsupportedElementOrder) as excinfo:
        ordered_insert(etree.Element(q("pPr")), etree.Element(q("notAChild")))
    assert "notAChild" in str(excinfo.value)


def test_ordered_insert_can_append_when_asked() -> None:
    parent = etree.Element(q("madeUpParent"))
    child = etree.Element(q("x"))
    assert ordered_insert(parent, child, on_unknown="append") is child
    assert parent[0] is child


def test_has_schema_order_reports_coverage() -> None:
    assert has_schema_order(etree.Element(q("pPr")))
    assert not has_schema_order(etree.Element(q("madeUpParent")))


def test_every_schema_order_table_is_a_unique_sequence() -> None:
    """A duplicated name would make the placement algorithm pick arbitrarily."""
    for parent, order in SCHEMA_ORDER.items():
        assert len(order) == len(set(order)), parent


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------


def test_find_and_findall_filter_by_name_and_attribute() -> None:
    body = parse_wml('<w:p w:rsidR="00A"/><w:p w:rsidR="00B"/><w:tbl/>')

    assert len(findall(body, "w:p")) == 2
    assert len(findall(body, "w:p", **{"w:rsidR": "00B"})) == 1
    assert find(body, "w:p").get(qn("w:rsidR")) == "00A"
    assert find(body, "w:sectPr") is None


def test_iter_descendants_can_filter_by_name() -> None:
    root = parse_wml("<w:p><w:r><w:t>hi</w:t></w:r></w:p>")

    assert [local_name(e.tag) for e in iter_descendants(root, "w:t")] == ["t"]
    assert len(list(iter_descendants(root))) == 3  # p, r, t


def test_sub_creates_and_places() -> None:
    pPr = etree.Element(q("pPr"))
    sub(pPr, "w:ind")
    sub(pPr, "w:keepNext")
    assert [local_name(c.tag) for c in pPr] == ["keepNext", "ind"]


def test_ensure_ordered_is_idempotent() -> None:
    pPr = etree.Element(q("pPr"))
    first = ensure_ordered(pPr, "w:ind", **{"w:firstLine": "480"})
    second = ensure_ordered(pPr, "w:ind")
    assert first is second
    assert len(findall(pPr, "w:ind")) == 1


def test_ensure_ordered_creates_when_the_attributes_differ() -> None:
    pPr = etree.Element(q("pPr"))
    ensure_ordered(pPr, "w:ind", **{"w:firstLine": "480"})
    ensure_ordered(pPr, "w:ind", **{"w:firstLine": "0"})
    assert len(findall(pPr, "w:ind")) == 2


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def test_text_of_joins_runs() -> None:
    p = parse_wml("<w:r><w:t>Alpha</w:t></w:r><w:r><w:t xml:space='preserve'> beta</w:t></w:r>")
    assert text_of(p) == "Alpha beta"
    assert paragraph_text(p) == "Alpha beta"


def test_text_of_maps_tabs_breaks_and_hyphens() -> None:
    p = parse_wml("<w:r><w:t>A</w:t><w:tab/><w:t>B</w:t><w:br/><w:t>C</w:t><w:noBreakHyphen/></w:r>")
    assert text_of(p) == "A\tB\nC-"


def test_text_of_ignores_field_codes() -> None:
    p = parse_wml("<w:r><w:instrText>PAGE</w:instrText><w:t>7</w:t></w:r>")
    assert text_of(p) == "7"


def test_text_of_on_an_empty_paragraph() -> None:
    assert text_of(etree.Element(q("p"))) == ""


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_remove_detaches_and_is_safe_on_a_root() -> None:
    parent = etree.Element(q("body"))
    child = etree.SubElement(parent, q("p"))

    remove(child)
    assert len(parent) == 0

    remove(parent)  # no parent: must not raise


def test_deepcopy_preserves_the_python_docx_element_class() -> None:
    p = parse(f'<w:p xmlns:w="{W_NS}"/>')
    assert type(deepcopy(p)).__name__ == type(p).__name__
