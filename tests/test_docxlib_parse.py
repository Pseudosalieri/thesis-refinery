"""Tests for docxlib.parse, including the entity-expansion guarantee."""

from __future__ import annotations

import pytest

from thesis_refinery.docxlib.parse import (
    DECLARATION,
    declaration_of,
    parse,
    serialize,
    serialize_like,
)


def test_declaration_is_words_declaration_verbatim() -> None:
    assert DECLARATION == b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'


def test_declaration_of_extracts_word_style_declaration() -> None:
    raw = DECLARATION + b"<w:document/>"
    assert declaration_of(raw) == DECLARATION


def test_declaration_of_returns_empty_when_absent() -> None:
    assert declaration_of(b"<root/>") == b""


def test_declaration_of_tolerates_single_quotes_and_lf() -> None:
    raw = b"<?xml version='1.0' encoding='UTF-8'?>\n<root/>"
    assert declaration_of(raw) == b"<?xml version='1.0' encoding='UTF-8'?>\n"


def test_serialize_like_keeps_the_original_declaration_not_lxmls() -> None:
    """lxml writes single quotes; Word writes double. The original must win."""
    original = DECLARATION + b'<w:document xmlns:w="http://x"/>'
    root = parse(original)

    rewritten = serialize_like(root, original)

    assert rewritten.startswith(DECLARATION)
    assert b"standalone='yes'" not in rewritten


def test_serialize_round_trips_a_parsed_document() -> None:
    raw = DECLARATION + b'<w:document xmlns:w="http://x"><w:body/></w:document>'
    assert serialize(parse(raw)) == raw


def test_parse_does_not_expand_entities() -> None:
    """The whole point of resolve_entities=False: no expansion, no file reads."""
    payload = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "EXPANDED">]><r>&x;</r>'

    root = parse(payload)

    # The entity reference survives as a reference; it is not substituted.
    assert root.text != "EXPANDED"
    assert b"EXPANDED" not in serialize(root, declaration=None)


def test_parse_rejects_a_doctype_that_reads_a_file() -> None:
    """An external entity must not be resolved, so this cannot leak /etc/passwd."""
    payload = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b"<r>&x;</r>"
    )

    root = parse(payload)

    assert root.text is None or "root:" not in root.text


def test_parse_produces_python_docx_element_classes() -> None:
    """Re-using python-docx's parser must keep its custom element classes."""
    root = parse(b'<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
    assert type(root).__name__ == "CT_P"


def test_parser_is_the_shared_one() -> None:
    """One parser for the whole project; a second one is how postures drift."""
    from docx.oxml.parser import oxml_parser

    from thesis_refinery.docxlib.parse import parser

    assert parser() is oxml_parser


@pytest.mark.parametrize("raw", [b"", b"<root/>", DECLARATION])
def test_declaration_of_never_raises(raw: bytes) -> None:
    assert isinstance(declaration_of(raw), bytes)
