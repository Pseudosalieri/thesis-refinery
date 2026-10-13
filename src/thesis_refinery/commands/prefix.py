"""``extract-prefix``: cut the leading pages of a finished document into a cover file.

It cuts the front pages out of a finished document and re-imports them as the cover
of every subsequent build. That round trip is why the two clean-ups below are in it
at all: they were both found by *opening the result*, not by reading the code.

**Dangling relationships.** A part that is dropped takes its relationships with it,
but the parts that pointed *at* it keep their entries. An OPC reader that follows
one of those entries fails on a part that is no longer in the package —
``KeyError: "There is no item named 'word/webextensions/taskpanes.xml'"`` is the
shape of it, and it appeared only once the source document grew a
``word/webextensions/`` directory that the older sources did not have. External
targets are kept: they point outside the package and dropping them would break a
hyperlink that still resolves.

**Dangling content-type overrides.** The same problem one level up:
``[Content_Types].xml`` declares a content type for every part by name, so a part
that has been dropped leaves an override naming something that is not there. Only
overrides are removed — the ``Default`` entries are keyed by extension and cover
parts this command never looks at.

The command writes a new file and never modifies its input.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from thesis_refinery.docxlib.ns import q, qn
from thesis_refinery.docxlib.pkgs import Package
from thesis_refinery.docxlib.xmlq import Element, children, deepcopy, find
from thesis_refinery.exitcodes import ExitCode

__all__ = ["DEFAULT_PAGES", "Extraction", "PrefixError", "extract", "run"]

DOCUMENT_PART = "word/document.xml"
DOCUMENT_RELS = "word/_rels/document.xml.rels"
PACKAGE_RELS = "_rels/.rels"
CONTENT_TYPES = "[Content_Types].xml"

DEFAULT_PAGES = 5
"""Five, because a thesis cover is conventionally a cover, a declaration, an
authorisation and a table of contents. The value is a default and not a fact, so
``--pages`` exists; the number that matters is the one the author counts."""

ALWAYS_KEEP = (
    CONTENT_TYPES,
    PACKAGE_RELS,
    DOCUMENT_PART,
    DOCUMENT_RELS,
    "word/styles.xml",
    "word/settings.xml",
    "word/fontTable.xml",
    "word/webSettings.xml",
    "word/numbering.xml",
    "word/theme/",
    "docProps/",
)
"""Parts the rest of a document needs, whether or not the cut pages reference them.

Prefixes, not names: ``word/theme/`` is a directory of parts and ``docProps/``
holds the title and author metadata a reader shows. A cover with no styles part
opens as unstyled text, which is a worse outcome than carrying a part the cover
does not happen to use.
"""


class PrefixError(Exception):
    """Raised when the source cannot be cut, with the reason a reader can act on."""


@dataclass
class Extraction:
    """What a cut kept and what it removed, for the report and for the tests."""

    kept: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    removed_overrides: list[str] = field(default_factory=list)
    removed_relationships: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The cut
# ---------------------------------------------------------------------------


def page_cut(body_children: list[Element], pages: int) -> int:
    """The index just past the *pages*-th page break.

    A page break is ``<w:br w:type="page"/>``. Counting them is the only way to
    find a page boundary in a format that does not store pages: Word computes
    pagination when it lays the document out, and the file records only the breaks
    the author or the renderer inserted.

    Too few breaks is an error rather than a silent short cut. Cutting fewer pages
    than asked produces a cover that is missing its last page, and nothing about
    the result says so.
    """
    seen = 0
    for index, element in enumerate(body_children):
        for br in element.iter(q("br")):
            if br.get(q("type")) == "page":
                seen += 1
        if seen >= pages:
            return index + 1
    raise PrefixError(f"the document has only {seen} page break(s), fewer than the {pages} asked for")


def front_section(children_after: list[Element], body: Element) -> Element:
    """The section properties the cut pages are given.

    The first section break *after* the cut, because that is the section the front
    pages were part of — its page size, margins and header references are the ones
    those pages were laid out with. The body-level ``w:sectPr`` is the fallback for
    a document with no break after the cut.
    """
    for element in children_after:
        if element.tag != q("p"):
            continue
        properties = find(element, "w:pPr")
        if properties is None:
            continue
        section = find(properties, "w:sectPr")
        if section is not None:
            return deepcopy(section)

    body_section = find(body, "w:sectPr")
    if body_section is None:
        raise PrefixError("the document has no section properties to give the cut pages")
    return deepcopy(body_section)


def referenced_ids(nodes: list[Element]) -> set[str]:
    """Every relationship id the *nodes* refer to.

    Any attribute in the relationships namespace whose local name is ``id``,
    ``embed`` or ``link``. Read generically rather than by listing the elements
    that carry one: a drawing, a hyperlink, a header reference and an embedded
    object all use the namespace, and an enumerated list is a list somebody has to
    remember to extend.
    """
    ids: set[str] = set()
    for element in nodes:
        for node in element.iter():
            for attribute, value in node.attrib.items():
                if not isinstance(attribute, str) or attribute not in _REFERENCE_ATTRIBUTES:
                    continue
                ids.add(value)
    return ids


_REFERENCE_ATTRIBUTES = frozenset(qn(f"r:{local}") for local in ("id", "embed", "link"))


def relative_target(base_part: str, target: str) -> str:
    """Resolve a relationship's ``Target`` to a part name."""
    if target.startswith("/"):
        return target.lstrip("/")
    parent = Path(base_part).parent
    resolved = parent / target
    return str(resolved).replace("\\", "/")


def _rels_host(rels_part: str) -> str:
    """The part a rels file belongs to, for resolving its relative targets.

    ``_rels/.rels`` belongs to the package root, so its host is a name in the root
    directory rather than ``_rels/`` itself — the directory a package-root target is
    relative to is the root.
    """
    if rels_part == PACKAGE_RELS:
        return "document.xml"
    return rels_part.replace("_rels/", "", 1).removesuffix(".rels")


# ---------------------------------------------------------------------------
# The clean-ups
# ---------------------------------------------------------------------------


def prune_dangling_rels(root: Element, rels_part: str, keep: set[str]) -> list[str]:
    """Remove relationships that point at parts the cut did not keep.

    Returns the targets removed, so the caller can report them. External targets
    are never removed: they point outside the package, and a hyperlink that
    resolves is not a dangling reference.
    """
    host = _rels_host(rels_part)
    removed: list[str] = []
    for relationship in list(root):
        if relationship.get("TargetMode") == "External":
            continue
        target = relative_target(host, relationship.get("Target") or "")
        if target in keep:
            continue
        removed.append(target)
        root.remove(relationship)
    return removed


def prune_dangling_overrides(root: Element, keep: set[str]) -> list[str]:
    """Remove content-type overrides naming parts the cut did not keep.

    Only ``Override`` is touched. ``Default`` entries are keyed by extension and
    describe every part with that extension, including ones this command never
    looks at; removing one would strip the content type from parts that are still
    in the package.
    """
    removed: list[str] = []
    for override in list(root):
        if etree.QName(override).localname != "Override":
            continue
        name = (override.get("PartName") or "").lstrip("/")
        if name in keep:
            continue
        removed.append(name)
        root.remove(override)
    return removed


# ---------------------------------------------------------------------------
# The extraction
# ---------------------------------------------------------------------------


def extract(
    source: Path,
    destination: Path,
    *,
    pages: int = DEFAULT_PAGES,
) -> Extraction:
    """Cut the first *pages* pages of *source* into *destination*.

    *source* is opened read-only; :meth:`Package.save` refuses a destination that is
    its own input, and this command relies on that refusal rather than working
    around it.
    """
    if pages < 1:
        raise PrefixError(f"--pages must be at least 1, not {pages}")

    source = Path(source)
    destination = Path(destination)
    report = Extraction()

    with Package(source) as package:
        if not package.exists(DOCUMENT_PART):
            raise PrefixError(f"{source} has no {DOCUMENT_PART}")

        document = package.xml(DOCUMENT_PART)
        body = find(document, "w:body")
        if body is None:
            raise PrefixError(f"{source} has no w:body in {DOCUMENT_PART}")

        body_children = [child for child in children(body) if isinstance(child.tag, str)]
        cut = page_cut(body_children, pages)
        front = [deepcopy(child) for child in body_children[:cut]]
        section = front_section(body_children[cut:], body)

        keep = {name for name in package.names() if name.startswith(ALWAYS_KEEP)}
        _collect_referenced_parts(package, [*front, section], keep)

        # Rebuild the body from the cut pages plus the section that lays them out.
        for child in children(body):
            body.remove(child)
        for child in front:
            body.append(child)
        body.append(section)
        package.replace_xml(DOCUMENT_PART, document)

        _keep_only_referenced_relationships(package, [*front, section], keep, report)
        _prune_sub_relationships(package, keep, report)

        with package.edit_xml(CONTENT_TYPES) as content_types:
            report.removed_overrides = prune_dangling_overrides(content_types, keep)

        for name in sorted(keep):
            if not name.endswith(".rels"):
                continue
            with package.edit_xml(name) as rels_root:
                for target in prune_dangling_rels(rels_root, name, keep):
                    report.removed_relationships.append((name, target))

        report.kept = sorted(keep)
        report.dropped = sorted(set(package.names()) - keep)
        for name in report.dropped:
            package.delete(name)

        package.save(destination)

    return report


def _collect_referenced_parts(package: Package, nodes: list[Element], keep: set[str]) -> None:
    """Add every part *nodes* reach, and everything those parts reach, to *keep*.

    Transitively, because a kept header may reference an image and a kept drawing
    may reference a media part: one level of reachability leaves a picture missing
    from a cover that still points at it.
    """
    wanted = referenced_ids(nodes)
    pending = [DOCUMENT_PART]

    while pending:
        part = pending.pop()
        for relationship in package.relationships(part):
            if relationship.get("Id") not in wanted:
                continue
            target = relationship.get("Target") or ""
            if relationship.get("TargetMode") == "External":
                continue
            resolved = relative_target(part, target)
            if resolved in keep or not package.exists(resolved):
                continue
            keep.add(resolved)

            rels = f"{Path(resolved).parent}/_rels/{Path(resolved).name}.rels"
            if package.exists(rels):
                keep.add(rels)
                wanted |= {
                    relationship.get("Id")
                    for relationship in package.relationships(resolved)
                }
            pending.append(resolved)


def _keep_only_referenced_relationships(
    package: Package, nodes: list[Element], keep: set[str], report: Extraction
) -> None:
    """Drop the document's relationships that the cut pages do not use.

    A relationship survives if the pages reference it **or** if it points at a part
    the cut kept anyway. The second clause is not a convenience: ``word/styles.xml``
    is kept whether or not the cover's paragraphs reference it, and a styles part
    the document has no relationship to is a styles part Word does not load. The
    cover's paragraphs carry style ids, so dropping that relationship leaves a cover
    that opens as unstyled text while the styles sit unused in the file.

    Removals are recorded in the report. A cover that silently sheds a relationship
    is a cover whose author cannot tell a deliberate omission from a part the
    command failed to carry across.
    """
    wanted = referenced_ids(nodes)
    host = _rels_host(DOCUMENT_RELS)
    with package.edit_xml(DOCUMENT_RELS) as rels_root:
        for relationship in list(rels_root):
            if relationship.get("Id") in wanted:
                continue
            target = relationship.get("Target") or ""
            if relationship.get("TargetMode") == "External":
                rels_root.remove(relationship)
                continue

            resolved = relative_target(host, target)
            if resolved in keep:
                continue

            report.removed_relationships.append((DOCUMENT_RELS, resolved))
            rels_root.remove(relationship)


def _prune_sub_relationships(package: Package, keep: set[str], report: Extraction) -> None:
    """Within each kept part's own rels, drop what points outside the kept set."""
    for name in sorted(keep):
        if not name.endswith(".rels") or name == DOCUMENT_RELS:
            continue
        with package.edit_xml(name) as rels_root:
            for target in prune_dangling_rels(rels_root, name, keep):
                report.removed_relationships.append((name, target))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
# The argument specification lives in `thesis_refinery.cli`, with every other
# subcommand's, and `test_cli_contract` holds the two ends together. A module that
# also declared its own parser would be a second source of truth for the same
# flags, which is the drift that test exists to catch.


def run(args: argparse.Namespace) -> int:
    """Cut the pages and report what happened. Returns an exit code."""
    try:
        report = extract(Path(args.source), Path(args.out), pages=args.pages)
    except (PrefixError, OSError, KeyError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return ExitCode.USAGE

    if getattr(args, "quiet", False):
        return ExitCode.OK

    print(f"kept {len(report.kept)} part(s), dropped {len(report.dropped)}")
    for part, target in report.removed_relationships:
        print(f"  relationship removed: {part} -> {target}")
    for name in report.removed_overrides:
        print(f"  content type removed: {name}")
    print(f"wrote {args.out}")

    return ExitCode.OK
