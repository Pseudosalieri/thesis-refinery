"""Everything a check needs, resolved once.

Checks receive a :class:`Context` and return findings. They never open files and
never parse XML themselves, which keeps each check to the question it is actually
asking and makes a check testable by constructing the context it expects.

:func:`open_document` is a context manager because a :class:`Package` holds the
document open. Checks that read header and footer parts need them parsed before
the package closes, so resolution happens up front and the context hands out
ordinary elements.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from thesis_refinery.config import Checks, Identity
from thesis_refinery.docxlib import drawings as drawings_module
from thesis_refinery.docxlib.ns import RELTYPE, q
from thesis_refinery.docxlib.pkgs import Package
from thesis_refinery.docxlib.xmlq import Element, find, iter_descendants, text_of
from thesis_refinery.style import StyleSpec
from thesis_refinery.verify.findings import Expectations
from thesis_refinery.verify.styles import StyleIndex, paragraph_style_id

__all__ = ["Context", "open_document", "section_kinds"]

DOCUMENT_PART = "word/document.xml"
STYLES_PART = "word/styles.xml"
SETTINGS_PART = "word/settings.xml"


@dataclass
class Context:
    """A document under test, with the configuration it is being tested against."""

    path: Path
    package: Package
    style: StyleSpec
    identity: Identity
    expectations: Expectations

    root: Element
    body: Element
    styles: StyleIndex
    settings: Element | None = None
    prefix_source: Path | None = None
    header_parts: dict[str, Element] = field(default_factory=dict)
    footer_parts: dict[str, Element] = field(default_factory=dict)

    # -- structure ---------------------------------------------------------

    def all_body_paragraphs(self) -> list[Element]:
        """Every direct ``w:p`` child of ``w:body``, front matter included."""
        return [child for child in self.body if child.tag == q("p")]

    def front_matter(self) -> list[Element]:
        """Paragraphs and cover tables that precede the first level-1 heading.

        This is a property of the document rather than of a check, because two
        checks need it: the front-matter checks compare against it, and the body
        checks must *exclude* it. A cover is not body text, and demanding a
        first-line indent of the cover is the kind of false positive that gets a
        gate switched off.

        Tables are included rather than skipped: a cover page is very often a
        form, and the fields an identity check looks for are inside that table.
        """
        found: list[Element] = []
        for child in self.body:
            if child.tag == q("tbl"):
                found.append(child)
                continue
            if child.tag != q("p"):
                continue
            if self.is_level_one_heading(child):
                break
            found.append(child)
        return found

    def has_level_one_heading(self) -> bool:
        """True when a body paragraph carries a level-1 heading style.

        Without one, the front-matter boundary is undefined and every check that
        depends on it is meaningless, so the checks say so rather than guessing.
        """
        return any(self.is_level_one_heading(child) for child in self.all_body_paragraphs())

    def is_level_one_heading(self, paragraph: Element) -> bool:
        name = self.style_name_of(paragraph)
        return name is not None and self.style.heading_style_matches(name, 1)

    def body_paragraphs(self) -> list[Element]:
        """Direct ``w:p`` children of ``w:body`` that are *not* front matter.

        This is what "body text" means for the purposes of indent and spacing: a
        paragraph in a table cell follows the table's formatting and a paragraph
        on the cover follows the cover's, so demanding the body indent of either
        produces a hundred false positives.
        """
        front = {id(element) for element in self.front_matter()}
        return [child for child in self.all_body_paragraphs() if id(child) not in front]

    def all_paragraphs(self) -> list[Element]:
        """Every paragraph in the document, tables included."""
        return list(iter_descendants(self.root, "w:p"))

    def table_paragraphs(self) -> list[Element]:
        """Paragraphs that live inside a table cell."""
        inside: list[Element] = []
        for table in self.tables():
            inside.extend(iter_descendants(table, "w:p"))
        return inside

    def tables(self) -> list[Element]:
        """Every table, in document order."""
        return list(iter_descendants(self.root, "w:tbl"))

    def sections(self) -> list[Element]:
        """Every ``w:sectPr`` in document order.

        A section break at the end of a paragraph lives in that paragraph's
        ``w:pPr``; the last section's properties live directly on ``w:body``. A
        read of both in document order is the only ordering that matches what
        Word paginates.
        """
        return list(iter_descendants(self.root, "w:sectPr"))

    def section_paragraph(self, section: Element) -> Element | None:
        """The paragraph that carries *section*'s break, if it carries one."""
        parent = section.getparent()
        if parent is None or parent.tag != q("pPr"):
            return None
        grandparent = parent.getparent()
        return grandparent if grandparent is not None and grandparent.tag == q("p") else None

    # -- paragraph access --------------------------------------------------

    def style_id_of(self, paragraph: Element) -> str | None:
        return paragraph_style_id(paragraph)

    def style_name_of(self, paragraph: Element) -> str | None:
        style_id = paragraph_style_id(paragraph)
        if style_id is None:
            return None
        definition = self.styles.definition(style_id)
        return definition.name if definition is not None else style_id

    def is_body_style(self, paragraph: Element) -> bool:
        """True when the paragraph uses one of the configured body styles.

        Matches on both id and display name, because pandoc writes
        ``FirstParagraph`` while Word shows ``First Paragraph``, and a check that
        knows only one of them silently checks nothing.
        """
        style_id = paragraph_style_id(paragraph)
        if style_id is None:
            return False
        definition = self.styles.definition(style_id)
        name = definition.name if definition is not None else style_id
        wanted = {candidate.replace(" ", "") for candidate in self.style.body_style_ids}
        return style_id.replace(" ", "") in wanted or name.replace(" ", "") in wanted

    def is_image_caption(self, paragraph: Element) -> bool:
        return self._is_caption(paragraph, self.style.caption.image_style_ids)

    def is_table_caption(self, paragraph: Element) -> bool:
        return self._is_caption(paragraph, self.style.caption.table_style_ids)

    def is_caption(self, paragraph: Element) -> bool:
        return self.is_image_caption(paragraph) or self.is_table_caption(paragraph)

    def _is_caption(self, paragraph: Element, style_ids: tuple[str, ...]) -> bool:
        style_id = paragraph_style_id(paragraph)
        definition = self.styles.definition(style_id) if style_id else None
        name = definition.name if definition is not None else style_id
        wanted = {candidate.replace(" ", "").lower() for candidate in style_ids}
        return (
            (style_id or "").replace(" ", "").lower() in wanted
            or (name or "").replace(" ", "").lower() in wanted
        )

    def caption_paragraphs(self) -> list[Element]:
        return [p for p in self.all_paragraphs() if self.is_caption(p)]

    def paragraphs_with_caption_prefix(self) -> list[Element]:
        """Paragraphs whose text starts with a configured caption prefix.

        The difference between this and :meth:`caption_paragraphs` is the whole
        point of the pair: text that looks like a caption but does not carry the
        caption style is a caption the formatting step will skip, and it renders
        in body text.
        """
        prefixes = (*self.style.caption.image_prefixes, *self.style.caption.table_prefixes)

        found: list[Element] = []
        for paragraph in self.all_paragraphs():
            text = text_of(paragraph).strip()
            if any(text.startswith(prefix) for prefix in prefixes):
                found.append(paragraph)
        return found

    # -- media -------------------------------------------------------------

    def drawings(self) -> list[drawings_module.Drawing]:
        return list(drawings_module.iter_drawings(self.root))

    def media_parts(self) -> list[str]:
        return [name for name in self.package.names() if name.startswith("word/media/")]

    def part_names(self) -> list[str]:
        return self.package.names()


@contextmanager
def open_document(
    path: Path,
    *,
    style: StyleSpec,
    identity: Identity,
    checks: Checks,
    prefix_source: Path | None = None,
) -> Iterator[Context]:
    """Open *path* and resolve everything a check may need.

    *checks* carries the configured expected counts; a value of ``"auto"`` becomes
    ``None``, meaning "derive it from the document you are looking at".
    """
    from thesis_refinery.config import AUTO

    def expectation(value: int | str) -> int | None:
        return None if value == AUTO else int(value)

    path = Path(path)
    with Package(path) as package:
        root = package.xml(DOCUMENT_PART)
        body = find(root, "w:body")
        if body is None:
            raise ValueError(f"{path} has no w:body in {DOCUMENT_PART}")

        styles_root = package.xml(STYLES_PART) if STYLES_PART in package else None
        settings_root = package.xml(SETTINGS_PART) if SETTINGS_PART in package else None

        context = Context(
            path=path,
            package=package,
            style=style,
            identity=identity,
            expectations=Expectations(
                captions=expectation(checks.expect_captions),
                body_paragraphs=expectation(checks.expect_body_paragraphs),
                reference_entries=expectation(checks.expect_reference_entries),
                sections=expectation(checks.expect_sections),
                drawings=expectation(checks.expect_drawings),
            ),
            root=root,
            body=body,
            styles=StyleIndex(styles_root),
            settings=settings_root,
            prefix_source=Path(prefix_source) if prefix_source else None,
        )

        context.header_parts, context.footer_parts = _load_headers_and_footers(package)
        yield context


def _load_headers_and_footers(package: Package) -> tuple[dict[str, Element], dict[str, Element]]:
    """Parse every header and footer part, keyed by part name.

    Resolved eagerly: the parts are read while the package is open, and a check
    that tried to open one later would be reading a closed zip.
    """
    headers: dict[str, Element] = {}
    footers: dict[str, Element] = {}

    for relationship in package.relationships(DOCUMENT_PART):
        reltype = relationship.get("Type", "")
        target = relationship.get("Target", "")
        if not target:
            continue
        part = _resolve_document_target(target)
        if part not in package:
            continue
        if reltype == RELTYPE["header"]:
            headers[part] = package.xml(part)
        elif reltype == RELTYPE["footer"]:
            footers[part] = package.xml(part)

    return headers, footers


def _resolve_document_target(target: str) -> str:
    """Turn a relationship target into a part name.

    Header and footer targets are relative to ``word/``, e.g. ``header1.xml``
    means ``word/header1.xml``. An absolute target already names a part.
    """
    if target.startswith("/"):
        return target.lstrip("/")
    return f"word/{target}"


def section_kinds(context: Context) -> list[str]:
    """The ``w:type`` of each section, defaulting to ``nextPage`` as Word does."""
    kinds: list[str] = []
    for section in context.sections():
        kind = find(section, "w:type")
        kinds.append(kind.get(q("val"), "nextPage") if kind is not None else "nextPage")
    return kinds
