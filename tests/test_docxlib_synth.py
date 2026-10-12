"""Tests for docxlib.synth.

Two properties here were found the hard way, by a downstream tool that worked
around them before reporting them. Both are the kind of bug that a unit test only
catches if somebody thinks to write it, so both are written down.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.parse import parse
from thesis_refinery.docxlib.pkgs import Package
from thesis_refinery.docxlib.synth import (
    build_docx,
    inline_drawing_xml,
    paragraph_xml,
    parse_fragment,
    part_xml,
    style_xml,
    styles_xml,
    table_xml,
)
from thesis_refinery.docxlib.xmlq import SCHEMA_ORDER, find, iter_descendants, local_name

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def positions(parent: str) -> dict[str, int]:
    """Flatten the schema order for *parent*, as ``xmlq`` does internally."""
    flattened: dict[str, int] = {}
    for index, entry in enumerate(SCHEMA_ORDER[parent]):
        if isinstance(entry, tuple):
            for name in entry:
                flattened[name] = index
        else:
            flattened[entry] = index
    return flattened


def assert_ordered(element, parent: str) -> None:
    """Assert *element*'s children appear in the order the schema requires."""
    order = positions(parent)
    seen = [order[local_name(child.tag)] for child in element if local_name(child.tag) in order]
    assert seen == sorted(seen), (
        f"{parent} children out of schema order: "
        f"{[local_name(c.tag) for c in element]}"
    )


# ---------------------------------------------------------------------------
# Element order
# ---------------------------------------------------------------------------


def test_outline_level_lands_after_the_properties_that_must_precede_it() -> None:
    """`w:outlineLvl` comes after `w:spacing`, `w:ind` and `w:jc` in CT_PPr.

    Prefixing it -- the obvious implementation when the surrounding code builds
    XML as strings -- puts it first and produces a schema-invalid style. Word's
    response is "the file is corrupt", which is how this was found.
    """
    styles = styles_xml(
        [
            style_xml(
                "Heading1",
                "Heading 1",
                outline_level=0,
                paragraph_properties='<w:keepNext/><w:spacing w:before="240"/><w:jc w:val="center"/>',
            )
        ]
    )
    root = parse(styles)
    style = next(iter_descendants(root, "w:style"))
    properties = find(style, "w:pPr")

    assert properties is not None
    assert_ordered(properties, "pPr")
    assert [local_name(child.tag) for child in properties] == [
        "keepNext",
        "spacing",
        "jc",
        "outlineLvl",
    ]


def test_outline_level_alone_still_produces_a_paragraph_properties_element() -> None:
    styles = styles_xml([style_xml("Heading1", "Heading 1", outline_level=0)])
    root = parse(styles)
    properties = find(next(iter_descendants(root, "w:style")), "w:pPr")

    assert properties is not None
    assert find(properties, "w:outlineLvl").get(q("val")) == "0"


def test_a_style_without_properties_has_no_paragraph_properties() -> None:
    styles = styles_xml([style_xml("Normal", "Normal", default=True)])
    root = parse(styles)
    assert find(next(iter_descendants(root, "w:style")), "w:pPr") is None


def test_run_properties_follow_paragraph_properties_in_the_style() -> None:
    """CT_Style is sequence-typed too: w:pPr before w:rPr."""
    styles = styles_xml(
        [
            style_xml(
                "BodyText",
                "Body Text",
                paragraph_properties='<w:ind w:firstLine="480"/>',
                run_properties='<w:sz w:val="24"/>',
            )
        ]
    )
    style = next(iter_descendants(parse(styles), "w:style"))
    names = [local_name(child.tag) for child in style]

    assert names.index("pPr") < names.index("rPr")


def test_generated_paragraph_properties_are_in_schema_order() -> None:
    paragraph = parse_fragment(paragraph_xml("x", style="BodyText", outline_level=1))
    properties = find(paragraph, "w:pPr")

    assert properties is not None
    assert_ordered(properties, "pPr")
    assert [local_name(child.tag) for child in properties] == ["pStyle", "outlineLvl"]


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


def test_building_the_same_document_twice_produces_identical_bytes(tmp_path: Path) -> None:
    """A zip member carries a modification time.

    Stamping the wall clock makes two runs of the same code on the same inputs
    differ, which defeats "did this stage change anything?" -- and makes a
    committed generated asset impossible to verify against its generator.
    """
    body = paragraph_xml("Deterministic.") + table_xml([["a", "b"], ["1", "2"]])
    styles = styles_xml([style_xml("Normal", "Normal", default=True)])

    first = build_docx(tmp_path / "a.docx", body=body, styles=styles)
    second = build_docx(tmp_path / "b.docx", body=body, styles=styles)

    assert first.read_bytes() == second.read_bytes()


def test_two_builds_of_the_same_inputs_have_the_same_part_digests(tmp_path: Path) -> None:
    body = paragraph_xml("Deterministic.")
    first = build_docx(tmp_path / "a.docx", body=body)
    second = build_docx(tmp_path / "b.docx", body=body)

    with Package(first) as left, Package(second) as right:
        assert left.names() == right.names()
        for name in left.names():
            assert left.read(name) == right.read(name), name


def test_a_generated_document_is_a_package_we_can_open(tmp_path: Path) -> None:
    """The generator's output must satisfy the reader it will be fed to."""
    path = build_docx(
        tmp_path / "doc.docx",
        body=paragraph_xml("Hello."),
        styles=styles_xml([style_xml("Normal", "Normal", default=True)]),
    )
    with Package(path) as package:
        assert "word/document.xml" in package
        assert "[Content_Types].xml" in package


# ---------------------------------------------------------------------------
# Drawing fragments
# ---------------------------------------------------------------------------


def test_inline_drawing_declares_both_the_layout_and_the_shape_extent() -> None:
    """A drawing with only one of them renders differently in different readers."""
    fragment = inline_drawing_xml("rId7", width_emu=1_000_000, height_emu=2_000_000)
    root = parse(part_xml(root="w:r", namespaces={"w": W_NS, "wp": "x", "a": "y", "pic": "z", "r": "q"}, body=fragment))

    assert 'cx="1000000"' in etree.tostring(root, encoding="unicode")


def test_inline_drawing_can_carry_a_rotation() -> None:
    fragment = inline_drawing_xml("rId7", width_emu=1, height_emu=1, rotation_degrees=180)
    assert 'rot="10800000"' in fragment  # 180 degrees, in 60000ths


@pytest.mark.parametrize("degrees", [0, 90, 180, 270, 359])
def test_rotation_is_encoded_in_sixty_thousandths(degrees: int) -> None:
    fragment = inline_drawing_xml("rId7", width_emu=1, height_emu=1, rotation_degrees=degrees)
    assert f'rot="{round(degrees * 60000)}"' in fragment
