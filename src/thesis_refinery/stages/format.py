"""Style application: turn a rendered document into a conforming one.

This is the stage between "the renderer produced a file" and "the gates can be
asked whether it is submittable". It reads one document, writes another, and takes
every number, name and size from :class:`~thesis_refinery.style.StyleSpec`.

Five decisions shape it, each a reaction to a way this goes wrong.

**The input is opened, never written.** :class:`~thesis_refinery.docxlib.pkgs.Package`
is copy-on-write per part and refuses to save over its own input, so "the source is
untouched" is a property of the design rather than a rule to remember. This module
does not work around that refusal: working around it is how a build eats a thesis.

**A part whose bytes did not change is not written.** Every write goes through
:func:`_write_if_changed`, which compares the bytes it would write against the
bytes the part already has. :attr:`Package.changed` then means "this stage altered
this" rather than "this stage looked at this", so "what did the formatting step
do?" has a short answer a human can read and a test can assert on.

**Page setup and page numbering go on every section.** Word stores both per
section: a document whose first section is A4 and whose appendix is Letter prints
correctly in the middle and wrong at the end. The one thing that genuinely is
positional is which side of the book a section is on — the first section is front
matter and takes the front-matter margins, every later section is body.

**Body metrics prefer the paragraph style over the paragraph.** pandoc puts body
formatting on the style, so a document is edited by a human in the style dialog; a
stage that wrote the indent onto every paragraph would fight every later edit and
multiply the diff by the paragraph count. Direct formatting is used only when the
document defines no such style, or when the paragraph already overrides the
property directly — the direct value is the one that wins, and quietly setting the
style instead would leave the paragraph rendering exactly as it was.

**Running heads are rewritten, not accumulated.** The specification names the
running head, so the part that carries it is written from the specification rather
than appended beside whatever was there. A document with two running heads is worse
than one with the wrong one, because the wrong one is at least visible in the
gate's report.

Three things this stage deliberately does not do, each with the alternative it was
chosen over:

* It does not touch ``w:pgSz/@w:orient``. The alternative — deriving it from the
  configured width and height — would set an attribute no check reads, on a page
  that may legitimately be printed sideways for a fold-out figure.
* It does not apply ``ParagraphSpec.space_before_twips`` / ``space_after_twips``.
  Nothing in the specification's *checks* reads them, and this stage's remit is the
  two properties the body check asks about; the alternative is a stage that writes
  values nobody verifies.
* It does not manufacture a ``word/settings.xml`` to carry a flag. See
  :func:`_apply_settings` for why, and for what that costs.

The traversal here is written out rather than imported from
:mod:`thesis_refinery.verify.context`, even though the checker answers the same
questions. That is the project's rule, not an oversight: the applier and the
checker share the *specification* (data) and must not share the *walk*, because a
bug in a shared walk cancels itself out and the gate then passes on a document that
is wrong in exactly the way the walk is wrong.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from thesis_refinery.docxlib import fields, tables
from thesis_refinery.docxlib.ns import CONTENT_TYPE, NSMAP, RELTYPE, q, qn
from thesis_refinery.docxlib.pkgs import Package, content_types_part
from thesis_refinery.docxlib.xmlq import (
    Element,
    ensure_ordered,
    find,
    findall,
    iter_descendants,
    new,
    ordered_insert,
    text_of,
)
from thesis_refinery.style import ParagraphSpec, StyleSpec

__all__ = ["apply"]


# ---------------------------------------------------------------------------
# Names and OOXML vocabulary
# ---------------------------------------------------------------------------
# Part names are not formatting facts: OPC gives each part exactly one spelling,
# and the checker names them again on its own side, on purpose.
DOCUMENT_PART = "word/document.xml"
STYLES_PART = "word/styles.xml"
SETTINGS_PART = "word/settings.xml"

_KIND_ROOT: dict[str, str] = {"header": "hdr", "footer": "ftr"}
"""The element a header or footer part hangs off."""

_DEFAULT = "default"
_EVEN = "even"
"""``w:type`` on a header or footer reference. ``default`` covers every page that
no more specific reference claims, which is why a document needs it to have a
running head at all."""

_XML_TRUE = "true"
_FALSE_SPELLINGS = ("false", "0", "off")
"""What OOXML accepts for an "off" settings flag. All three appear in documents."""

_RESTART_AT = "1"
"""Where a page-number format change restarts. Not a specification value: the
specification carries the two *formats*, and every thesis restarts the body at 1.
The alternative — continuing the count — is the ``vii, viii, 9, 10`` defect that
:mod:`thesis_refinery.verify.checks.numbering` exists to catch."""

_LINE_RULE_AUTO = "auto"
"""``w:lineRule``: ``w:line`` is a multiple of single spacing, not a fixed height."""

_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

_METRIC_TAGS = ("w:ind", "w:spacing")
"""The two paragraph properties this stage applies to body text."""

_AFTER_STYLE_PARAGRAPH_PROPERTIES = ("w:rPr", "w:tblPr", "w:trPr", "w:tcPr", "w:tblStylePr")
"""In a ``w:style``, the elements that must follow ``w:pPr`` per CT_Style."""


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------


def apply(source: Path, destination: Path, *, style: StyleSpec) -> Path:
    """Read *source*, apply the style specification, write *destination*.

    Every value comes from *style*: this module contains no millimetre, point size,
    style name or border size. *source* is opened read-only and never written —
    :meth:`Package.save` refuses a destination that is its own input, and this
    stage relies on that refusal rather than working around it.

    Returns *destination*.
    """
    source = Path(source)
    destination = Path(destination)

    with Package(source) as package:
        document = package.xml(DOCUMENT_PART)
        body = find(document, "w:body")
        if body is None:
            raise ValueError(f"{source} has no w:body in {DOCUMENT_PART}")

        styles_root = package.xml(STYLES_PART) if package.exists(STYLES_PART) else None
        records = _style_records(styles_root)

        document_before = _snapshot(document)
        styles_before = _snapshot(styles_root) if styles_root is not None else None

        sections = _sections(document, body)
        heading_index = _heading_index(body, style=style, records=records)

        _apply_page_setup(sections, style)
        _apply_numbering(sections, _first_body_section(sections, heading_index), style)
        _apply_body_metrics(body, style=style, records=records, heading_index=heading_index)
        _apply_three_line_tables(document, style)

        even_header = _apply_running_heads(
            package,
            sections,
            style,
            heading_style_name=_level_one_style_name(records, style),
            heading_text=text_of(body[heading_index]) if heading_index is not None else "",
        )
        _apply_settings(package, even_header=even_header)

        _write_if_changed(package, DOCUMENT_PART, document, document_before)
        if styles_root is not None and styles_before is not None:
            _write_if_changed(package, STYLES_PART, styles_root, styles_before)

        return package.save(destination)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Section:
    """One ``w:sectPr`` together with the body child it closes."""

    element: Element
    closes_at: int
    """Index into ``w:body``'s children of the last one this section contains.

    A section break lives in the ``w:pPr`` of the paragraph it ends, and the last
    section lives on ``w:body`` itself, closing at the end of the body. Knowing
    where a section *ends* is what makes "the sections after the first heading"
    answerable at all.
    """


def _sections(document: Element, body: Element) -> list[_Section]:
    """Every ``w:sectPr``, in document order, with the body child it closes.

    Both places a section can live are read, because Word paginates in that order
    and a stage that read only one of them would format the last section of the
    document with the *first* section's margins — or, worse, not at all.

    The association is keyed by each node's XPath rather than by ``id()``. lxml
    hands out proxy objects: a proxy that has been garbage collected lets a *later*
    proxy for a *different* node reuse its ``id``, and a dictionary keyed on that
    quietly maps one section's properties onto another's — a bug that shows up as
    the wrong page-number format in one section and nowhere else.
    """
    tree = document.getroottree()
    closes_at: dict[str, int] = {}

    for index, child in enumerate(body):
        if child.tag != q("p"):
            continue
        properties = find(child, "w:pPr")
        section = find(properties, "w:sectPr") if properties is not None else None
        if section is not None:
            closes_at[tree.getpath(section)] = index

    final = find(body, "w:sectPr")
    if final is not None:
        closes_at[tree.getpath(final)] = len(body)

    return [
        _Section(
            element=element,
            closes_at=closes_at.get(tree.getpath(element), len(body)),
        )
        for element in iter_descendants(document, "w:sectPr")
    ]


def _first_body_section(sections: Sequence[_Section], heading_index: int | None) -> int:
    """Index of the first section the level-1 heading belongs to.

    The section that closes at or after the heading is the first body section;
    everything before it is front matter and is numbered in the front-matter
    format. With no heading the boundary is undefined, so every section counts as
    front matter and the gate fails the document for the missing heading rather
    than for a page number — one defect reported once.
    """
    if heading_index is None:
        return len(sections)

    for index, section in enumerate(sections):
        if section.closes_at >= heading_index:
            return index

    return len(sections)


def _apply_page_setup(sections: Sequence[_Section], style: StyleSpec) -> None:
    """Set page size, margins and header/footer distances on every section.

    On **every** section, because Word stores page setup per section: a document
    whose appendix is a different size prints correctly in the middle and wrong at
    the end. The checks read the same way, per section, and would report the
    appendix rather than the body.

    The first section takes the front-matter side margins and the rest take the
    body's. Two side values rather than one because a bound thesis is given a wider
    gutter on the cover and declaration pages, and those pages are a section of
    their own — the alternative, one margin everywhere, is wrong at one end of the
    document whichever value you pick.
    """
    margins = style.margins

    for index, section in enumerate(sections):
        side = margins.front_matter_side_twips if index == 0 else margins.body_side_twips

        size = ensure_ordered(section.element, "w:pgSz")
        size.set(q("w"), str(style.page.width_twips))
        size.set(q("h"), str(style.page.height_twips))

        page_margins = ensure_ordered(section.element, "w:pgMar")
        for attribute, value in (
            ("top", margins.top_twips),
            ("bottom", margins.bottom_twips),
            ("left", side),
            ("right", side),
            ("header", margins.header_distance_twips),
            ("footer", margins.footer_distance_twips),
        ):
            page_margins.set(q(attribute), str(value))


def _apply_numbering(sections: Sequence[_Section], first_body: int, style: StyleSpec) -> None:
    """Give every section a page-number format, and restart where the format changes.

    Front matter is numbered in roman and the body in arabic, so the body has to
    declare ``w:start="1"``: without it the arabic count carries on from the front
    matter's last roman page, and the printed thesis reads ``vii, viii, 9, 10`` — a
    defect invisible in a screenshot of any single page.

    The restart is written **only** at the change, not on every body section:
    restarting the count at each section break is a different defect with the same
    root cause, and it is the one an all-sections fix would introduce.
    """
    numbers = style.header_footer
    format_changes = numbers.front_matter_number_format != numbers.body_number_format

    for index, section in enumerate(sections):
        element = ensure_ordered(section.element, "w:pgNumType")
        if index < first_body:
            element.set(q("fmt"), numbers.front_matter_number_format)
        else:
            element.set(q("fmt"), numbers.body_number_format)
            if format_changes and index == first_body:
                element.set(q("start"), _RESTART_AT)


# ---------------------------------------------------------------------------
# Body paragraphs
# ---------------------------------------------------------------------------


def _apply_body_metrics(
    body: Element,
    *,
    style: StyleSpec,
    records: Mapping[str, _StyleRecord],
    heading_index: int | None,
) -> None:
    """Give body paragraphs their first-line indent and line spacing.

    Only paragraphs *after* the first level-1 heading. A cover is not body text,
    and demanding a first-line indent of the cover is the kind of false positive
    that gets a gate switched off; the heading is the boundary because a page count
    is a number that changes.

    Only body-level paragraphs, too. A direct ``w:p`` child of ``w:body`` cannot be
    inside a table, so the table case needs no separate test — and a paragraph
    inside a content control is left alone, because the specification describes
    body text and not what an author wrapped in a control.
    """
    if heading_index is None:
        return

    for child in list(body)[heading_index + 1 :]:
        if child.tag == q("p"):
            _format_body_paragraph(child, style=style, records=records)


def _format_body_paragraph(
    paragraph: Element, *, style: StyleSpec, records: Mapping[str, _StyleRecord]
) -> None:
    """Apply the two body metrics to one paragraph, on the right element.

    Each property is placed independently, because a paragraph may override one of
    them directly and not the other. The rule is exactly the one the checker
    resolves with: a direct value wins, otherwise the value inherited from the
    style chain does. Writing the style value on a paragraph that already overrides
    the property directly would change nothing on the page while looking, in the
    diff, like it had.
    """
    style_id = _paragraph_style_id(paragraph)
    if style_id is None:
        return

    record = records.get(style_id)
    if not _is_body_style(style_id, record.name if record is not None else style_id, style):
        return

    properties = find(paragraph, "w:pPr")
    for tag in _METRIC_TAGS:
        overridden = properties is not None and find(properties, tag) is not None
        if overridden or record is None:
            _ensure_metrics(paragraph, tag, style.body)
        else:
            _ensure_metrics(record.element, tag, style.body)


def _ensure_metrics(container: Element, tag: str, spec: ParagraphSpec) -> None:
    """Set one body metric on a paragraph or on a style.

    "Container" rather than "paragraph" because both carry the property in a
    ``w:pPr``; only the way that ``w:pPr`` is obtained differs, and that is
    :func:`_paragraph_properties`' job.
    """
    properties = _paragraph_properties(container)

    if tag == "w:ind":
        indent = ensure_ordered(properties, "w:ind")
        indent.set(q("firstLine"), str(spec.first_line_indent_twips))
        # `w:firstLine` and `w:hanging` are two spellings of the same slot. A
        # leftover hanging indent would win, and the paragraph would render flush
        # with its first line outdented — the opposite of the specification.
        if indent.get(q("hanging")) is not None:
            del indent.attrib[q("hanging")]
        return

    spacing = ensure_ordered(properties, "w:spacing")
    spacing.set(q("line"), str(spec.line_spacing_twips))
    # `w:line` is ambiguous without the rule: Word reads 360 as 1.5 lines under
    # `auto` and as 18 pt under `exact`, and only the first is what the
    # specification means by "1.5 lines". Leaving a stale `exact` in place would
    # make the printed page disagree with a value that checks out.
    spacing.set(q("lineRule"), _LINE_RULE_AUTO)


def _paragraph_properties(container: Element) -> Element:
    """The ``w:pPr`` of a paragraph or of a style, created where the schema wants it.

    ``xmlq.ordered_insert`` knows CT_PPr's child order but not CT_Style's — its
    table covers the parents this pipeline inserts into, and ``w:style`` is not one
    of them — and it refuses rather than guesses, which is right. So the anchor is
    named here: ``w:pPr`` precedes the run and table properties, and everything
    else a ``w:style`` may carry precedes ``w:pPr``. Bluntly appending would put
    ``w:pPr`` after ``w:rPr``, and Word rejects that document as corrupt.
    """
    existing = find(container, "w:pPr")
    if existing is not None:
        return existing

    element = new("w:pPr")
    for name in _AFTER_STYLE_PARAGRAPH_PROPERTIES:
        follower = find(container, name)
        if follower is not None:
            follower.addprevious(element)
            return element

    # A paragraph has nothing that may precede `w:pPr`, and a style with no run or
    # table properties has nothing it must precede, so the schema position is the
    # end. `ordered_insert` is still asked: for a parent it does not know, the
    # escape hatch is named explicitly rather than being the default.
    ordered_insert(container, element, on_unknown="append")
    return element


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StyleRecord:
    """One ``w:style`` reduced to what this stage needs."""

    style_id: str
    name: str
    element: Element


def _style_records(styles_root: Element | None) -> dict[str, _StyleRecord]:
    """Index a styles part by ``w:styleId``.

    By id, because ``w:pStyle`` carries the id while Word shows the name — the two
    differ for every built-in style in a localised document, and matching on the
    wrong one finds nothing at all and formats nothing, silently.
    """
    records: dict[str, _StyleRecord] = {}
    if styles_root is None:
        return records

    for style in iter_descendants(styles_root, "w:style"):
        style_id = style.get(q("styleId"))
        if not style_id or style_id in records:
            continue
        name = find(style, "w:name")
        records[style_id] = _StyleRecord(
            style_id=style_id,
            name=name.get(q("val"), style_id) if name is not None else style_id,
            element=style,
        )

    return records


def _paragraph_style_id(paragraph: Element) -> str | None:
    """The paragraph's ``w:pStyle``, or ``None`` when it carries none.

    A paragraph with no ``w:pStyle`` is not a body paragraph: the checker matches
    on the style, so exempting it here and there agree.
    """
    properties = find(paragraph, "w:pPr")
    if properties is None:
        return None
    style = find(properties, "w:pStyle")
    return style.get(q("val")) if style is not None else None


def _style_name_of(paragraph: Element, records: Mapping[str, _StyleRecord]) -> str | None:
    """The display name of a paragraph's style, falling back to its id."""
    style_id = _paragraph_style_id(paragraph)
    if style_id is None:
        return None
    record = records.get(style_id)
    return record.name if record is not None else style_id


def _is_body_style(style_id: str, name: str, style: StyleSpec) -> bool:
    """True when a style is one of the configured body styles.

    Matched on both the id and the display name, with spaces removed, because
    pandoc writes ``BodyText`` where Word shows ``Body Text``. A stage that knows
    only one spelling formats nothing while reporting success, which is the failure
    mode this project exists to end.
    """
    wanted = {candidate.replace(" ", "") for candidate in style.body_style_ids}
    return style_id.replace(" ", "") in wanted or name.replace(" ", "") in wanted


def _heading_index(
    body: Element, *, style: StyleSpec, records: Mapping[str, _StyleRecord]
) -> int | None:
    """Where the front matter ends: the first level-1 heading, or ``None``.

    The boundary is a *style*, not a page count, because a page count is a number
    that changes. Both spellings of the level-1 style are accepted, because a
    document formatted for a Chinese locale carries ``标题 1`` where the
    specification's canonical name is ``Heading 1`` — and a scan that knows only
    the English one runs to the end of the document and indents the cover.
    """
    for index, child in enumerate(body):
        if child.tag != q("p"):
            continue
        name = _style_name_of(child, records)
        if name is not None and style.heading_style_matches(name, 1):
            return index
    return None


def _level_one_style_name(records: Mapping[str, _StyleRecord], style: StyleSpec) -> str:
    """The name of the document's level-1 heading style, for a ``STYLEREF``.

    A ``STYLEREF`` is resolved by Word against the style *name*, so the name the
    document actually carries is used. Naming the canonical spelling on a document
    whose heading style is ``标题 1`` produces a running head that reads "Error!
    Reference source not found." on every even page. Both spellings are configured,
    so this is a choice between two facts rather than an invention; the canonical
    one is the fallback when the document defines neither.
    """
    for record in records.values():
        if style.heading_style_matches(record.name, 1):
            return record.name
    return style.heading_styles[0] if style.heading_styles else ""


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def _apply_three_line_tables(document: Element, style: StyleSpec) -> None:
    """Make every table a three-line table.

    Every table, nested ones included: a table inside a cell is still printed as a
    table, and a rule that reached only the outer one leaves the inner one boxed.
    The border sizes are the specification's, not this module's — the checker reads
    the same :class:`~thesis_refinery.style.TableSpec`, so a size typed here would
    be a second copy of the fact.
    """
    for table in iter_descendants(document, "w:tbl"):
        tables.apply_three_line_borders(table, style.table)


# ---------------------------------------------------------------------------
# Running heads and footers
# ---------------------------------------------------------------------------


def _apply_running_heads(
    package: Package,
    sections: Sequence[_Section],
    style: StyleSpec,
    *,
    heading_style_name: str,
    heading_text: str,
) -> bool:
    """Ensure a default header and footer, plus the even header when asked for.

    Returns ``True`` when an ``even`` header was referenced, which is what the
    document-wide "different even and odd pages" flag has to accompany.

    A document with no sections gets no header parts. Nothing would reference them,
    and an unreferenced part is not free: the gate reads header parts through the
    document's relationships, so it would still have to conform.
    """
    if not sections:
        return False

    header_id = _ensure_running_part(
        package,
        sections,
        kind="header",
        side=_DEFAULT,
        fill=lambda root: _write_default_header(root, style=style),
    )
    _point_sections(sections, kind="header", side=_DEFAULT, rel_id=header_id)

    footer_id = _ensure_running_part(
        package,
        sections,
        kind="footer",
        side=_DEFAULT,
        fill=lambda root: _write_footer(root, style=style),
    )
    _point_sections(sections, kind="footer", side=_DEFAULT, rel_id=footer_id)

    if not style.header_footer.even_page_uses_heading:
        return False

    even_id = _ensure_running_part(
        package,
        sections,
        kind="header",
        side=_EVEN,
        fill=lambda root: _write_even_header(
            root, style=style, style_name=heading_style_name, text=heading_text
        ),
    )
    _point_sections(sections, kind="header", side=_EVEN, rel_id=even_id)
    return True


def _ensure_running_part(
    package: Package,
    sections: Sequence[_Section],
    *,
    kind: str,
    side: str,
    fill: Callable[[Element], None],
) -> str:
    """Make one header or footer part reachable from the document, and return its ``rId``.

    The part is **reused** when a section already references one for this side, and
    created otherwise. Reuse matters twice over. Creating a second part would not
    take the first one out of scope — the gate reads header parts through the
    document's relationships, not through the references — so a fresh part adds a
    second part that must also conform; and a document that ends up with an
    orphaned header still carries it in the file.

    The content is written, not appended to. The specification names the running
    head, so a part that kept an older one beside the new one would print two.

    A section that referenced a *different* part for this side is repointed at the
    part this stage keeps, because the specification carries one running head and
    not one per section. The abandoned part stays related to the document, so the
    gate still reads it — a document that declares two competing default headers is
    a document whose headers have to be reconciled by hand. That is the smaller
    surprise: the alternative, leaving both, prints a different head on different
    pages of the same submission.
    """
    part = _referenced_part(
        package, sections, _relationship_targets(package, kind), kind=kind, side=side
    )

    if part is None:
        created = _new_part_root(kind)
        fill(created)
        part = _next_part_name(package, kind)
        package.replace_xml(part, created)
        rel_id = _ensure_relationship(package, kind, part)
        _ensure_content_type(package, part, CONTENT_TYPE[kind])
        return rel_id

    root = package.xml(part)
    before = _snapshot(root)
    for child in list(root):
        root.remove(child)
    fill(root)
    _write_if_changed(package, part, root, before)
    return _ensure_relationship(package, kind, part)


def _write_default_header(root: Element, *, style: StyleSpec) -> None:
    """The default running head: the configured wording, ruled off from the body.

    The rule is drawn only when the specification asks for one, and the wording may
    be empty: an institution that requires no running head is a real case, and the
    part is still created so every section can reference a header. A section with no
    reference inherits the section before it, which is how one chapter silently
    loses its head.
    """
    paragraph = _add_paragraph(root)
    if style.header_footer.odd_page_text:
        ordered_insert(paragraph, _text_run(style.header_footer.odd_page_text))
    if style.header_footer.rule_below_header:
        tables.set_paragraph_bottom_border(paragraph)


def _write_even_header(
    root: Element, *, style: StyleSpec, style_name: str, text: str
) -> None:
    """The even-page running head: the current chapter, through a ``STYLEREF`` field.

    The field's cached result is the first chapter's title rather than nothing,
    because a field whose result is empty shows no running head at all until Word
    refreshes it — and the document as committed should show what the author will
    see on the page.

    The rule below the head is drawn here too, and that is not decoration: the gate
    asks it of *every* header part, so an even header without one would make the
    formatted document fail the check the default header just satisfied.
    """
    paragraph = _add_paragraph(root)
    if style_name:
        for run in fields.styleref_field(style_name, result=text):
            ordered_insert(paragraph, run)
    if style.header_footer.rule_below_header:
        tables.set_paragraph_bottom_border(paragraph)


def _write_footer(root: Element, *, style: StyleSpec) -> None:
    """The folio: a ``PAGE`` field at the configured size.

    The size goes on **every** run of the field, not only on the cached result.
    Word rebuilds the result from the field's own runs when it refreshes, so a size
    that lived only in the cache would be dropped the first time the field updated
    and the printed folio would render at whatever the paragraph default happens to
    be — a size nothing in the specification asked for, on every page. The checker
    reads the first run that declares a ``w:sz``; this is the same answer by a
    different route.
    """
    half_points = style.header_footer.footer_size_half_points
    paragraph = _add_paragraph(root)

    for run in fields.page_number_field(run_properties=_size_properties(half_points)):
        _set_size(ensure_ordered(run, "w:rPr"), half_points)
        ordered_insert(paragraph, run)


def _add_paragraph(root: Element) -> Element:
    """An empty ``w:p`` in a header or footer part.

    ``ordered_insert`` is asked with ``on_unknown="append"``: CT_HdrFtr is not in
    ``xmlq.SCHEMA_ORDER``, and the part was just created empty, so the end of the
    document *is* the schema position rather than a guess.
    """
    paragraph = new("w:p")
    ordered_insert(root, paragraph, on_unknown="append")
    return paragraph


def _text_run(text: str) -> Element:
    """A ``w:r`` carrying one ``w:t``.

    ``xml:space="preserve"`` because a running head may begin or end with a space,
    and Word drops it without the attribute — which turns a spaced-out head into a
    run-together one on the printed page only.
    """
    run = new("w:r")
    text_element = new("w:t")
    text_element.set(_XML_SPACE, "preserve")
    text_element.text = text
    ordered_insert(run, text_element)
    return run


def _size_properties(half_points: int) -> Element:
    """A ``w:rPr`` carrying a font size, for a field's cached result."""
    properties = new("w:rPr")
    _set_size(properties, half_points)
    return properties


def _set_size(run_properties: Element, half_points: int) -> None:
    """Set ``w:sz`` and ``w:szCs`` on a run-properties element.

    Both, because ``w:szCs`` governs complex-script text: a document that sets only
    ``w:sz`` renders a Chinese or Arabic folio at the paragraph default, which is
    the same defect one locale later.
    """
    for name in ("w:sz", "w:szCs"):
        ensure_ordered(run_properties, name).set(q("val"), str(half_points))


def _point_sections(
    sections: Sequence[_Section], *, kind: str, side: str, rel_id: str
) -> None:
    """Reference a part from every section as the header or footer of this side.

    Every section, because a section that inherits its header references from the
    section before it inherits whichever header that one happened to carry: a
    chapter split into its own section silently loses its running head. The
    ``w:type`` is written out rather than left to the schema default, so the
    reference's meaning does not depend on which reader is looking at it.
    """
    for section in sections:
        reference = _reference_of(section.element, kind=kind, side=side)
        if reference is None:
            reference = new(f"w:{kind}Reference", **{"w:type": side})
            ordered_insert(section.element, reference)
        reference.set(qn("r:id"), rel_id)


def _reference_of(section: Element, *, kind: str, side: str) -> Element | None:
    """A section's reference of this kind and this side, if it has one.

    A missing ``w:type`` means ``default`` in the schema and Word writes it that
    way, so treating it as anything else would miss the reference a document
    actually has — and then add a second one beside it.
    """
    for reference in findall(section, f"w:{kind}Reference"):
        if (reference.get(q("type")) or _DEFAULT) == side:
            return reference
    return None


def _new_part_root(kind: str) -> Element:
    """An empty ``w:hdr`` or ``w:ftr`` root.

    ``xmlq.new`` deliberately emits no namespace declaration, which is right for an
    element about to be inserted into a tree that already declares one, and wrong
    for a part root: with no ancestor to inherit from, the namespace would be
    serialised under a generated prefix such as ``ns0``. A part that does not look
    like Word wrote it invites suspicion from strict readers.
    """
    return etree.Element(qn(f"w:{_KIND_ROOT[kind]}"), nsmap={"w": NSMAP["w"]})


def _next_part_name(package: Package, kind: str) -> str:
    """An unused ``word/headerN.xml`` or ``word/footerN.xml`` name.

    Word numbers these from one, and continuing that sequence keeps a document this
    stage wrote indistinguishable from one Word built. It also never collides with
    a part that is already there but no longer referenced — which is exactly the
    part a naive ``header1.xml`` would overwrite.
    """
    highest = 0
    for name in package.names():
        directory, _, filename = name.rpartition("/")
        if directory != "word" or not filename.endswith(".xml"):
            continue
        number = filename[len(kind) : -len(".xml")] if filename.startswith(kind) else ""
        if number.isdigit():
            highest = max(highest, int(number))
    return f"word/{kind}{highest + 1}.xml"


def _relationship_targets(package: Package, kind: str) -> dict[str, str]:
    """The document's ``rId`` → part name for one relationship type.

    Targets are relative to ``word/``, which is where ``word/document.xml`` lives;
    an absolute target already names a part.
    """
    targets: dict[str, str] = {}
    for relationship in package.relationships(DOCUMENT_PART):
        if relationship.get("Type") != RELTYPE[kind]:
            continue
        target = relationship.get("Target", "")
        if not target:
            continue
        targets[relationship.get("Id", "")] = (
            target.lstrip("/") if target.startswith("/") else f"word/{target}"
        )
    return targets


def _referenced_part(
    package: Package,
    sections: Sequence[_Section],
    targets: Mapping[str, str],
    *,
    kind: str,
    side: str,
) -> str | None:
    """The part some section references for this kind and side, if any does.

    The relationship is followed rather than the name assumed: a document may call
    its running head anything, and a stage that looked for ``header1.xml`` would
    create a second header instead of editing the one on the page.

    A relationship that points at a part the package does not contain is skipped.
    Dangling relationships are common in documents edited by several tools, and
    treating one as an existing header would mean writing to a part that is not
    there.
    """
    for section in sections:
        reference = _reference_of(section.element, kind=kind, side=side)
        if reference is None:
            continue
        part = targets.get(reference.get(qn("r:id"), ""))
        if part is not None and package.exists(part):
            return part
    return None


def _ensure_relationship(package: Package, kind: str, part_name: str) -> str:
    """The document's relationship to *part_name*, creating it only when it is new."""
    target = part_name.removeprefix("word/")
    existing = package.find_relationship(DOCUMENT_PART, kind, target=target)
    if existing is not None:
        return existing.get("Id", "")
    return package.add_relationship(DOCUMENT_PART, kind, target)


def _ensure_content_type(package: Package, part_name: str, content_type: str) -> None:
    """Declare a part's content type, without rewriting the part when it is right.

    ``Package.ensure_content_type_override`` records ``[Content_Types].xml`` as
    changed the moment it is called, whether or not the override was already there
    and correct — so a reused header part would drag the content-types part into
    the diff for nothing. The answer is checked first.
    """
    wanted = f"/{part_name}"
    root = package.xml(content_types_part())
    declared = any(
        child.tag == qn("ct:Override")
        and child.get("PartName") == wanted
        and child.get("ContentType") == content_type
        for child in root
    )
    if not declared:
        package.ensure_content_type_override(part_name, content_type)


# ---------------------------------------------------------------------------
# Document settings
# ---------------------------------------------------------------------------


def _apply_settings(package: Package, *, even_header: bool) -> None:
    """Set the document-wide flags this stage is responsible for.

    ``w:updateFields`` makes Word refresh every field when the document is opened,
    which is what makes a table of contents' page numbers right without anybody
    pressing F9. It is also how a hand-placed placeholder an author intends to fill
    in gets replaced by an empty result, so it is turned on deliberately here
    rather than left to habit; ``docxlib.fields`` carries the same warning.

    ``w:evenAndOddHeaders`` is what makes the ``even`` header mean anything: with
    the flag off, Word prints the default header on every page and the chapter
    title never appears.

    **A settings part is never created.** Both flags live in a part that also holds
    the compatibility mode, the default tab stop and the theme language, and a
    stage that manufactured one to carry a flag would be inventing values it has no
    facts about — the same refusal the ``updateFields`` rule states, applied to
    both. The cost is stated rather than hidden: on a document with no settings
    part the even header is referenced and Word will not use it until the document
    grows one. A document built from a renderer's reference file has one already.
    """
    if not package.exists(SETTINGS_PART):
        return

    settings = package.xml(SETTINGS_PART)
    before = _snapshot(settings)

    if even_header and not _is_true(find(settings, "w:evenAndOddHeaders")):
        ensure_ordered(settings, "w:evenAndOddHeaders").set(q("val"), _XML_TRUE)

    if fields.update_fields_enabled(settings) is not True:
        fields.set_update_fields(settings, True)

    _write_if_changed(package, SETTINGS_PART, settings, before)


def _is_true(element: Element | None) -> bool:
    """True when a settings flag is declared and not spelled as "off"."""
    if element is None:
        return False
    return element.get(q("val"), _XML_TRUE) not in _FALSE_SPELLINGS


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _write_if_changed(package: Package, name: str, root: Element, snapshot: bytes) -> bool:
    """Write *root* back as *name*, but only when doing so changes the tree.

    ``Package.replace_xml`` cannot answer "did this part change?" — calling it
    records the part as changed whether or not anything moved. The comparison is
    made *tree to tree*, with the same serialiser on both sides, rather than against
    the part's original bytes: the project's parser is python-docx's, built with
    ``remove_blank_text``, so a pretty-printed part that was never touched still
    differs from its own bytes and would be reported as changed. Serialising both
    sides makes that difference cancel, and what is left is the only thing worth
    reporting: whether this stage altered the part.

    This is what keeps :attr:`Package.changed` meaning "this stage altered this",
    which is the property a test can assert on and a reviewer can trust.
    """
    if etree.tostring(root) == snapshot:
        return False
    package.replace_xml(name, root)
    return True


def _snapshot(root: Element) -> bytes:
    """A comparable image of a part, taken before this stage mutates it."""
    return etree.tostring(root)
