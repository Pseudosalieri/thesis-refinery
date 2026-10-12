"""Tests for the bundled example project.

The example is the first thing a new user copies, so what it needs is not "does it
look right" but "is it still internally consistent". Every way it can rot silently
has a test here:

* a chapter is added to ``index.qmd`` and the file is forgotten (or the reverse: a
  file is written and never included, so it renders nowhere);
* a hand-numbered bibliography drifts — a citation to ``[7]`` with no entry, or an
  entry nobody cites;
* the template the renderer reads stops defining a style the chapters ask pandoc
  for, and a heading silently renders in Normal;
* one of the two generated assets is hand-edited, or stops being reproducible.

Nothing here renders anything. Quarto is what the render needs, the render is a
build step rather than a test, and the stages that polish its output are tested
where they live. The prose is not proofread here either: these tests are about
consistency, not about whether the demo reads well.
"""

from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
import unicodedata
import zlib
from pathlib import Path
from types import ModuleType

import pytest

from thesis_refinery.config import AUTO, load
from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.pkgs import Package
from thesis_refinery.docxlib.xmlq import (
    SCHEMA_ORDER,
    Element,
    find,
    iter_descendants,
    local_name,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO = REPO_ROOT / "examples" / "demo-thesis"
MANUSCRIPT = DEMO / "manuscript"
CHAPTERS = MANUSCRIPT / "chapters"
INDEX = MANUSCRIPT / "index.qmd"
REFERENCE_DOCX = MANUSCRIPT / "reference.docx"
PLACEHOLDER_PNG = CHAPTERS / "raw" / "media" / "placeholder.png"

TOOLS = REPO_ROOT / "tools"
REFERENCE_GENERATOR = TOOLS / "make_reference_docx.py"
IMAGE_GENERATOR = TOOLS / "make_placeholder_image.py"

DOCUMENT_PART = "word/document.xml"
STYLES_PART = "word/styles.xml"

REFERENCES = CHAPTERS / "07-references.qmd"
"""The bibliography is an ordinary chapter file, so its name is a fact about the demo."""

RESULTS = CHAPTERS / "05-results.qmd"
ABBREVIATIONS = CHAPTERS / "08-abbreviations.qmd"

ABBREVIATION_COLUMNS = ("缩略语", "英文全称", "中文全称")
"""The header the abbreviation stage matches on.

Pinned here because it is a contract between two files that do not import each
other: the stage reads this row out of the rendered table, and the chapter writes
it. If the stage settles on different columns, this constant and
:data:`ABBREVIATIONS` change together — which is the point of pinning it rather
than letting the two drift.
"""

_INCLUDE = re.compile(r"\{\{<\s*include\s+(?P<target>[^\s>]+)\s*>\}\}")
_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>\S.*)$", re.MULTILINE)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s", re.MULTILINE)
_TABLE_RULE = re.compile(r"^\|?(?:\s*:?-{2,}:?\s*\|)+\s*$")
_CAPTION_LINE = re.compile(r"^:\s*\S", re.MULTILINE)
_FIGURE = re.compile(r"!\[(?P<caption>[^\]]*)\]\((?P<target>[^)]+)\)")
_CITATION = re.compile(r"\[(\d+)\]")
_ENTRY = re.compile(r"^\[(\d+)\]\s+(?P<text>\S.*)$", re.MULTILINE)
_UNDERSCORE_FORMULA = re.compile(r"[0-9A-Za-z)\]]_[0-9A-Za-z({\[]")
_SUBSCRIPT = re.compile(r"~[^~\s]+~")
_SUPERSCRIPT = re.compile(r"\^[^^\s]+\^")


# ---------------------------------------------------------------------------
# Reading the demo
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _chapter_files() -> list[Path]:
    """Every chapter source on disk, in file-name order."""
    return sorted(CHAPTERS.glob("*.qmd"))


def _included_chapters() -> list[Path]:
    """The chapter files ``index.qmd`` includes, resolved against the manuscript dir.

    Parsed rather than listed, so that a chapter added to the index without being
    written is a failure here instead of a broken render for whoever runs the demo
    next.
    """
    return [
        MANUSCRIPT / match.group("target")
        for match in _INCLUDE.finditer(_read(INDEX))
    ]


def _cited_numbers() -> dict[int, set[str]]:
    """Cited reference numbers → the chapters that cite them.

    :data:`REFERENCES` is excluded. Its ``[n]`` at the start of a line *is* the
    entry, and counting entries as citations would make "every entry is cited" true
    by construction.
    """
    found: dict[int, set[str]] = {}
    for path in _chapter_files():
        if path == REFERENCES:
            continue
        for number in _CITATION.findall(_read(path)):
            found.setdefault(int(number), set()).add(path.name)
    return found


def _declared_entries() -> dict[int, str]:
    """Reference numbers declared in the bibliography → the entry text."""
    return {
        int(match.group(1)): match.group("text")
        for match in _ENTRY.finditer(_read(REFERENCES))
    }


def _pipe_tables(text: str) -> list[list[str]]:
    """Every pandoc pipe table in *text*, as blocks of raw lines.

    A pipe table is a header row, a rule of dashes, and body rows; the rule is what
    distinguishes a table from prose that happens to contain a vertical bar.
    """
    tables: list[list[str]] = []
    block: list[str] = []

    def flush() -> None:
        if len(block) >= 2 and _TABLE_RULE.match(block[1].strip()):
            tables.append(list(block))

    for line in text.splitlines():
        if "|" in line and line.strip():
            block.append(line)
            continue
        flush()
        block = []
    flush()
    return tables


def _columns(row: str) -> list[str]:
    """The cells of one pipe-table row, trimmed."""
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _is_prose(line: str) -> bool:
    """True for a line that is paragraph text rather than another kind of block."""
    if not line.strip():
        return False
    if _HEADING.match(line) or _LIST_ITEM.match(line):
        return False
    if line.startswith(("|", ":", "!", "    ", "\t")):
        return False
    return not _TABLE_RULE.match(line.strip())


def _paragraph_follows_a_heading(text: str) -> bool:
    """True when prose comes directly after a heading, which pandoc styles differently."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not _HEADING.match(line):
            continue
        for following in lines[index + 1 :]:
            if following.strip():
                if _is_prose(following):
                    return True
                break
    return False


def _styles_the_chapters_need() -> set[str]:
    """Names of the styles pandoc will look for in ``reference.docx``.

    Derived from the sources rather than listed, which is the whole point: adding a
    construct to a chapter — a fourth heading level, a block quote, a code block —
    should ask the template for the style that construct needs, instead of rendering
    in Normal and being noticed by a reader.
    """
    needed: set[str] = set()
    for path in _chapter_files():
        text = _read(path)

        needed.update(f"Heading {len(m.group('hashes'))}" for m in _HEADING.finditer(text))
        if _pipe_tables(text) or _CAPTION_LINE.search(text):
            needed.add("Table Caption")
        if _LIST_ITEM.search(text):
            needed.add("Compact")
        if _FIGURE.search(text):
            needed.update(("Figure", "Captioned Figure", "Image Caption"))
        if any(_is_prose(line) for line in text.splitlines()):
            needed.add("Body Text")
        if _paragraph_follows_a_heading(text):
            needed.add("First Paragraph")

    return needed


def _style_names(path: Path) -> set[str]:
    """Display names of every ``w:style`` in ``path``."""
    with Package(path) as package:
        root = package.xml(STYLES_PART)
    names: set[str] = set()
    for style in iter_descendants(root, "w:style"):
        element = find(style, "w:name")
        names.add(element.get(q("val")) if element is not None else style.get(q("styleId"), ""))
    return names


def _parts_of(path: Path) -> dict[str, bytes]:
    """Every part of a package, decompressed."""
    with Package(path) as package:
        return {name: package.read(name) for name in package.names()}


def _order_violations(element: Element) -> list[str]:
    """Children of *element* that appear out of the order its schema declares.

    The order comes from :data:`~thesis_refinery.docxlib.xmlq.SCHEMA_ORDER`, the same
    table ``ordered_insert`` uses, so this test cannot disagree with the builder about
    what "in order" means.
    """
    order = SCHEMA_ORDER.get(local_name(element.tag))
    if order is None:
        return []

    position: dict[str, int] = {}
    for index, entry in enumerate(order):
        for name in (entry,) if isinstance(entry, str) else entry:
            position[name] = index

    violations: list[str] = []
    highest = -1
    for child in element:
        index = position.get(local_name(child.tag))
        if index is None:
            continue
        if index < highest:
            violations.append(f"{local_name(child.tag)} appears after a later sibling")
        highest = max(highest, index)
    return violations


def _decode_png(path: Path) -> tuple[tuple[int, int], bytes]:
    """Image size and decompressed image data of a non-interlaced PNG.

    Decoded rather than compared byte for byte: two zlib builds may compress the same
    pixels differently, and a test that fails on a different zlib release would be
    reporting the wrong thing.
    """
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n"), f"{path} is not a PNG"

    size: tuple[int, int] | None = None
    compressed = bytearray()
    offset = 8
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        if kind == b"IHDR":
            size = (int.from_bytes(payload[0:4], "big"), int.from_bytes(payload[4:8], "big"))
        elif kind == b"IDAT":
            compressed.extend(payload)
        offset += 12 + length

    assert size is not None, f"{path} has no IHDR chunk"
    assert compressed, f"{path} has no IDAT chunk"
    return size, zlib.decompress(bytes(compressed))


def _generator(module_name: str) -> ModuleType:
    """Import one of the ``tools/`` generators.

    Imported rather than shelled out to, because most of what these tests check is
    the artifact the generator *would* write, which must not depend on the working
    tree being writable.
    """
    return importlib.import_module(module_name)


def _run_tool(tool: Path) -> subprocess.CompletedProcess[str]:
    """Run a generator the way the documentation says to: no arguments."""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        path
        for path in (str(REPO_ROOT / "src"), str(TOOLS), env.get("PYTHONPATH", ""))
        if path
    )
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(tool)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


GENERATORS = (
    pytest.param(REFERENCE_GENERATOR, REFERENCE_DOCX, id="reference-docx"),
    pytest.param(IMAGE_GENERATOR, PLACEHOLDER_PNG, id="placeholder-image"),
)


# ---------------------------------------------------------------------------
# The chapter sources
# ---------------------------------------------------------------------------


def test_index_includes_resolve_to_existing_chapter_files() -> None:
    included = _included_chapters()

    assert included, f"{INDEX.name} declares no chapter includes; the parse found none"
    assert [path for path in included if not path.is_file()] == []


def test_no_chapter_file_is_missing_from_index() -> None:
    """A chapter nobody includes renders nowhere, which is easy to not notice."""
    on_disk = {path.resolve() for path in _chapter_files()}
    included = {path.resolve() for path in _included_chapters()}

    assert on_disk, f"no .qmd sources under {CHAPTERS}"
    assert sorted(path.name for path in on_disk - included) == []


def test_thesis_yml_loads_and_resolves_the_demo_paths() -> None:
    loaded = load(DEMO)

    assert loaded.paths.chapters_dir == CHAPTERS
    assert loaded.reference_docx == REFERENCE_DOCX
    assert loaded.paths.chapters_dir.is_dir()
    assert loaded.reference_docx is not None and loaded.reference_docx.is_file()
    # Counts stay derived from the document; a pinned count here would rot with the
    # prose, which is exactly what `auto` exists to prevent.
    assert loaded.checks.expect_captions == AUTO


def test_every_citation_resolves_to_a_reference_entry() -> None:
    entries = _declared_entries()
    cited = _cited_numbers()

    assert cited, "no citation of the form [n] found in any chapter"
    dangling = {number: sorted(files) for number, files in cited.items() if number not in entries}
    assert dangling == {}


def test_every_reference_entry_is_cited() -> None:
    entries = _declared_entries()
    cited = set(_cited_numbers())

    assert entries, f"{REFERENCES.name} declares no numbered entries"
    assert sorted(set(entries) - cited) == []


def test_reference_entries_are_numbered_from_one_without_gaps() -> None:
    """Hand-numbering breaks by a gap or a duplicate, not by a wrong style."""
    numbers = sorted(_declared_entries())

    assert numbers == list(range(1, len(numbers) + 1))


def test_each_chapter_is_one_fragment_with_headings_no_deeper_than_three() -> None:
    """``#`` is a chapter title, ``##`` and ``###`` are sections, and that is all.

    A second ``#`` splits the chapter in two in the rendered document, and a fourth
    level has no style in the template and no entry in the table of contents.
    """
    titles: dict[str, int] = {}
    too_deep: list[str] = []

    for path in _chapter_files():
        levels = [len(match.group("hashes")) for match in _HEADING.finditer(_read(path))]
        titles[path.name] = levels.count(1)
        too_deep.extend(f"{path.name}: {'#' * level}" for level in levels if level > 3)

    assert titles, "no chapter sources found"
    assert {name: count for name, count in titles.items() if count != 1} == {}
    assert too_deep == []


def test_chapters_use_no_unicode_subscript_or_superscript_characters() -> None:
    """``H₂O`` looks right and is the wrong input; ``H~2~O`` is the right one.

    Computed from the Unicode name of each character rather than from a hand-written
    list of code points, because the list is long, script-dependent, and incomplete
    the moment somebody adds one more superscript letter.
    """
    files = _chapter_files()
    offenders = [
        f"{path.name}: U+{ord(char):04X} {char!r} ({name})"
        for path in files
        for char in _read(path)
        if (name := unicodedata.name(char, "")).startswith(("SUBSCRIPT", "SUPERSCRIPT"))
    ]

    assert files, "no chapter sources found"
    assert offenders == []


def test_chapters_use_no_underscore_formula_notation() -> None:
    """``S_max`` renders as literal text; the project's notation is ``S~max~``.

    Only a token that looks like a formula is reported — a letter or digit, an
    underscore, then another word character — so markdown emphasis (``_word_``) is
    not dragged in.
    """
    files = _chapter_files()
    offenders = [
        f"{path.name}: {match.group(0)!r}"
        for path in files
        for match in _UNDERSCORE_FORMULA.finditer(_read(path))
    ]

    assert files, "no chapter sources found"
    assert offenders == []


def test_chapters_do_use_the_tilde_and_caret_notation() -> None:
    """The rule above is only meaningful while the notation it mandates is in use.

    Without this, deleting every subscript from the demo would make the underscore
    check pass more easily, which is the wrong direction for a rule to reward.
    """
    text = "\n".join(_read(path) for path in _chapter_files())

    assert _SUBSCRIPT.search(text), "no ~subscript~ anywhere in the chapters"
    assert _SUPERSCRIPT.search(text), "no ^superscript^ anywhere in the chapters"


def test_results_chapter_has_a_pipe_table() -> None:
    tables = _pipe_tables(_read(RESULTS))

    assert tables, f"{RESULTS.name} has no pandoc pipe table to turn into a three-line table"
    header = _columns(tables[0][0])
    assert len(header) >= 2, f"the first table has {len(header)} column(s): {header}"
    assert all(cell for cell in header), f"a header cell is empty: {header}"
    assert len(tables[0]) > 2, "the first table has a header but no body rows"


def test_results_chapter_has_a_captioned_figure_pointing_at_a_generated_image() -> None:
    text = _read(RESULTS)
    figure = _FIGURE.search(text)

    assert figure is not None, f"{RESULTS.name} has no figure"
    caption = figure.group("caption").strip()
    prefixes = load(DEMO).style.caption.image_prefixes
    assert caption.startswith(prefixes), (
        f"figure caption {caption!r} starts with none of the configured prefixes {prefixes}"
    )

    target = CHAPTERS / figure.group("target")
    assert target.is_file(), f"the figure points at {figure.group('target')}, which does not exist"
    assert target.resolve() == PLACEHOLDER_PNG.resolve()


def test_table_captions_carry_a_configured_prefix() -> None:
    """A caption line is the ``:`` paragraph pandoc attaches to the table above it."""
    prefixes = load(DEMO).style.caption.table_prefixes
    captions = [
        line.lstrip(":").strip()
        for path in _chapter_files()
        for line in _read(path).splitlines()
        if _CAPTION_LINE.match(line)
    ]

    assert captions, "no table caption lines found in any chapter"
    assert [caption for caption in captions if not caption.startswith(prefixes)] == []


def test_abbreviations_table_has_the_header_the_stage_expects() -> None:
    tables = _pipe_tables(_read(ABBREVIATIONS))

    assert tables, f"{ABBREVIATIONS.name} has no pipe table"
    assert tuple(_columns(tables[0][0])) == ABBREVIATION_COLUMNS
    assert len(tables[0]) > 2, "the abbreviation table has a header but no rows"


# ---------------------------------------------------------------------------
# The generated template
# ---------------------------------------------------------------------------


def test_reference_docx_is_a_readable_package() -> None:
    assert REFERENCE_DOCX.is_file(), (
        f"{REFERENCE_DOCX} is missing; run `python tools/make_reference_docx.py`"
    )

    with Package(REFERENCE_DOCX) as package:
        assert DOCUMENT_PART in package
        assert STYLES_PART in package
        styles_root = package.xml(STYLES_PART)

    normal = [
        style
        for style in iter_descendants(styles_root, "w:style")
        if (name := find(style, "w:name")) is not None and name.get(q("val")) == "Normal"
    ]
    assert len(normal) == 1, "expected exactly one Normal style"
    assert normal[0].get(q("default")) == "1", "Normal is not declared as the default style"


def test_reference_docx_defines_every_style_the_chapters_use() -> None:
    needed = _styles_the_chapters_need()

    assert needed, "no styles derived from the chapters; the derivation is broken"
    defined = {name.replace(" ", "").lower() for name in _style_names(REFERENCE_DOCX)}
    missing = sorted(name for name in needed if name.replace(" ", "").lower() not in defined)
    assert missing == [], (
        f"{REFERENCE_DOCX.name} defines no {missing}. Rendering the demo would fall back to "
        f"Normal for whatever asked for them; add the style to tools/make_reference_docx.py "
        f"and regenerate."
    )


def test_reference_docx_style_properties_are_in_schema_order() -> None:
    """``w:pPr`` and ``w:rPr`` are sequence types, and Word says 'corrupt' when they are not.

    The trap is specific: ``synth.style_xml`` writes an outline level *first* when it
    is handed one, while CT_PPr puts ``w:outlineLvl`` after ``w:spacing`` and ``w:jc``.
    A heading that carries both is written in an order Word rejects, and nothing else
    in the suite would notice.
    """
    offenders: list[str] = []
    with Package(REFERENCE_DOCX) as package:
        root = package.xml(STYLES_PART)
        for style in iter_descendants(root, "w:style"):
            for part in ("w:pPr", "w:rPr"):
                element = find(style, part)
                if element is None:
                    continue
                offenders.extend(
                    f"{style.get(q('styleId'))}/{part}: {violation}"
                    for violation in _order_violations(element)
                )

    assert offenders == []


def test_reference_docx_parts_match_the_generator(tmp_path: Path) -> None:
    """The checked-in template is what the generator produces, not a later hand edit.

    Compared part by part after decompression, so a different zlib release is not a
    failure: what must match is the XML inside, which is what the styles are.
    """
    generated = _generator("make_reference_docx").build(tmp_path / "reference.docx")

    assert _parts_of(generated) == _parts_of(REFERENCE_DOCX), (
        "reference.docx differs from its generator; run "
        "`python tools/make_reference_docx.py` and commit the result"
    )


def test_placeholder_png_matches_the_generator(tmp_path: Path) -> None:
    generated = _generator("make_placeholder_image").build(tmp_path / "placeholder.png")

    assert _decode_png(PLACEHOLDER_PNG) == _decode_png(generated), (
        "placeholder.png differs from the image its generator draws; run "
        "`python tools/make_placeholder_image.py` and commit the result"
    )


# ---------------------------------------------------------------------------
# The generators, run the way the README says to run them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("tool", "artifact"), GENERATORS)
def test_generator_writes_its_artifact_and_says_so(tool: Path, artifact: Path) -> None:
    result = _run_tool(tool)

    assert result.returncode == 0, f"{tool.name} failed:\n{result.stdout}\n{result.stderr}"
    assert artifact.is_file()
    assert artifact.stat().st_size > 0
    assert str(artifact) in result.stdout, f"{tool.name} did not report the file it wrote"


@pytest.mark.parametrize(("tool", "artifact"), GENERATORS)
def test_generator_is_idempotent(tool: Path, artifact: Path) -> None:
    """A generator whose output moves when nothing changed cannot be diffed or asserted on."""
    first_run = _run_tool(tool)
    assert first_run.returncode == 0, first_run.stderr
    first = artifact.read_bytes()

    second_run = _run_tool(tool)
    assert second_run.returncode == 0, second_run.stderr
    assert artifact.read_bytes() == first, f"{tool.name} rewrote {artifact.name} differently"
