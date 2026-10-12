"""Namespaces, prefixed names, and relationship types.

python-docx already exposes a namespace map and a ``qn()`` helper, and this
module **reuses** them rather than re-declaring them: re-declaration is how the
predecessor project ended up with eight copies of the same literal.

What is added:

* the OPC namespaces (``ct``, ``pr``) that python-docx keeps private to its own
  package layer, but which package surgery needs;
* a handful of legacy namespaces (VML, Office, Word 2003) that appear in real
  documents written by Word and WPS;
* relationship *type* URIs, which are not XML namespaces but belong in the same
  "all the magic URIs live here" place.

Usage::

    from thesis_refinery.docxlib.ns import q, qn

    q("p")        # '{http://...wordprocessingml/2006/main}p'
    qn("wp:inline")
"""

from __future__ import annotations

from docx.oxml.ns import nsmap as _docx_nsmap

__all__ = ["CONTENT_TYPE", "NSMAP", "RELTYPE", "q", "qn"]


# ---------------------------------------------------------------------------
# Extra namespaces
# ---------------------------------------------------------------------------
# Kept deliberately small. Add a prefix here only when a real document needs it;
# an unused prefix is a claim nobody checks.
_EXTRA: dict[str, str] = {
    # OPC, i.e. the package itself rather than the document inside it
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    # Markup compatibility: Word wraps extension content in mc:AlternateContent
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    # Document properties
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
    # Word 2010/2012 extensions, used for things like checkbox content controls
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
    # Legacy drawing and Office namespaces; still emitted by WPS Office
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "w10": "urn:schemas-microsoft-com:office:word",
}

NSMAP: dict[str, str] = {**_docx_nsmap, **_EXTRA}
"""Every namespace prefix this project may emit, keyed by prefix."""


def qn(prefixed_name: str) -> str:
    """Return the Clark notation for a prefixed name, e.g. ``w:p``.

    Raises :class:`KeyError` with the offending prefix spelled out, because
    lxml's own error ("KeyError: 'zzz'") does not say where the name came from.
    """
    prefix, separator, local = prefixed_name.partition(":")
    if not separator:
        raise KeyError(
            f"expected a prefixed name like 'w:p', got {prefixed_name!r}; "
            "use q() for unprefixed WordprocessingML names"
        )
    try:
        namespace = NSMAP[prefix]
    except KeyError:
        known = ", ".join(sorted(NSMAP))
        raise KeyError(f"unknown namespace prefix {prefix!r} in {prefixed_name!r}; known: {known}") from None
    return f"{{{namespace}}}{local}"


def q(local_name: str) -> str:
    """Return the Clark notation for a WordprocessingML name: ``q("p")`` → ``w:p``."""
    return qn(f"w:{local_name}")


# ---------------------------------------------------------------------------
# Relationship types
# ---------------------------------------------------------------------------
RELTYPE: dict[str, str] = {
    "office_document": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
    "styles": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles",
    "numbering": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering",
    "settings": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings",
    "web_settings": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/webSettings",
    "font_table": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/fontTable",
    "theme": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme",
    "image": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
    "hyperlink": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
    "header": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header",
    "footer": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer",
    "footnotes": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes",
    "endnotes": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes",
    "comments": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
    "core_properties": "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
    "extended_properties": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties",
    "custom_properties": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties",
    "custom_xml": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml",
    "custom_xml_properties": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXmlProps",
    "glossary_document": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/glossaryDocument",
}
"""Relationship type URIs, keyed by a readable name."""


# ---------------------------------------------------------------------------
# Content types
# ---------------------------------------------------------------------------
CONTENT_TYPE: dict[str, str] = {
    "document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "styles": "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml",
    "settings": "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml",
    "numbering": "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml",
    "header": "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml",
    "footer": "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml",
    "footnotes": "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
    "endnotes": "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml",
    "core_properties": "application/vnd.openxmlformats-package.core-properties+xml",
    "extended_properties": "application/vnd.openxmlformats-officedocument.extended-properties+xml",
    "relationships": "application/vnd.openxmlformats-package.relationships+xml",
}
"""Content-type strings for the parts this project may create."""
