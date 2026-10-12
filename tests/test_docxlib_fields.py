"""Tests for docxlib.fields."""

from __future__ import annotations

import pytest
from lxml import etree

from thesis_refinery.docxlib.fields import (
    FIELD_BEGIN,
    FIELD_END,
    FIELD_SEPARATE,
    complex_field,
    contains_field,
    field_char,
    field_simple,
    iter_instructions,
    num_pages_field,
    page_number_field,
    paragraphs_with_fields,
    remove_update_fields,
    set_update_fields,
    styleref_field,
    update_fields_enabled,
)
from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.parse import parse
from thesis_refinery.docxlib.synth import (
    DOCUMENT_NAMESPACES,
    paragraph_xml,
    parse_fragment,
    part_xml,
)
from thesis_refinery.docxlib.xmlq import find, findall, iter_descendants, local_name

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def document(body: str) -> etree._Element:
    return parse(
        part_xml(root="w:document", namespaces=DOCUMENT_NAMESPACES, body=f"<w:body>{body}</w:body>")
    )


def paragraph_with(runs: list[etree._Element]) -> etree._Element:
    paragraph = parse_fragment(paragraph_xml(""))
    for run in runs:
        paragraph.append(run)
    return paragraph


def settings(body: str = "") -> etree._Element:
    return parse(
        part_xml(
            root="w:settings",
            namespaces={"w": DOCUMENT_NAMESPACES["w"]},
            body=body,
        )
    )


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def test_field_char_has_the_right_type() -> None:
    char = find(field_char(FIELD_BEGIN), "w:fldChar")
    assert char is not None
    assert char.get(q("fldCharType")) == "begin"


def test_field_char_can_be_marked_dirty() -> None:
    char = find(field_char(FIELD_BEGIN, dirty=True), "w:fldChar")
    assert char.get(q("dirty")) == "true"


def test_field_char_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValueError, match="begin/separate/end"):
        field_char("middle")


def test_complex_field_has_the_five_part_shape() -> None:
    runs = complex_field("PAGE", "7")

    types = []
    for run in runs:
        char = find(run, "w:fldChar")
        if char is not None:
            types.append(char.get(q("fldCharType")))
        else:
            types.append("content")

    assert types == [FIELD_BEGIN, "content", FIELD_SEPARATE, "content", FIELD_END]


def test_instruction_text_preserves_its_spaces() -> None:
    """Word delimits field arguments by spaces; stripping them changes the field."""
    instruction = next(
        run for run in complex_field("PAGE") if find(run, "w:instrText") is not None
    )
    text = find(instruction, "w:instrText")

    assert text.get(XML_SPACE) == "preserve"
    assert text.text.startswith(" ") and text.text.endswith(" ")


def test_complex_field_caches_its_result() -> None:
    """A field with no cached result shows nothing until Word refreshes it."""
    runs = complex_field("NUMPAGES", "42")
    texts = [t.text for run in runs for t in findall(run, "w:t")]
    assert "42" in texts


def test_field_simple_carries_the_instruction_in_an_attribute() -> None:
    element = field_simple("PAGE", "3")
    assert element.tag == q("fldSimple")
    assert element.get(q("instr")) == " PAGE "
    assert [t.text for t in iter_descendants(element, "w:t")] == ["3"]


# ---------------------------------------------------------------------------
# The fields this project uses
# ---------------------------------------------------------------------------


def test_styleref_quotes_the_style_name() -> None:
    runs = styleref_field("Heading 1")
    instruction = next(t.text for run in runs for t in findall(run, "w:instrText"))
    assert 'STYLEREF "Heading 1"' in instruction


def test_styleref_escapes_an_embedded_quote() -> None:
    runs = styleref_field('He said "hi"')
    instruction = next(t.text for run in runs for t in findall(run, "w:instrText"))
    assert '\\"hi\\"' in instruction


def test_styleref_accepts_a_localised_style_name() -> None:
    runs = styleref_field("标题 1")
    instruction = next(t.text for run in runs for t in findall(run, "w:instrText"))
    assert "标题 1" in instruction


def test_page_and_numpages_instructions() -> None:
    page = next(t.text for run in page_number_field() for t in findall(run, "w:instrText"))
    total = next(t.text for run in num_pages_field() for t in findall(run, "w:instrText"))

    assert "PAGE" in page and "NUMPAGES" not in page
    assert "NUMPAGES" in total


def test_a_cached_result_can_carry_its_own_run_properties() -> None:
    """A running head has a font size; a field without rPr renders at the default."""
    properties = etree.Element(q("rPr"))
    etree.SubElement(properties, q("sz")).set(q("val"), "18")

    runs = styleref_field("Heading 1", result="Chapter One", run_properties=properties)
    result_run = next(run for run in runs if find(run, "w:t") is not None)

    assert find(result_run, "w:rPr") is not None
    assert find(find(result_run, "w:rPr"), "w:sz").get(q("val")) == "18"


def test_run_properties_are_copied_not_aliased() -> None:
    properties = etree.Element(q("rPr"))
    runs = page_number_field(run_properties=properties)
    result_run = next(run for run in runs if find(run, "w:t") is not None)
    assert find(result_run, "w:rPr") is not properties


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_iter_instructions_covers_both_spellings() -> None:
    paragraph = paragraph_with(complex_field("PAGE"))
    paragraph.append(field_simple("NUMPAGES"))

    instructions = iter_instructions(paragraph)

    assert any("PAGE" in text for text in instructions)
    assert any("NUMPAGES" in text for text in instructions)


def test_contains_field_matches_a_substring() -> None:
    paragraph = paragraph_with(styleref_field("Heading 1"))
    assert contains_field(paragraph, "STYLEREF")
    assert not contains_field(paragraph, "NUMPAGES")


def test_paragraphs_with_fields_finds_only_those_that_have_one() -> None:
    body = paragraph_xml("plain") + paragraph_xml("")
    root = document(body)
    paragraphs = findall(find(root, "w:body"), "w:p")
    for run in complex_field("PAGE", "1"):
        paragraphs[1].append(run)

    assert paragraphs_with_fields(find(root, "w:body")) == [paragraphs[1]]


# ---------------------------------------------------------------------------
# updateFields
# ---------------------------------------------------------------------------


def test_update_fields_is_absent_by_default() -> None:
    assert update_fields_enabled(settings()) is None


def test_update_fields_can_be_enabled() -> None:
    root = settings()
    set_update_fields(root, True)

    assert update_fields_enabled(root) is True
    assert find(root, "w:updateFields").get(q("val")) == "true"


def test_update_fields_can_be_turned_off_explicitly() -> None:
    """Absent and explicitly false mean different things and must not collapse."""
    root = settings()
    set_update_fields(root, True)
    set_update_fields(root, False)

    assert update_fields_enabled(root) is False
    assert find(root, "w:updateFields") is not None


def test_declaring_can_be_undone_entirely() -> None:
    root = settings()
    set_update_fields(root, True)

    remove_update_fields(root)

    assert update_fields_enabled(root) is None
    assert find(root, "w:updateFields") is None


def test_removing_an_absent_declaration_is_not_an_error() -> None:
    root = settings()
    remove_update_fields(root)
    assert update_fields_enabled(root) is None


def test_update_fields_is_only_created_once() -> None:
    root = settings()
    set_update_fields(root, True)
    set_update_fields(root, True)
    assert len(findall(root, "w:updateFields")) == 1


def test_update_fields_reads_a_negative_spelling() -> None:
    root = settings('<w:updateFields w:val="0"/>')
    assert update_fields_enabled(root) is False


def test_settings_element_name_is_what_callers_expect() -> None:
    assert local_name(settings().tag) == "settings"
