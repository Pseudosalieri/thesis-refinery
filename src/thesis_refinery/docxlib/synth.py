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
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from lxml import etree

from thesis_refinery.docxlib import units
from thesis_refinery.docxlib.ns import CONTENT_TYPE, NSMAP, RELTYPE
from thesis_refinery.docxlib.parse import DECLARATION, parse
from thesis_refinery.docxlib.xmlq import Element, new, ordered_insert

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
"""A fixed member timestamp, so two builds of the same inputs are byte-identical.

The zip format stores a modification time per member. Stamping the wall clock
makes the archive differ between two runs of the same code on the same inputs,
which defeats the "did this stage change anything?" question the whole pipeline
is built around -- and makes a committed generated asset impossible to verify
against its generator.
"""

__all__ = [
    "DOCUMENT_NAMESPACES",
    "build_docx",
    "content_types_xml",
    "document_xml",
    "drawing_xml",
    "inline_drawing_xml",
    "paragraph_xml",
    "parse_fragment",
    "part_xml",
    "relationships_xml",
    "run_xml",
    "style_xml",
    "styles_xml",
    "table_xml",
]

W = NSMAP["w"]
R = NSMAP["r"]
CT = NSMAP["ct"]
PR = NSMAP["pr"]
WP = NSMAP["wp"]
A = NSMAP["a"]
PIC = NSMAP["pic"]

DOCUMENT_NAMESPACES: dict[str, str] = {
    "w": W,
    "r": R,
    "wp": WP,
    "a": A,
    "pic": PIC,
    "mc": NSMAP["mc"],
    "w14": NSMAP["w14"],
}
"""Prefixes ``word/document.xml`` declares.

A fragment like a drawing uses ``wp:``/``a:``/``pic:`` and relies on the root
declaring them, which is exactly how Word writes it, so a document built here
looks like one Word produced rather than one assembled by string concatenation.
"""


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
        namespaces=DOCUMENT_NAMESPACES,
        body=f"<w:body>\n{body}\n</w:body>",
    )


# ---------------------------------------------------------------------------
# Drawings
# ---------------------------------------------------------------------------


def inline_drawing_xml(
    relationship_id: str,
    *,
    width_emu: int,
    height_emu: int,
    name: str = "Picture",
    drawing_id: int = 1,
    rotation_degrees: float | None = None,
) -> str:
    """A ``w:drawing`` containing an inline picture.

    Both sizes are written: ``wp:extent`` is what lays the picture out and
    ``a:ext`` is the shape's own transform. A fragment with only one of them
    renders differently in different readers, which is why
    :func:`thesis_refinery.docxlib.drawings.set_extent_emu` updates both.
    """
    rotation = (
        f' rot="{round((rotation_degrees % 360) * 60000)}"'
        if rotation_degrees is not None
        else ""
    )
    return (
        "<w:drawing>"
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{width_emu}" cy="{height_emu}"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        f'<wp:docPr id="{drawing_id}" name="{name} {drawing_id}"/>'
        "<wp:cNvGraphicFramePr>"
        '<a:graphicFrameLocks noChangeAspect="1"/>'
        "</wp:cNvGraphicFramePr>"
        "<a:graphic>"
        f'<a:graphicData uri="{PIC}">'
        "<pic:pic>"
        "<pic:nvPicPr>"
        f'<pic:cNvPr id="{drawing_id}" name="{name} {drawing_id}"/>'
        "<pic:cNvPicPr/>"
        "</pic:nvPicPr>"
        "<pic:blipFill>"
        f'<a:blip r:embed="{relationship_id}"/>'
        "<a:stretch><a:fillRect/></a:stretch>"
        "</pic:blipFill>"
        "<pic:spPr>"
        f'<a:xfrm{rotation}><a:off x="0" y="0"/><a:ext cx="{width_emu}" cy="{height_emu}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        "</pic:spPr>"
        "</pic:pic>"
        "</a:graphicData>"
        "</a:graphic>"
        "</wp:inline>"
        "</w:drawing>"
    )


def drawing_xml(
    relationship_id: str,
    *,
    width_emu: int,
    height_emu: int,
    name: str = "Picture",
    drawing_id: int = 1,
    rotation_degrees: float | None = None,
) -> str:
    """A paragraph containing an inline drawing. Convenience wrapper."""
    inner = inline_drawing_xml(
        relationship_id,
        width_emu=width_emu,
        height_emu=height_emu,
        name=name,
        drawing_id=drawing_id,
        rotation_degrees=rotation_degrees,
    )
    return paragraph_xml(runs=[f'<w:r>{inner}</w:r>'])


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

_TABLE_WIDTH_TWIPS = units.mm_to_twips(160.0)
"""Usable text width: A4 width minus 25 mm of margin on each side."""


def table_xml(
    rows: Sequence[Sequence[str]],
    *,
    header: bool = True,
    column_widths: Sequence[int] | None = None,
) -> str:
    """A ``w:tbl`` with the given cell text.

    The first row is marked ``w:tblHeader`` when *header* is true, which is what
    :func:`thesis_refinery.docxlib.tables.header_row` looks for before falling
    back to "the first row".
    """
    if not rows:
        raise ValueError("a table needs at least one row")

    column_count = max(len(row) for row in rows)
    widths = list(column_widths or [])
    if len(widths) != column_count:
        share = _TABLE_WIDTH_TWIPS // column_count
        widths = [share] * column_count

    grid = "".join(f'<w:gridCol w:w="{width}"/>' for width in widths)

    body: list[str] = []
    for index, row in enumerate(rows):
        properties = "<w:trPr><w:tblHeader/></w:trPr>" if (header and index == 0) else ""
        cells = []
        for column in range(column_count):
            text = row[column] if column < len(row) else ""
            cells.append(
                "<w:tc>"
                f'<w:tcPr><w:tcW w:w="{widths[column]}" w:type="dxa"/></w:tcPr>'
                f"{paragraph_xml(text)}"
                "</w:tc>"
            )
        body.append(f"<w:tr>{properties}{''.join(cells)}</w:tr>")

    return (
        "<w:tbl>"
        "<w:tblPr>"
        '<w:tblW w:w="0" w:type="auto"/>'
        '<w:tblLayout w:type="fixed"/>'
        "</w:tblPr>"
        f"<w:tblGrid>{grid}</w:tblGrid>"
        f"{''.join(body)}"
        "</w:tbl>"
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

    *outline_level* is the exception, and it is inserted in schema order rather
    than concatenated. ``w:outlineLvl`` must come **after** ``w:spacing``,
    ``w:ind`` and ``w:jc``, so prefixing it -- the obvious implementation --
    produces a schema-invalid style whenever the fragment names any of those.
    Word's response is "the file is corrupt", which is a memorable way to learn
    that a string template is the wrong tool for a sequence-typed element.
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
    placed = _ordered_paragraph_properties(paragraph_properties, outline_level)
    if placed:
        parts.append(f"<w:pPr>{placed}</w:pPr>")
    if run_properties:
        parts.append(f"<w:rPr>{run_properties}</w:rPr>")
    body = "\n".join(parts)
    return f"<w:style {attributes}>\n{body}\n</w:style>"


def _ordered_paragraph_properties(fragment: str, outline_level: int | None) -> str:
    """*fragment* with ``w:outlineLvl`` inserted where CT_PPr requires it."""
    if outline_level is None:
        return fragment

    wrapper = parse(part_xml(root="w:pPr", namespaces={"w": W}, body=fragment or ""))
    ordered_insert(wrapper, new("w:outlineLvl", **{"w:val": str(outline_level)}))

    serialised = etree.tostring(wrapper, encoding="unicode")
    return serialised[serialised.index(">") + 1 : serialised.rindex("</")]


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
        # [Content_Types].xml first: some strict readers require it. Members carry
        # a fixed timestamp rather than the wall clock, so that generating the
        # same document twice produces the same bytes.
        for name in ("[Content_Types].xml", "_rels/.rels", *parts):
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, package[name])

    return path


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def parse_fragment(fragment: str, *, root: str = "w:frag") -> Element:
    """Parse a body fragment on its own, returning its first element.

    The builders here emit fragments that rely on ``word/document.xml``'s root
    namespace declarations, exactly as Word does — which means a fragment cannot
    be parsed by itself. This wraps it so tests and tools can work with one
    paragraph or one table without building a whole document.
    """
    from thesis_refinery.docxlib.parse import parse

    wrapper = part_xml(
        root=root,
        namespaces=DOCUMENT_NAMESPACES,
        body=fragment,
    )
    parsed = parse(wrapper)
    return parsed[0]
