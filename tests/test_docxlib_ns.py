"""Tests for docxlib.ns."""

from __future__ import annotations

import pytest

from thesis_refinery.docxlib.ns import CONTENT_TYPE, NSMAP, RELTYPE, q, qn

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def test_q_expands_to_wordprocessingml() -> None:
    assert q("p") == f"{{{W_NS}}}p"
    assert q("pPr") == f"{{{W_NS}}}pPr"


def test_qn_accepts_any_known_prefix() -> None:
    assert qn("w:p") == f"{{{W_NS}}}p"
    assert qn("wp:inline").endswith("}inline")
    assert qn("a:tbl").endswith("}tbl")


def test_extended_prefixes_are_present() -> None:
    """The OPC prefixes python-docx keeps private must be reachable here."""
    for prefix in ("ct", "pr", "mc", "w14", "w15", "ep", "vt", "v", "o", "w10"):
        assert prefix in NSMAP, prefix
        assert NSMAP[prefix].startswith(("http://", "urn:"))


def test_python_docx_prefixes_are_reused_not_redeclared() -> None:
    """We extend python-docx's map; we must not fork it."""
    from docx.oxml.ns import nsmap as docx_nsmap

    for prefix, uri in docx_nsmap.items():
        assert NSMAP[prefix] == uri, prefix


def test_unknown_prefix_reports_the_prefix() -> None:
    with pytest.raises(KeyError) as excinfo:
        qn("zzz:foo")
    assert "zzz" in str(excinfo.value)


def test_unprefixed_name_is_rejected_with_guidance() -> None:
    with pytest.raises(KeyError) as excinfo:
        qn("p")
    assert "q(" in str(excinfo.value)


def test_relationship_types_are_uris() -> None:
    assert RELTYPE["image"].startswith("http://")
    assert RELTYPE["office_document"].endswith("/officeDocument")
    assert all(v.startswith("http://") for v in RELTYPE.values())


def test_content_types_look_like_mime_types() -> None:
    assert all("/" in v for v in CONTENT_TYPE.values())
    assert CONTENT_TYPE["document"].endswith(".main+xml")
