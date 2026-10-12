"""Generate the bundled example's synthetic cover.

Committed alongside the ``.docx`` it writes, for the same reason
``make_reference_docx.py`` is: a binary in a repository is a binary nobody can
review. The file this produces is the input to the front-matter stage, so anyone
reading the example can see exactly which strings the cover contains without opening
Word.

Everything here is a placeholder. The cover deliberately keeps its hand-filled
fields — the author's name and the date are ``XXX`` — because that is what a real
cover looks like before it is signed off, and because the front-matter stage's whole
promise is that it will not touch them.

    python tools/make_placeholder_frontmatter.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY / "src"))

from thesis_refinery.docxlib.synth import (  # noqa: E402
    build_docx,
    paragraph_xml,
    style_xml,
    styles_xml,
)

DEFAULT_OUTPUT = REPOSITORY / "examples" / "demo-thesis" / "manuscript" / "front-cover.docx"

TITLE = "示例学位论文"
"""The wording the identity check requires. Synthetic, and obviously so."""

PLACEHOLDER = "XXX"
"""What an author fills in by hand. The stage must leave it exactly as it is."""

LINES: tuple[tuple[str, str], ...] = (
    (TITLE, "CoverTitle"),
    ("", "CoverLine"),
    (f"姓　　名：{PLACEHOLDER}", "CoverLine"),
    (f"学　　号：{PLACEHOLDER}", "CoverLine"),
    ("专　　业：示例专业", "CoverLine"),
    ("指导教师：示例教师", "CoverLine"),
    ("学　　院：示例学院", "CoverLine"),
    ("", "CoverLine"),
    (f"{PLACEHOLDER} 年 {PLACEHOLDER} 月", "CoverLine"),
)

STYLES = styles_xml(
    [
        style_xml("Normal", "Normal", default=True),
        style_xml(
            "CoverTitle",
            "Cover Title",
            based_on="Normal",
            paragraph_properties='<w:jc w:val="center"/><w:spacing w:before="480" w:after="480"/>',
            run_properties='<w:rFonts w:ascii="Times New Roman" w:eastAsia="宋体"/><w:sz w:val="36"/>',
        ),
        # `basedOn` the title on purpose: the front-matter stage has to carry the
        # dependency, or the imported lines fall back to Normal.
        style_xml(
            "CoverLine",
            "Cover Line",
            based_on="CoverTitle",
            paragraph_properties='<w:jc w:val="center"/><w:spacing w:before="0" w:after="120"/>',
            run_properties='<w:rFonts w:ascii="Times New Roman" w:eastAsia="宋体"/><w:sz w:val="24"/>',
        ),
    ]
)

SECTION = (
    "<w:sectPr>"
    '<w:pgSz w:w="11906" w:h="16838"/>'
    '<w:pgMar w:top="1417" w:right="1417" w:bottom="1417" w:left="1417" '
    'w:header="720" w:footer="720"/>'
    "</w:sectPr>"
)


def build(path: Path = DEFAULT_OUTPUT) -> Path:
    """Write the cover and return where it went."""
    body = "\n".join(
        [
            *(paragraph_xml(text, style=style) if text else f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr></w:p>' for text, style in LINES),
            SECTION,
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return build_docx(path, body=body, styles=STYLES)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    output = Path(arguments[0]) if arguments else DEFAULT_OUTPUT
    written = build(output)
    print(f"wrote {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
