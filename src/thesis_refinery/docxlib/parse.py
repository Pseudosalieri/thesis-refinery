"""The one XML parser, and byte-faithful serialisation.

Two rules, both learned the hard way:

**One parser.** python-docx already configures a parser with
``resolve_entities=False``, and every element it hands back carries its custom
element classes. Re-using that parser rather than constructing our own means the
security posture cannot drift per module, and that ``a.tbl`` is still a
``CT_Tbl`` after a round trip.

**Serialise like Word, or not at all.** Word writes::

    <?xml version="1.0" encoding="UTF-8" standalone="yes"?>\\r\\n

    lxml writes single quotes and a bare newline. A pipeline that rewrites every
    part of a document therefore produces a file that differs from Word's in
    every part, which makes "this stage changed nothing" impossible to verify.
    :func:`serialize_like` reuses the original part's declaration verbatim, and
    ``pkgs`` goes further: parts it did not touch are copied through as raw
    bytes and never parsed at all.
"""

from __future__ import annotations

from docx.oxml import parse_xml as _docx_parse_xml
from lxml import etree

__all__ = [
    "DECLARATION",
    "declaration_of",
    "parse",
    "parser",
    "serialize",
    "serialize_like",
]

DECLARATION = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
"""The XML declaration Word emits, byte for byte."""


def parser() -> etree.XMLParser:
    """Return the shared, hardened parser.

    Prefer :func:`parse`; this exists for callers that need to pass a parser to
    lxml directly, such as ``etree.iterparse``.
    """
    from docx.oxml.parser import oxml_parser

    return oxml_parser


def parse(xml: str | bytes) -> etree._Element:
    """Parse XML with the shared parser.

    Entities are not resolved, so ``<!ENTITY>`` declarations cannot be used to
    read files or exhaust memory.

    A ``str`` is encoded to UTF-8 first. lxml refuses a *string* that carries an
    encoding declaration, and callers have no reason to know that: every part
    this project generates carries one.
    """
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    return _docx_parse_xml(xml)


def declaration_of(raw: bytes) -> bytes:
    """Return the XML declaration at the start of *raw*, or ``b""``.

    The declaration is returned verbatim, including its original line ending, so
    that a part can be re-serialised without disturbing it.
    """
    stripped = raw.lstrip()
    if not stripped.startswith(b"<?xml"):
        return b""
    end = raw.find(b"?>")
    if end == -1:
        return b""
    end += 2
    # Keep whatever newline followed, if any.
    while end < len(raw) and raw[end : end + 1] in (b"\r", b"\n"):
        end += 1
    return raw[:end]


def serialize(root: etree._Element, *, declaration: bytes | None = DECLARATION) -> bytes:
    """Serialise an element to bytes, prefixed with *declaration*."""
    body = etree.tostring(root, encoding="UTF-8", xml_declaration=False)
    return (declaration or b"") + body


def serialize_like(root: etree._Element, original: bytes) -> bytes:
    """Serialise *root* using the declaration found in *original*.

    This is the right call when rewriting a part that already exists: it keeps
    the byte shape Word produced instead of substituting lxml's.
    """
    return serialize(root, declaration=declaration_of(original))
