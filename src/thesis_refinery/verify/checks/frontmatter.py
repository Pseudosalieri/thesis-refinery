"""Front matter: the cover, declaration and authorisation pages.

The rule these checks encode is blunt, and it is the right one: **the cover is
not to be modified.** Front matter is imported from a source document by a
byte-preserving copy, so the only thing a pipeline can do to it is damage it. The
source of truth for what the cover says is therefore the cover, not the build.

That has two consequences this module implements.

First, the boundary has to be defined before anything can be compared. Front
matter ends where the first level-1 heading begins, which is why
:func:`front_matter_paragraphs` uses the style specification rather than a count
of pages: a page count is a number that changes.

Second, no default identity. If no required strings are configured, the identity
check **skips** rather than inventing a list. A checker with a built-in fallback
verifies whatever the fallback describes — which is how the predecessor spent two
months confirming that a document it was not looking at had the right cover.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.pkgs import Package
from thesis_refinery.docxlib.xmlq import Element, find, local_name, text_of
from thesis_refinery.verify.checks import Check
from thesis_refinery.verify.context import Context
from thesis_refinery.verify.findings import Finding, failed, passed, skipped

CHECK = Check(
    name="frontmatter",
    description="cover boundary, configured identity strings, and source fidelity",
    run=lambda context: run(context),
)

DOCUMENT_PART = "word/document.xml"
_MAX_REPORTED = 10


def run(context: Context) -> Iterator[Finding]:
    front = context.front_matter()
    heading = context.has_level_one_heading()

    yield from _boundary(context, front, heading)
    yield from _identity(context, front)
    yield from _fidelity(context, front)


def _boundary(context: Context, front: list[Element], heading_found: bool) -> Iterator[Finding]:
    """The boundary is only meaningful when a heading marks where the front matter ends.

    Paragraphs are counted separately from the returned list, because that list
    also carries cover tables. Comparing a mixed list against a paragraph count
    makes every branch below unreachable — a check that cannot fail.
    """
    total = len(context.all_body_paragraphs())
    front_paragraphs = [item for item in front if item.tag == q("p")]

    if not heading_found:
        yield failed(
            "frontmatter.boundary",
            "no level-1 heading was found, so the front-matter boundary is undefined",
            (
                "either the body has no heading, or the heading no longer carries a "
                "recognised level-1 style",
                f"configured: {', '.join(context.style.heading_styles)} "
                f"/ {', '.join(context.style.localized_heading_styles)}",
            ),
        )
        return

    if not front_paragraphs:
        yield failed(
            "frontmatter.boundary",
            "the first paragraph is already a level-1 heading: there is no front matter",
            ("the cover, declaration and authorisation pages are missing",),
        )
        return

    yield passed(
        "frontmatter.boundary",
        f"{len(front_paragraphs)} front-matter paragraph(s) of {total}, then the first "
        "level-1 heading",
    )


def _identity(context: Context, front: list[Element]) -> Iterator[Finding]:
    required = context.identity.required
    forbidden = context.identity.forbidden

    if not required and not forbidden:
        yield skipped(
            "frontmatter.identity",
            "no identity strings are configured, so nothing was checked",
            (
                "this check has no default on purpose: a built-in fallback would verify a "
                "different document and report success",
                "set identity.required / identity.forbidden in thesis.yml",
            ),
        )
        return

    text = "\n".join(text_of(paragraph) for paragraph in front)
    missing = [value for value in required if value not in text]
    present = [value for value in forbidden if value in text]

    if missing or present:
        yield failed(
            "frontmatter.identity",
            f"{len(missing) + len(present)} identity problem(s) in the front matter",
            (
                *(f"missing: {value!r}" for value in missing),
                *(f"should not appear: {value!r}" for value in present),
            ),
        )
        return

    yield passed(
        "frontmatter.identity",
        f"{len(required)} required string(s) present, {len(forbidden)} forbidden string(s) absent",
    )


def _fidelity(context: Context, front: list[Element]) -> Iterator[Finding]:
    source = context.prefix_source
    if source is None:
        yield skipped(
            "frontmatter.fidelity",
            "no front-matter source document was given, so fidelity was not checked",
            ("pass --prefix <cover.docx> to compare against the file the cover came from",),
        )
        return

    if not Path(source).is_file():
        yield failed(
            "frontmatter.fidelity",
            f"the front-matter source {source} does not exist",
        )
        return

    wanted = _source_paragraph_texts(Path(source))
    actual = [text_of(paragraph) for paragraph in front]
    wanted = [text for text in wanted if text.strip()]

    if wanted == actual:
        yield passed(
            "frontmatter.fidelity",
            f"{len(actual)} front-matter paragraph(s) identical to {Path(source).name}",
        )
        return

    detail = _first_differences(wanted, actual)
    yield failed(
        "frontmatter.fidelity",
        f"the front matter differs from {Path(source).name} "
        f"({len(actual)} paragraph(s) here, {len(wanted)} there)",
        detail,
    )


def _source_paragraph_texts(path: Path) -> list[str]:
    """Paragraph texts of a source document, read without modifying it."""
    with Package(path) as package:
        root = package.xml(DOCUMENT_PART)
        body = find(root, "w:body")
        if body is None:
            return []
        return [
            text_of(child) for child in body.iter() if local_name(child.tag) == "p"
        ]


def _first_differences(wanted: list[str], actual: list[str]) -> tuple[str, ...]:
    detail: list[str] = []
    for index in range(max(len(wanted), len(actual))):
        left = wanted[index] if index < len(wanted) else "<missing>"
        right = actual[index] if index < len(actual) else "<missing>"
        if left != right:
            detail.append(f"paragraph {index + 1}: source={_clip(left)!r} document={_clip(right)!r}")
        if len(detail) >= _MAX_REPORTED:
            detail.append("... further differences suppressed")
            break
    if not detail:
        detail.append("the paragraph texts match but the counts differ")
    return tuple(detail)


def _clip(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= 60 else f"{collapsed[:60]}…"
