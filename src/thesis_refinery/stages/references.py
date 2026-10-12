"""Reference entries: the typographic conversion the citation style requires.

Ported from the private pipeline this project was extracted from, where it had been
run against a submitted thesis and where every rule below was found by reading the
*output* rather than the code.

**What this stage owns, and what it does not.** It owns characters: the case of a
surname, ``等`` against ``et al.``, and the italics that mark a journal name. It does
not own fonts or sizes — those are the specification's, applied by
:mod:`thesis_refinery.stages.format`, and a second place that decided them would be
a second specification. Italic is the exception that proves the rule: a journal name
is italic because the citation style says so, not because somebody chose a font.

**The document diverges from the markdown here, on purpose.** The bibliography's
manual numbering in the chapter source is the truth; the entry as printed is a
build-time conversion of it. Any check that compares the two must therefore ignore
the entry lines, and this stage's own closing assertions exist so that a conversion
which *did not happen* cannot pass for one that did:

* an overridden entry may not still contain CJK characters, and
* it must contain the fragments the caller lists for it.

Both are the same failure seen twice — silent non-conversion. A rule that matches
nothing produces exactly the output of a rule that matched correctly, so the only
way to tell them apart is to assert on the result.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from thesis_refinery.config import ConfigError
from thesis_refinery.docxlib.ns import q, qn
from thesis_refinery.docxlib.pkgs import Package
from thesis_refinery.docxlib.xmlq import (
    Element,
    children,
    deepcopy,
    find,
    findall,
    iter_descendants,
    new,
    ordered_insert,
    text_of,
)
from thesis_refinery.style import StyleSpec

__all__ = [
    "DOCUMENT_PART",
    "OptionsError",
    "apply",
    "author_case",
    "italic_spans",
    "override_entry",
    "write_text_over_runs",
]

DOCUMENT_PART = "word/document.xml"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

ENTRY_NUMBER = re.compile(r"^\[(\d+)\]")
CJK = re.compile(
    "[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]"
)
"""CJK punctuation and ideographs, including the fullwidth forms a Chinese
bibliography uses. Used as a *post-condition*: an entry the caller asked to be
replaced by an English record may not still contain any of it."""

_KNOWN_OPTIONS = frozenset(
    {"heading", "et_al", "surname_case", "journal_italic", "overrides", "required_fragments", "expect_entries"}
)
_FONT_ITALIC = ("w:i", "w:iCs")


class OptionsError(ConfigError):
    """Raised when the stage's ``options`` are missing or wrong."""


@dataclass(frozen=True)
class _Options:
    heading: str
    et_al: bool
    surname_case: bool
    journal_italic: bool
    overrides: Mapping[int, str]
    required_fragments: Mapping[int, tuple[str, ...]]
    expect_entries: int


def _read_options(raw: Mapping[str, object] | None) -> _Options:
    given = dict(raw or {})
    unknown = sorted(set(given) - _KNOWN_OPTIONS)
    if unknown:
        raise OptionsError(
            f"unknown option(s) for the references stage: {', '.join(unknown)}.\n"
            f"Known: {', '.join(sorted(_KNOWN_OPTIONS))}"
        )

    overrides: dict[int, str] = {}
    for number, text in _mapping(given.get("overrides", {}), "overrides").items():
        overrides[int(number)] = str(text)

    fragments: dict[int, tuple[str, ...]] = {}
    for number, values in _mapping(
        given.get("required_fragments", {}), "required_fragments"
    ).items():
        if isinstance(values, str) or not isinstance(values, Sequence):
            raise OptionsError(
                f"required_fragments[{number}] must be a list of strings, not {values!r}"
            )
        fragments[int(number)] = tuple(str(value) for value in values)

    missing = sorted(set(overrides) - set(fragments))
    if missing:
        raise OptionsError(
            f"overrides are given for entr(y/ies) {missing} with no required_fragments.\n"
            "A whole-entry replacement with nothing asserted about the result cannot "
            "be told apart from one that silently did not happen."
        )

    expect = given.get("expect_entries", 0)
    if not isinstance(expect, int):
        raise OptionsError("expect_entries must be an integer")

    return _Options(
        heading=str(given.get("heading", "参考文献")),
        et_al=bool(given.get("et_al", True)),
        surname_case=bool(given.get("surname_case", True)),
        journal_italic=bool(given.get("journal_italic", True)),
        overrides=overrides,
        required_fragments=fragments,
        expect_entries=expect,
    )


def _mapping(value: object, name: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise OptionsError(f"stages.references.{name} must be a mapping, not {value!r}")
    return value


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Run:
    element: Element
    start: int
    end: int


def _runs(paragraph: Element) -> list[Element]:
    """Every run in the paragraph, **including the ones inside a hyperlink**.

    The inclusion is the point. A DOI is a hyperlink whose display text is part of
    the entry, and an implementation that skipped hyperlinks would leave the old
    DOI text standing after a whole-entry replacement — which is exactly the shape
    of the bug this port carries a fix for.
    """
    return list(iter_descendants(paragraph, "w:r"))


def _run_text(run: Element) -> str:
    return "".join(node.text or "" for node in findall(run, "w:t"))


def _set_run_text(run: Element, value: str) -> None:
    """Write *value* into the run, keeping the text in its first ``w:t``.

    A run may carry several ``w:t`` elements, and Word does produce them. Writing
    into the first and clearing the rest keeps the run's own properties and its
    position, which rebuilding the run would not.
    """
    nodes = findall(run, "w:t")
    if not nodes:
        if not value:
            return
        nodes = [ordered_insert(run, new("w:t"))]
    nodes[0].set(XML_SPACE, "preserve")
    nodes[0].text = value
    for extra in nodes[1:]:
        extra.text = ""


def _in_hyperlink(run: Element) -> bool:
    """True when the run sits inside a ``w:hyperlink``."""
    parent = run.getparent()
    while parent is not None:
        if parent.tag == q("hyperlink"):
            return True
        parent = parent.getparent()
    return False


def _run_map(paragraph: Element) -> list[_Run]:
    """Every run with the character range of the paragraph it covers."""
    position = 0
    mapped: list[_Run] = []
    for run in _runs(paragraph):
        length = len(_run_text(run))
        mapped.append(_Run(run, position, position + length))
        position += length
    return mapped


def _split_run(run: Element, offset: int) -> None:
    """Split *run* at *offset*, leaving the tail in a copy placed after it."""
    text = _run_text(run)
    tail = deepcopy(run)
    _set_run_text(run, text[:offset])
    _set_run_text(tail, text[offset:])
    run.addnext(tail)


def _split_runs_at(paragraph: Element, offsets: Sequence[int]) -> None:
    """Make every offset a run boundary, so a span can be italicised run by run."""
    for offset in sorted(set(offsets), reverse=True):
        position = 0
        for run in _runs(paragraph):
            text = _run_text(run)
            end = position + len(text)
            if position < offset < end:
                _split_run(run, offset - position)
                break
            position = end


def write_text_over_runs(paragraph: Element, start: int, end: int, value: str) -> None:
    """Replace the paragraph's ``[start, end)`` with *value*, writing run by run.

    Two departures from an ordinary splice, both required by "replace one whole
    entry":

    * **Runs past the range are cleared.** A splice keeps the tail of the last
      covered run; when the replacement is *shorter* than the text it replaces —
      which a Chinese entry becoming an English one always is — the old text after
      the range stays where it was.
    * **Text inside a ``w:hyperlink`` is cleared too.** A whole-entry replacement
      invalidates all of the old text, including a DOI's display text. An earlier
      version skipped hyperlinks entirely and produced
      ``…-00048.10.3760/…-00048`` — the old DOI appended after the new record. The
      hyperlink element and its relationship are left alone, so the link still
      resolves; it simply stops carrying text.
    """
    entries = _run_map(paragraph)
    covered = [entry for entry in entries if entry.end > start and entry.start < end]
    if not covered:
        raise ValueError(f"no run covers [{start}, {end}) of {text_of(paragraph)!r}")

    first = covered[0]
    _set_run_text(first.element, _run_text(first.element)[: start - first.start] + value)
    for entry in covered[1:]:
        _set_run_text(entry.element, "")
    for entry in entries:
        if entry.start >= end and _run_text(entry.element):
            _set_run_text(entry.element, "")


# ---------------------------------------------------------------------------
# The three conversions
# ---------------------------------------------------------------------------


def author_case(text: str) -> str:
    """All-caps surnames become initial-capital, one comma-separated part at a time.

    ``isalpha`` gates the change, so a non-Latin surname is left exactly as the
    author wrote it. A version that keyed on "upper == value" alone would rewrite a
    name it cannot case, and the result would be a corrupted author rather than a
    reformatted one.

    Hyphenated surnames are capitalised segment by segment, because the rule is
    about the first letter of each name and not the first letter of the string:
    ``SALGADO-POLO`` is ``Salgado-Polo``.
    """
    parts = []
    for part in text.split(","):
        words = part.strip().split()
        if words:
            prefix = ""
            if words[0].startswith("[") and len(words) > 1:
                prefix, words = words[0], words[1:]
            for position, word in enumerate(words):
                if len(word) > 1 and word.upper() == word and word.replace("-", "").isalpha():
                    words[position] = "-".join(
                        (segment[0] + segment[1:].lower()) if segment else segment
                        for segment in word.split("-")
                    )
            parts.append(" ".join(([prefix] if prefix else []) + words).strip())
        else:
            parts.append("")
    return ", ".join(parts)


def italic_spans(text: str) -> list[tuple[int, int]]:
    """The character spans that belong in italics: the journal, and every ``et al.``"""
    spans: list[tuple[int, int]] = []
    journal = re.search(r"\.\s*([^.,]+),\s*\d{4}\s*,", text)
    if journal:
        spans.append((journal.start(1), journal.end(1)))
    spans.extend((match.start(), match.end()) for match in re.finditer(r"et al\.", text))
    return spans


def _apply_italic(paragraph: Element, spans: Sequence[tuple[int, int]]) -> None:
    """Italicise the spans by splitting runs, never by rebuilding them."""
    if not spans:
        return
    for start, end in spans:
        _split_runs_at(paragraph, (start, end))

    for entry in _run_map(paragraph):
        if _in_hyperlink(entry.element):
            continue
        italic = any(start <= entry.start and entry.end <= end for start, end in spans)
        run_properties = find(entry.element, "w:rPr")
        if run_properties is None:
            if not italic:
                continue
            run_properties = ordered_insert(entry.element, new("w:rPr"))
        for tag in _FONT_ITALIC:
            existing = find(run_properties, tag)
            if italic and existing is None:
                ordered_insert(run_properties, new(tag))
            elif not italic and existing is not None:
                run_properties.remove(existing)


def _apply_author_case(paragraph: Element) -> None:
    """Initial-capital the author field, run by run.

    The replacement is computed over the whole author segment — so the comma
    spacing is normalised exactly once — and then written back **per run**, each run
    receiving only the characters of its own range. Splicing the whole segment
    instead would push the first characters of the italic ``et al.`` run into the
    run before it and destroy the italic span.
    """
    full = text_of(paragraph)
    match = re.match(r"(\[\d+\]\s*)(.*?)(\.\s+)", full)
    if not match:
        return

    author_end = match.end(2)
    replacement = match.group(1) + author_case(match.group(2))
    if replacement == full[:author_end]:
        return

    if len(replacement) == len(full[:author_end]):
        for entry in _run_map(paragraph):
            if entry.start >= author_end:
                break
            cut = min(entry.end, author_end) - entry.start
            text = _run_text(entry.element)
            _set_run_text(entry.element, replacement[entry.start : entry.start + cut] + text[cut:])
        return

    write_text_over_runs(paragraph, 0, author_end, replacement)


def override_entry(
    paragraph: Element,
    *,
    overrides: Mapping[int, str],
    required_fragments: Mapping[int, tuple[str, ...]],
) -> bool:
    """Replace a whole entry by its number, then assert the replacement happened.

    The assertions are the reason this function is worth having. A rule that matches
    nothing produces exactly the output of a rule that matched correctly, so the
    only evidence that the conversion ran is what the result contains: no CJK left
    in an entry that was replaced by an English record, and every fragment the
    caller listed present in it.
    """
    text = text_of(paragraph)
    match = ENTRY_NUMBER.match(text)
    if not match:
        return False
    number = int(match.group(1))
    if number not in overrides:
        return False

    replacement = f"[{number}] {overrides[number]}"
    if text == replacement:
        return False

    write_text_over_runs(paragraph, 0, len(text), replacement)
    after = text_of(paragraph)

    if CJK.search(after):
        raise OptionsError(
            f"reference [{number}] still contains CJK characters after being "
            f"replaced, so the conversion is incomplete: {after[:160]}"
        )

    missing = [fragment for fragment in required_fragments[number] if fragment not in after]
    if missing:
        raise OptionsError(
            f"reference [{number}] is missing the required fragment(s) {missing} after "
            f"being replaced:\n  {after[:160]}"
        )
    return True


def convert_paragraph(paragraph: Element, options: _Options) -> bool:
    """Apply every configured conversion to one reference entry."""
    before = text_of(paragraph)

    if options.et_al:
        for run in _runs(paragraph):
            text = _run_text(run)
            if "等" in text:
                _set_run_text(run, text.replace("等", "et al."))
        for run in _runs(paragraph):
            text = _run_text(run)
            if "et al.." in text:
                _set_run_text(run, text.replace("et al..", "et al."))
        runs = _runs(paragraph)
        for position, run in enumerate(runs):
            if (
                position
                and _run_text(run) == "."
                and _run_text(runs[position - 1]).endswith("et al.")
            ):
                _set_run_text(run, "")

    if options.journal_italic:
        _apply_italic(paragraph, italic_spans(text_of(paragraph)))

    if options.surname_case:
        _apply_author_case(paragraph)

    # The overrides go last, or a fully-cased surname would be pushed back to
    # initial capitals by the rule above.
    override_entry(
        paragraph,
        overrides=options.overrides,
        required_fragments=options.required_fragments,
    )

    return text_of(paragraph) != before


# ---------------------------------------------------------------------------
# Finding the bibliography
# ---------------------------------------------------------------------------


def _style_names(package: Package) -> dict[str, str]:
    """Style id → name, read from the document's styles part.

    The same reader :mod:`thesis_refinery.stages.format` carries. Both stages need
    to answer "what style is this paragraph", neither may import
    :mod:`thesis_refinery.verify` (a shared walk between applier and checker cancels
    its own bug out), and consolidating the two into ``docxlib`` is the follow-up
    this comment is here to record rather than to hide.
    """
    if not package.exists("word/styles.xml"):
        return {}
    styles = package.xml("word/styles.xml")
    names: dict[str, str] = {}
    for style in iter_descendants(styles, "w:style"):
        identifier = style.get(qn("w:styleId"))
        name = find(style, "w:name")
        if identifier and name is not None:
            names[identifier] = name.get(qn("w:val")) or ""
    return names


def _paragraph_style(paragraph: Element, names: Mapping[str, str]) -> tuple[str, str]:
    """The paragraph's ``(style id, style name)``, either of which may be empty.

    Both, because they are not the same string and different things are configured
    against different ones: a heading is matched by *name* (the specification lists
    ``Heading 1`` and ``标题 1``), while a caption is matched by *id*, because
    pandoc writes ``ImageCaption`` where Word's own name is ``Image Caption``.
    """
    properties = find(paragraph, "w:pPr")
    if properties is None:
        return ("", "")
    style = find(properties, "w:pStyle")
    if style is None:
        return ("", "")
    identifier = style.get(qn("w:val")) or ""
    return (identifier, names.get(identifier, ""))


def _is_caption(style: StyleSpec, identifier: str, name: str) -> bool:
    wanted = {
        candidate.casefold()
        for candidate in (*style.caption.image_style_ids, *style.caption.table_style_ids)
    }
    return identifier.casefold() in wanted or name.casefold() in wanted


def reference_entries(
    body: Element, *, style: StyleSpec, names: Mapping[str, str], heading: str
) -> list[Element]:
    """The paragraphs of the bibliography, from its heading to the first thing after it.

    Three ways out, all of them needed:

    * a **level-1 heading** starts something that is not the bibliography;
    * a **caption** belongs to a figure or a table, and a bibliography that ran on
      into one would have the caption rewritten as an entry;
    * the end of the body.

    A scan with none of these ends at the last paragraph of the document, which is a
    mistake that looks like thoroughness.
    """
    paragraphs = [child for child in children(body) if child.tag == q("p")]
    start = None
    for position, paragraph in enumerate(paragraphs):
        if text_of(paragraph).strip() == heading.strip():
            start = position
            break
    if start is None:
        raise OptionsError(
            f"no paragraph reads {heading!r}, so the bibliography was not found.\n"
            "Set stages.references.heading to the heading your document uses."
        )

    entries: list[Element] = []
    for paragraph in paragraphs[start + 1 :]:
        identifier, name = _paragraph_style(paragraph, names)
        if name and style.heading_style_matches(name, 1):
            break
        if _is_caption(style, identifier, name):
            break
        if text_of(paragraph).strip():
            entries.append(paragraph)
    return entries


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------


def apply(
    source: Path,
    destination: Path,
    *,
    style: StyleSpec,
    options: Mapping[str, object] | None = None,
) -> Path:
    """Read *source*, convert its bibliography entries, write *destination*."""
    settings = _read_options(options)
    source = Path(source)
    destination = Path(destination)

    with Package(source) as package:
        document = package.xml(DOCUMENT_PART)
        before = etree.tostring(document)

        body = find(document, "w:body")
        if body is None:
            raise OptionsError(f"{source} has no w:body in {DOCUMENT_PART}")

        names = _style_names(package)
        entries = reference_entries(body, style=style, names=names, heading=settings.heading)

        if not entries:
            raise OptionsError(
                f"the bibliography under {settings.heading!r} has no entries; nothing "
                "to convert, which is indistinguishable from a conversion that failed"
            )

        if settings.expect_entries and len(entries) < settings.expect_entries:
            raise OptionsError(
                f"the bibliography has {len(entries)} entr(y/ies) and the configuration "
                f"expects at least {settings.expect_entries}.\n"
                "A scan that stopped early takes the rest of the document with it, so "
                "the count is checked before anything is rewritten."
            )

        for entry in entries:
            convert_paragraph(entry, settings)

        if etree.tostring(document) != before:
            package.replace_xml(DOCUMENT_PART, document)
        return package.save(destination)
