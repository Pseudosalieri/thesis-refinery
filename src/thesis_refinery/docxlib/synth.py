"""Build valid OPC packages from scratch.

Three callers need this, which is why it lives in the package rather than in the
tests:

* the test suite, which needs a minimal ``.docx`` and must not commit a binary
  fixture;
* ``tools/make_reference_docx.py``, which synthesises a neutral ``reference.docx``
  so that the project ships no institution's template;
* ``tools/make_placeholder_frontmatter.py``, which synthesises a cover page for
  the demo thesis without copying anyone's.

Everything here writes plain strings, so what the project considers a valid
document is legible and reviewable rather than hidden inside a binary blob.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path

from thesis_refinery.docxlib.ns import CONTENT_TYPE, NSMAP, RELTYPE
from thesis_refinery.docxlib.parse import DECLARATION

__all__ = [
    "build_docx",
    "content_types_xml",
    "document_xml",
    "paragraph_xml",
    "part_xml",
    "relationships_xml",
    "run_xml",
    "style_xml",
    "styles_xml",
]

W = NSMAP["w"]
R = NSMAP["r"]
CT = NSMAP["ct"]
PR = NSMAP["pr"]


def part_xml(
    *,
    root: str,
    namespaces: Mapping[str, str] | None = None,
    default_namespace: str | None = None,
    body: str,
) -> str:
    """Assemble a part: declaration, root element, body.

    *root* is emitted verbatim, so if it carries a prefix that prefix must appear
    in *namespaces*. An unprefixed *root* with a *default_namespace* is how Word
    writes ``[Content_Types].xml`` and ``.rels`` parts, and that is reproduced
    here rather than substituted with a prefixed form — a part that does not look
    like Word wrote it invites suspicion from strict readers.
    """
    declarations: list[str] = []
    if default_namespace is not None:
        declarations.append(f'xmlns="{default_namespace}"')
    declarations.extend(f'xmlns:{prefix}="{uri}"' for prefix, uri in (namespaces or {}).items())

    attributes = " ".join(declarations)
    inner = "\n".join(f"  {line}" for line in body.strip().splitlines())
    return f"{DECLARATION.decode()}<{root} {attributes}>\n{inner}\n</{root}>"


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


def run_xml(text: str, *, bold: bool = False, italic: bool = False, size_half_points: int | None = None) -> str:
    """A ``w:r`` with one ``w:t``."""
    properties = []
    if bold:
        properties.append("<w:b/>")
    if italic:
        properties.append("<w:i/>")
    if size_half_points is not None:
        properties.append(f'<w:sz w:val="{size_half_points}"/><w:szCs w:val="{size_half_points}"/>')
    rpr = f"<w:rPr>{''.join(properties)}</w:rPr>" if properties else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{_escape(text)}</w:t></w:r>'


def paragraph_xml(
    text: str = "",
    *,
    style: str | None = None,
    outline_level: int | None = None,
    runs: Iterable[str] | None = None,
) -> str:
    """A ``w:p``.

    Pass *runs* to control run-level formatting; otherwise *text* becomes a
    single run.
    """
    parts = []
    if style:
        parts.append(f'<w:pStyle w:val="{style}"/>')
    if outline_level is not None:
        parts.append(f'<w:outlineLvl w:val="{outline_level}"/>')
    ppr = f"<w:pPr>{''.join(parts)}</w:pPr>" if parts else ""
    body = "".join(runs) if runs is not None else (run_xml(text) if text else "")
    return f"<w:p>{ppr}{body}</w:p>"


def document_xml(body: str) -> str:
    """``word/document.xml`` containing *body* inside a ``w:body``."""
    return part_xml(
        root="w:document",
        namespaces={"w": W, "r": R},
        body=f"<w:body>\n{body}\n</w:body>",
    )


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------


def style_xml(
    style_id: str,
    name: str,
    *,
    based_on: str | None = None,
    next_style: str | None = None,
    outline_level: int | None = None,
    paragraph_properties: str = "",
    run_properties: str = "",
    default: bool = False,
) -> str:
    """A ``w:style`` element.

    *paragraph_properties* and *run_properties* are raw XML fragments, e.g.
    ``'<w:ind w:firstLine="480"/>'``. Raw fragments are the honest choice here:
    a typed builder for every formatting property would be a second, subtly
    different model of the same schema.
    """
    attributes = f'w:type="paragraph" w:styleId="{style_id}"'
    if default:
        attributes += ' w:default="1"'
    parts = [f'<w:name w:val="{name}"/>']
    if based_on:
        parts.append(f'<w:basedOn w:val="{based_on}"/>')
    if next_style:
        parts.append(f'<w:next w:val="{next_style}"/>')
    parts.append("<w:qFormat/>")
    if outline_level is not None:
        paragraph_properties = f'<w:outlineLvl w:val="{outline_level}"/>' + paragraph_properties
    if paragraph_properties:
        parts.append(f"<w:pPr>{paragraph_properties}</w:pPr>")
    if run_properties:
        parts.append(f"<w:rPr>{run_properties}</w:rPr>")
    body = "\n".join(parts)
    return f"<w:style {attributes}>\n{body}\n</w:style>"


def styles_xml(styles: Iterable[str]) -> str:
    """``word/styles.xml`` from a sequence of :func:`style_xml` results."""
    return part_xml(root="w:styles", namespaces={"w": W, "r": R}, body="\n".join(styles))


# ---------------------------------------------------------------------------
# Package parts
# ---------------------------------------------------------------------------


def relationships_xml(relationships: Iterable[tuple[str, str, str]]) -> str:
    """A ``.rels`` part from ``(id, type-or-key, target)`` tuples.

    The type may be a key of :data:`~thesis_refinery.docxlib.ns.RELTYPE`.
    """
    lines = [
        f'<Relationship Id="{rid}" Type="{RELTYPE.get(rtype, rtype)}" Target="{target}"/>'
        for rid, rtype, target in relationships
    ]
    return part_xml(root="Relationships", default_namespace=PR, body="\n".join(lines))


def content_types_xml(
    *,
    defaults: Mapping[str, str] | None = None,
    overrides: Mapping[str, str] | None = None,
) -> str:
    """``[Content_Types].xml``.

    Defaults are emitted before overrides, because the schema requires it.
    """
    resolved_defaults = {"rels": CONTENT_TYPE["relationships"], "xml": "application/xml"}
    resolved_defaults.update(defaults or {})

    lines = [
        f'<Default Extension="{extension}" ContentType="{content_type}"/>'
        for extension, content_type in resolved_defaults.items()
    ]
    for part, content_type in (overrides or {}).items():
        name = part if part.startswith("/") else f"/{part}"
        lines.append(f'<Override PartName="{name}" ContentType="{content_type}"/>')
    return part_xml(root="Types", default_namespace=CT, body="\n".join(lines))


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_docx(
    path: str | Path,
    *,
    body: str,
    styles: str | None = None,
    media: Mapping[str, bytes] | None = None,
    extra_parts: Mapping[str, bytes] | None = None,
    extra_defaults: Mapping[str, str] | None = None,
    extra_overrides: Mapping[str, str] | None = None,
    document_relationships: Iterable[tuple[str, str, str]] = (),
) -> Path:
    """Write a minimal but valid ``.docx`` to *path* and return it.

    Parameters
    ----------
    body:
        Raw XML for the contents of ``w:body``.
    styles:
        A complete ``word/styles.xml``, as produced by :func:`styles_xml`.
    media:
        Part name → bytes, e.g. ``{"word/media/image1.png": png_bytes}``.
    extra_parts:
        Any additional parts, keyed by part name.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    overrides = {"/word/document.xml": CONTENT_TYPE["document"]}
    if styles is not None:
        overrides["/word/styles.xml"] = CONTENT_TYPE["styles"]
    overrides.update(extra_overrides or {})

    relationships: list[tuple[str, str, str]] = []
    if styles is not None:
        relationships.append(("rId1", "styles", "styles.xml"))
    relationships.extend(document_relationships)

    parts: dict[str, bytes] = {
        "word/document.xml": document_xml(body).encode("utf-8"),
        "word/_rels/document.xml.rels": relationships_xml(relationships).encode("utf-8"),
    }
    if styles is not None:
        parts["word/styles.xml"] = styles.encode("utf-8")
    for name, data in (media or {}).items():
        parts[name] = data
    for name, data in (extra_parts or {}).items():
        parts[name] = data

    package = {
        "[Content_Types].xml": content_types_xml(
            defaults=extra_defaults, overrides=overrides
        ).encode("utf-8"),
        "_rels/.rels": relationships_xml(
            [("rId1", "office_document", "word/document.xml")]
        ).encode("utf-8"),
        **parts,
    }

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        # [Content_Types].xml first: some strict readers require it.
        for name in ("[Content_Types].xml", "_rels/.rels", *parts):
            archive.writestr(name, package[name])

    return path


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
