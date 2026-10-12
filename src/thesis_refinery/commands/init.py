"""``thesis-refinery init``: scaffold an empty project.

Deliberately *not* a copy of the bundled example. A new project inheriting the
example's synthetic content, its fake references and its placeholder tables would
have to be cleaned out before any real work started, and the cleaning is exactly
the kind of task that gets half done. What gets written here is the smallest set
of files that renders, with the places you are meant to edit marked.

Four files, and each exists for a reason the file itself states:

* ``thesis.yml`` — the configuration, with the three sections that need real
  values called out.
* ``manuscript/_quarto.yml`` — the render configuration, including the mirror of
  the style-template path that the build then asserts against ``thesis.yml``.
* ``manuscript/index.qmd`` — the include list. Named files, not a wildcard, so
  that adding a chapter is a deliberate edit.
* ``manuscript/chapters/*.qmd`` — one skeleton chapter, because a project whose
  first build succeeds is a project you keep using.

The renderer's ``reference.docx`` is *not* written. It is the one input that has
to come from your institution, and a placeholder that renders wrongly is worse
than a missing file that says so.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from thesis_refinery.docxlib.save import atomic_write_bytes
from thesis_refinery.exitcodes import ExitCode

__all__ = ["add_arguments", "run", "templates"]

DEFAULT_DESTINATION = "my-thesis"

THESIS_YML = """\
# Configuration for one thesis.
#
# Three sections need real values before this is useful. They are marked below.
# Everything else has a neutral default, and `thesis-refinery audit` will tell
# you what the document actually contains rather than what you hoped.

project:
  thesis_dir: manuscript
  chapters_dir: manuscript/chapters
  output_dir: manuscript/_outputs

# NEEDS YOUR VALUES: the strings your cover must contain, and must not.
#
# Left empty, the identity check SKIPS rather than passing. That is deliberate:
# a checker with a built-in default identity verifies whatever the default
# describes, which is how you end up confirming that the wrong document has the
# right cover.
identity:
  required: []
  forbidden: []

style:
  page:
    width_mm: 210
    height_mm: 297
  margins:
    top_mm: 25
    bottom_mm: 25
    body_side_mm: 20
    front_matter_side_mm: 25
    header_distance_mm: 12.7
    footer_distance_mm: 12.7
  body:
    first_line_indent_twips: 480
    line_spacing_twips: 360
  header_footer:
    # NEEDS YOUR VALUE. Every institution specifies its own wording, and printing
    # the wrong running head on every page of a submitted thesis is worse than
    # printing none, so there is no useful default here.
    odd_page_text: "学位论文"
    footer_size_pt: 9

# `auto` derives each expectation from the document under test. Pin a number only
# when you need to pin it: a hard-coded count rots silently, and you find out
# when it has already been wrong for a month.
checks:
  expect_captions: auto
  expect_body_paragraphs: auto
  expect_reference_entries: auto
  expect_sections: auto
  expect_drawings: auto

profiles: []

# NEEDS YOUR VALUE once you have a style template. The build asserts that this
# path and _quarto.yml's `reference-doc` resolve to the same file.
# reference_docx: manuscript/reference.docx
"""

QUARTO_YML = """\
project:
  type: default
  output-dir: _outputs
  render:
    - index.qmd

format:
  docx:
    toc: true
    toc-title: 目录
    number-sections: false
    toc-depth: 3
    # The single source of truth for this value is thesis.yml's reference_docx.
    # Quarto cannot read that file, so this is a mirror, and `build` refuses to
    # run when the two disagree rather than documenting the duplication.
    #
    # Uncomment both once you have a reference.docx from your institution:
    # reference-doc: reference.docx
"""

INDEX_QMD = """\
---
title: "论文题目"
---

{{< include chapters/01-introduction.qmd >}}

{{< include chapters/02-conclusion.qmd >}}
"""

CHAPTER_QMD = """\
# {title}

{body}
"""


def templates(title: str = "论文题目") -> dict[str, str]:
    """The files ``init`` writes, keyed by path relative to the project root."""
    return {
        "thesis.yml": THESIS_YML,
        "manuscript/_quarto.yml": QUARTO_YML,
        "manuscript/index.qmd": INDEX_QMD.replace("论文题目", title),
        "manuscript/chapters/01-introduction.qmd": CHAPTER_QMD.format(
            title="引言",
            body=(
                "在这里写引言。\n\n"
                "内容改动只改 markdown；版式改动只改 `thesis.yml` 的 `style`。\n"
                "两者都不要改 `.docx`：它是构建产物。\n"
            ),
        ),
        "manuscript/chapters/02-conclusion.qmd": CHAPTER_QMD.format(
            title="结论",
            body="在这里写结论。\n",
        ),
    }


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "dest", nargs="?", metavar="DEST", default=DEFAULT_DESTINATION,
        help=f"directory to create (default: ./{DEFAULT_DESTINATION})",
    )
    parser.add_argument("--force", action="store_true", help="write into a non-empty DEST anyway")
    parser.add_argument("--title", metavar="TEXT", help="thesis title to put in index.qmd")


def run(args: argparse.Namespace) -> int:
    destination = Path(args.dest)

    if destination.exists() and any(destination.iterdir()) and not args.force:
        print(
            f"thesis-refinery init: {destination} is not empty. "
            "Pass --force if you meant to write into it."
        )
        return ExitCode.USAGE

    files = templates(args.title or "论文题目")
    written: list[Path] = []
    for relative, text in files.items():
        path = destination / relative
        atomic_write_bytes(path, text.encode("utf-8"))
        written.append(path)

    print(f"wrote {len(written)} file(s) to {destination}:")
    for path in written:
        print(f"  {path.relative_to(destination)}")

    print(
        "\nNext:\n"
        "  1. set identity.required and style.header_footer.odd_page_text in thesis.yml\n"
        "  2. replace the placeholder chapters with your own\n"
        "  3. copy your institution's reference.docx into manuscript/ and point\n"
        "     both files at it\n"
        "  4. thesis-refinery build\n"
    )
    return ExitCode.OK
