"""A paragraph-level diff between two revisions of a ``.docx``.

This answers the question an author actually has after a Word round trip: *what
did this edit change?* Comparing the two packages byte for byte answers a
different question -- "did anything change at all" -- and answers it uselessly,
because Word rewrites revision ids, revision-save ids and much of
``settings.xml`` for reasons that have nothing to do with the text.

So the comparison happens at the level a reader thinks in: paragraphs, in
document order, with their style. Two decisions make the result usable.

**Matching is by text, not by position.** Edits land in the middle of a document,
and a positional diff reports every paragraph after an insertion as changed. The
matcher finds the longest matching blocks instead, so one inserted paragraph is
one added paragraph and the rest of the document stays quiet -- the only way a
report stays readable at thesis length.

**A paragraph whose text is unchanged but whose style is not is still a change.**
This project exists because formatting drifts; a diff that only looked at text
would call a body paragraph silently promoted to a heading "unchanged", which is
precisely the regression the toolchain is supposed to catch.

Note what is *not* compared: headers, footers, footnotes and comments live in
other parts of the package and are never opened here. That is a real limitation,
stated rather than hidden -- a header edit changes the file's bytes, and
``ledger.manifest`` is the tool that answers *that* question.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from thesis_refinery.docxlib.pkgs import Package
from thesis_refinery.docxlib.xmlq import Element, attr, find, iter_descendants, text_of

__all__ = [
    "ADDED",
    "CHANGED",
    "DOCUMENT_PART",
    "KINDS",
    "REMOVED",
    "SNIPPET_CHARS",
    "Change",
    "DiffReport",
    "Paragraph",
    "diff",
    "paragraphs",
    "render_markdown",
]

DOCUMENT_PART = "word/document.xml"
"""The part that holds the body.

Named once because the choice of part *is* the header/footer exclusion: anything
not reached from here is not compared.
"""

ADDED = "added"
REMOVED = "removed"
CHANGED = "changed"

KINDS: tuple[str, ...] = (ADDED, REMOVED, CHANGED)
"""Every value :attr:`Change.kind` may take, in report order."""

SNIPPET_CHARS = 200
"""Characters of paragraph text quoted in the markdown report.

Long enough to recognise a paragraph, short enough that a report of a
500-paragraph document is still something a person reads. The documents
themselves are one command away; the report is for deciding where to look.
"""


@dataclass(frozen=True)
class Paragraph:
    """One ``w:p`` of the body, in document order."""

    index: int
    """Position in the body, counting every paragraph, table cells included."""

    text: str
    """What :func:`thesis_refinery.docxlib.xmlq.text_of` finds in the paragraph."""

    style: str | None
    """The ``w:pStyle`` *id*, or ``None`` when the paragraph declares no style.

    ``None`` rather than the name of the document's default style: this function
    reads ``word/document.xml`` and never ``word/styles.xml``, so it does not know
    which style that is, and reporting "Normal" would be a claim about a part it
    never opened. The id is reported rather than the style's display name, for the
    same reason.
    """


def paragraphs(path: str | Path) -> list[Paragraph]:
    """Every body paragraph of *path*, in document order.

    Paragraphs inside tables **are** included, where the table sits in the flow.
    A thesis keeps its captions, its data tables and much of its appendix in
    tables; a diff that walked only top-level paragraphs would report "no
    changes" for a document whose every number had been retyped.

    Paragraphs in headers, footers, footnotes and comments are **not** included,
    because they are not in ``word/document.xml``. That is the honest reading of
    "the paragraph diff": it describes the body, and ``ledger.manifest`` covers
    everything else by bytes.

    A path that is not a readable package raises (``OSError``,
    ``zipfile.BadZipFile``). Returning an empty list for a file that could not be
    opened would report every paragraph of the other document as added, which
    looks like a catastrophe in the document rather than a typo in the command.

    Note that a paragraph inside a text box appears both on its own and inside the
    text of the paragraph that hosts the shape, because :func:`text_of` walks
    every descendant. That matches what the style stages see, and the alternative
    -- special-casing one container -- would make this report disagree with them.
    """
    with Package(path) as package:
        root = package.xml(DOCUMENT_PART)
    return [
        Paragraph(index=index, text=text_of(element), style=_style_of(element))
        for index, element in enumerate(iter_descendants(root, "w:p"))
    ]


def _style_of(paragraph: Element) -> str | None:
    """The ``w:pStyle`` id of *paragraph*, if it declares one."""
    properties = find(paragraph, "w:pPr")
    if properties is None:
        return None
    style = find(properties, "w:pStyle")
    if style is None:
        return None
    return attr(style, "w:val")


@dataclass(frozen=True)
class Change:
    """One paragraph that was added, removed, or replaced.

    Both indices are kept, rather than only the one on the "new" side, because a
    reviewer needs to find the paragraph in *both* documents: the old one is where
    the diff was generated from and the new one is what the author is looking at.
    The absent side of an addition or a removal is ``None``/empty, never a
    plausible-looking index.
    """

    kind: str
    old_index: int | None
    new_index: int | None
    old_text: str
    new_text: str
    old_style: str | None
    new_style: str | None

    def __post_init__(self) -> None:
        """Refuse a kind outside :data:`KINDS`.

        :func:`render_markdown` groups by ``kind``, so an unrecognised value would
        vanish from the report while still being counted in ``counts`` -- a change
        that exists and cannot be seen is worse than a crash.
        """
        if self.kind not in KINDS:
            raise ValueError(
                f"unknown change kind {self.kind!r}; expected one of {', '.join(KINDS)}"
            )


@dataclass(frozen=True)
class DiffReport:
    """Everything :func:`diff` found, in document order."""

    old_path: str
    new_path: str
    changes: tuple[Change, ...]

    @property
    def counts(self) -> dict[str, int]:
        """How many changes of each kind, including the kinds with none.

        Fixed keys, so a caller can print "2 added, 0 removed" without asking
        whether a key exists, and a missing key can never be read as a zero.
        """
        return {kind: sum(1 for change in self.changes if change.kind == kind) for kind in KINDS}


def diff(old_path: str | Path, new_path: str | Path) -> DiffReport:
    """Compare the body paragraphs of two documents.

    Changes come back in document order: an added paragraph is reported where it
    now sits, a removed one where it used to sit, and a changed one at both of its
    indices. Reading the report top to bottom therefore follows the new document,
    which is the one the reviewer has open.

    ``autojunk`` is off, deliberately. difflib's junk heuristic treats any element
    appearing in more than 1% of a sequence of 200 or more as noise, and a thesis
    is full of repeated short paragraphs: blank spacer lines, a repeated table
    header, "continued". With the heuristic on, the matcher declares those regions
    junk and reports them as replaced wholesale, so a one-word edit near the end
    of a long document comes back as a hundred changes.
    """
    old = paragraphs(old_path)
    new = paragraphs(new_path)
    matcher = SequenceMatcher(
        None,
        [paragraph.text for paragraph in old],
        [paragraph.text for paragraph in new],
        autojunk=False,
    )

    changes: list[Change] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            changes.extend(_restyled(old, new, i1, i2, j1))
        elif tag == "insert":
            changes.extend(_added(new[index]) for index in range(j1, j2))
        elif tag == "delete":
            changes.extend(_removed(old[index]) for index in range(i1, i2))
        else:
            changes.extend(_replaced(old, new, i1, i2, j1, j2))

    return DiffReport(old_path=str(old_path), new_path=str(new_path), changes=tuple(changes))


def _restyled(
    old: Sequence[Paragraph],
    new: Sequence[Paragraph],
    i1: int,
    i2: int,
    j1: int,
) -> list[Change]:
    """Pairs that the matcher matched by text, but whose style still differs.

    An equal block has the same length on both sides by construction, so the
    pairing is positional within the block. That is the only place a style-only
    change can be found: the matcher cannot see it, because it compares text.
    """
    found: list[Change] = []
    for offset in range(i2 - i1):
        older = old[i1 + offset]
        newer = new[j1 + offset]
        if older.style != newer.style:
            found.append(_changed(older, newer))
    return found


def _replaced(
    old: Sequence[Paragraph],
    new: Sequence[Paragraph],
    i1: int,
    i2: int,
    j1: int,
    j2: int,
) -> list[Change]:
    """A replaced region, paired index by index, with the leftover reported.

    A replacement block can be lopsided -- three paragraphs rewritten into two --
    and pairing the first two while reporting the third as removed says that more
    clearly than three "changed" entries whose texts have nothing in common.
    """
    found: list[Change] = []
    for offset in range(max(i2 - i1, j2 - j1)):
        if offset < i2 - i1 and offset < j2 - j1:
            found.append(_changed(old[i1 + offset], new[j1 + offset]))
        elif offset < i2 - i1:
            found.append(_removed(old[i1 + offset]))
        else:
            found.append(_added(new[j1 + offset]))
    return found


def _changed(older: Paragraph, newer: Paragraph) -> Change:
    """A paragraph that exists on both sides with different text or style."""
    return Change(
        kind=CHANGED,
        old_index=older.index,
        new_index=newer.index,
        old_text=older.text,
        new_text=newer.text,
        old_style=older.style,
        new_style=newer.style,
    )


def _added(newer: Paragraph) -> Change:
    """A paragraph that exists only in the new document."""
    return Change(
        kind=ADDED,
        old_index=None,
        new_index=newer.index,
        old_text="",
        new_text=newer.text,
        old_style=None,
        new_style=newer.style,
    )


def _removed(older: Paragraph) -> Change:
    """A paragraph that exists only in the old document."""
    return Change(
        kind=REMOVED,
        old_index=older.index,
        new_index=None,
        old_text=older.text,
        new_text="",
        old_style=older.style,
        new_style=None,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def render_markdown(report: DiffReport) -> str:
    """A reviewable markdown report of *report*, grouped by change kind.

    Every kind gets a section even when it is empty, so "nothing was removed" is
    visible as a statement rather than as an absence the reader has to infer from
    a missing heading. Additions and removals are tables -- they are lists of
    paragraphs, and a table is how a list of paragraphs is skimmed. A changed
    paragraph is quoted before and after, because the decision a reviewer makes is
    "is this the edit I asked for", and that decision needs both texts.

    Text is capped at :data:`SNIPPET_CHARS` characters; long paragraphs are
    truncated with a marker rather than dropped, and the documents are one command
    away for the full text. Newlines inside a paragraph (a ``w:br``) are shown as
    ``\\n`` inside tables, which cannot contain a line break, and as a real
    quotation block elsewhere. The text ends with a newline.
    """
    lines = [
        "# Paragraph diff",
        "",
        f"`{report.old_path}` -> `{report.new_path}`",
        "",
        f"**{_summary(report)}**",
        "",
    ]

    for kind, title in ((ADDED, "Added"), (REMOVED, "Removed"), (CHANGED, "Changed")):
        group = [change for change in report.changes if change.kind == kind]
        lines.extend([f"## {title} ({len(group)})", ""])
        if not group:
            lines.extend(["_None._", ""])
        elif kind == CHANGED:
            for change in group:
                lines.extend(_changed_block(change))
        else:
            lines.extend(_table(group, kind))
            lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def _summary(report: DiffReport) -> str:
    """The one-line tally at the top of the report."""
    counts = report.counts
    return f"{counts[ADDED]} added, {counts[REMOVED]} removed, {counts[CHANGED]} changed"


def _table(group: Sequence[Change], kind: str) -> list[str]:
    """One ``| index | style | text |`` table for the added or removed group."""
    added = kind == ADDED
    lines = [
        f"| {'New' if added else 'Old'} | Style | Text |",
        "| ---: | --- | --- |",
    ]
    for change in group:
        index = change.new_index if added else change.old_index
        style = change.new_style if added else change.old_style
        text = change.new_text if added else change.old_text
        lines.append(f"| {index} | {_code(style)} | {_cell(text)} |")
    return lines


def _changed_block(change: Change) -> list[str]:
    """One found paragraph, before and after."""
    lines = [
        f"### Paragraph {change.old_index} -> {change.new_index}",
        "",
        f"- Style: {_code(change.old_style)} -> {_code(change.new_style)}",
        f"- Text: {'unchanged' if change.old_text == change.new_text else 'modified'}",
        "",
        *_quote("Before", change.old_text),
        *_quote("After", change.new_text),
    ]
    return lines


def _quote(label: str, text: str) -> list[str]:
    """*label* and *text* as a markdown quotation block."""
    body = _snippet(text) or "(empty)"
    quoted = [f"> {line}" if line else ">" for line in body.split("\n")]
    return [f"**{label}**", "", *quoted, ""]


def _cell(text: str) -> str:
    """*text* as one table cell: no line breaks, no unescaped pipes."""
    return _snippet(text).replace("\n", "\\n").replace("|", "\\|")


def _code(style: str | None) -> str:
    """A style id as inline code, or a dash when the paragraph has none."""
    return f"`{style}`" if style else "-"


def _snippet(text: str) -> str:
    """*text*, capped at :data:`SNIPPET_CHARS` with a marker where it was cut."""
    if len(text) <= SNIPPET_CHARS:
        return text
    return text[:SNIPPET_CHARS].rstrip() + " ..."
