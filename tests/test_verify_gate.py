"""Tests for the verification gates.

The point of this file is the negative half. A gate that has never been observed
failing is not a gate, so every check here is exercised twice: once on a document
built to conform, and once on the same document corrupted in exactly the way that
check exists to catch.

The conforming document is synthesised in :func:`build_conforming` rather than
committed as a binary, so a reader can see what "conforming" means.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from thesis_refinery.config import Checks, Config, Identity, ProjectPaths
from thesis_refinery.docxlib.ns import CONTENT_TYPE, NSMAP
from thesis_refinery.docxlib.parse import parse
from thesis_refinery.docxlib.pkgs import Package
from thesis_refinery.docxlib.synth import (
    build_docx,
    document_xml,
    paragraph_xml,
    part_xml,
    style_xml,
    styles_xml,
    table_xml,
)
from thesis_refinery.docxlib.tables import apply_three_line_borders
from thesis_refinery.docxlib.xmlq import iter_descendants
from thesis_refinery.style import DEFAULT_STYLE, HeaderFooterSpec, StyleSpec
from thesis_refinery.verify.checks import all_checks
from thesis_refinery.verify.findings import Status
from thesis_refinery.verify.gate import evaluate

W = NSMAP["w"]
RUNNING_HEAD = "EXAMPLE THESIS"

STYLE = DEFAULT_STYLE.merged(
    header_footer=HeaderFooterSpec(odd_page_text=RUNNING_HEAD),
)
"""The specification these tests check against.

The running head is set explicitly because
:class:`~thesis_refinery.style.HeaderFooterSpec` ships a *default* wording, not a
fact, and a fixture that inherited it would only be testing that default.
"""

STYLES = styles_xml(
    [
        style_xml("Normal", "Normal", default=True),
        style_xml("Heading1", "Heading 1", based_on="Normal", outline_level=0),
        style_xml("CoverTitle", "Cover Title", based_on="Normal"),
        style_xml(
            "BodyText",
            "Body Text",
            based_on="Normal",
            paragraph_properties='<w:ind w:firstLine="480"/><w:spacing w:line="360" w:lineRule="auto"/>',
        ),
        style_xml("ImageCaption", "Image Caption", based_on="Normal"),
        style_xml("TableCaption", "Table Caption", based_on="Normal"),
    ]
)

COVER = paragraph_xml("A cover paragraph.", style="CoverTitle")
"""Front matter, on its own style.

Deliberately *not* a body style: a cover is not body text, and it must not be
drawn into the indent check.
"""


def _sect_pr(*, with_header: bool = True, fake: str = "", even: bool = False) -> str:
    references = ""
    if with_header:
        references = (
            '<w:headerReference w:type="default" r:id="rIdHeader"/>'
            '<w:footerReference w:type="default" r:id="rIdFooter"/>'
        )
    if even:
        references += '<w:headerReference w:type="even" r:id="rIdEven"/>'
    return (
        "<w:sectPr>"
        f"{references}"
        f'{fake}'
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1417" w:right="1134" w:bottom="1417" w:left="1134" '
        'w:header="720" w:footer="720" w:gutter="0"/>'
        '<w:pgNumType w:fmt="decimal" w:start="1"/>'
        "</w:sectPr>"
    )


def _even_header_part(text: str = "Chapter One") -> str:
    """An even-page running head with a bottom rule and its own text."""
    body = (
        "<w:p>"
        "<w:pPr><w:pBdr>"
        '<w:bottom w:val="single" w:sz="6" w:space="1" w:color="auto"/>'
        "</w:pBdr></w:pPr>"
        f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'
        "</w:p>"
    )
    return part_xml(root="w:hdr", namespaces={"w": W}, body=body)


def _header_part(text: str = RUNNING_HEAD) -> str:
    body = (
        "<w:p>"
        "<w:pPr><w:pBdr>"
        '<w:bottom w:val="single" w:sz="6" w:space="1" w:color="auto"/>'
        "</w:pBdr></w:pPr>"
        f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'
        "</w:p>"
    )
    return part_xml(root="w:hdr", namespaces={"w": W}, body=body)


def _footer_part() -> str:
    body = (
        "<w:p><w:r>"
        '<w:rPr><w:sz w:val="18"/></w:rPr>'
        '<w:fldChar w:fldCharType="begin"/>'
        "</w:r>"
        '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        '<w:r><w:rPr><w:sz w:val="18"/></w:rPr><w:t>1</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        "</w:p>"
    )
    return part_xml(root="w:ftr", namespaces={"w": W}, body=body)


def _table(fragment: str) -> str:
    """Apply the three-line borders to a table fragment, in memory."""
    from lxml import etree

    root = parse(document_xml(fragment))
    table = next(iter_descendants(root, "w:tbl"))
    apply_three_line_borders(table, DEFAULT_STYLE.table)
    return etree.tostring(table, encoding="unicode")


def build_conforming(path: Path, *, version: int = 0) -> Path:
    """A document that satisfies every check, optionally corrupted.

    *version* exists so each negative test can name the one thing it breaks;
    version 0 is the conforming document. Every version below is referenced by a
    case in ``test_a_deliberate_defect_makes_the_gate_fail``.
    """
    captions = paragraph_xml("Fig. 1 A caption.", style="ImageCaption")
    if version == 6:
        captions = paragraph_xml("Fig. 1 A caption.", style="BodyText")
    elif version == 7:
        captions = paragraph_xml("A caption without a prefix.", style="ImageCaption")

    table = _table(table_xml([["Head", "Value"], ["a", "1"]]))
    if version == 8:
        table = table_xml([["Head", "Value"], ["a", "1"]])

    heading = paragraph_xml("A heading", style="Heading1")
    if version == 9:
        heading = paragraph_xml("A heading", style="BodyText")

    body_paragraphs = "\n".join(
        [
            COVER,
            heading,
            paragraph_xml("First body paragraph.", style="BodyText"),
            paragraph_xml("Second body paragraph.", style="BodyText"),
            captions,
            table,
        ]
    )

    styles = STYLES
    if version == 4:
        # The indent is removed from the style rather than from a paragraph, so
        # this also exercises resolution through the style chain.
        styles = STYLES.replace('<w:ind w:firstLine="480"/>', "")

    if version == 3:
        # Two sections with a format change that does not restart: the body
        # would carry on from the front matter's roman numerals.
        break_paragraph = (
            "<w:p><w:pPr>"
            '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1417" w:right="1417" w:bottom="1417" w:left="1417" '
            'w:header="720" w:footer="720" w:gutter="0"/>'
            '<w:pgNumType w:fmt="upperRoman" w:start="1"/></w:sectPr>'
            "</w:pPr></w:p>"
        )
        final_sect = _sect_pr().replace(' w:start="1"', "")
        body = f"{break_paragraph}\n{body_paragraphs}\n{final_sect}"
    else:
        sect = _sect_pr()
        if version == 1:
            sect = sect.replace('w:w="11906" w:h="16838"', 'w:w="12240" w:h="15840"')
        elif version == 2:
            sect = sect.replace('w:top="1417"', 'w:top="2500"')
        body = f"{body_paragraphs}\n{sect}"

    header = _header_part()
    if version == 5:
        header = header.replace('<w:bottom w:val="single" w:sz="6" w:space="1" w:color="auto"/>', "")
    elif version == 11:
        header = header.replace(RUNNING_HEAD, "THE WRONG RUNNING HEAD")

    footer = _footer_part()
    if version == 12:
        footer = footer.replace('w:val="18"', 'w:val="24"')

    return build_docx(
        path,
        body=body,
        styles=styles,
        extra_parts={
            "word/header1.xml": header.encode("utf-8"),
            "word/footer1.xml": footer.encode("utf-8"),
        },
        extra_overrides={
            "/word/header1.xml": CONTENT_TYPE["header"],
            "/word/footer1.xml": CONTENT_TYPE["footer"],
        },
        document_relationships=[
            ("rIdHeader", "header", "header1.xml"),
            ("rIdFooter", "footer", "footer1.xml"),
        ],
    )


def make_config(root: Path, *, style: StyleSpec | None = None, checks: Checks | None = None) -> Config:
    return Config(
        root=root,
        paths=ProjectPaths(
            root=root,
            thesis_dir=root / "manuscript",
            chapters_dir=root / "manuscript/chapters",
            output_dir=root / "manuscript/_outputs",
        ),
        identity=Identity(),
        checks=checks or Checks(),
        style=style or STYLE,
    )


def findings_for(document: Path, config: Config) -> dict[str, tuple[Status, str]]:
    report = evaluate(document, config=config)
    return {finding.check: (finding.status, finding.message) for finding in report.findings}


# ---------------------------------------------------------------------------
# The conforming document
# ---------------------------------------------------------------------------


def test_the_conforming_document_passes_every_check(tmp_path: Path) -> None:
    document = build_conforming(tmp_path / "good.docx")
    report = evaluate(document, config=make_config(tmp_path))

    failures = report.failures()
    assert not failures, "\n".join(f"{f.check}: {f.message} {f.detail}" for f in failures)
    assert report.ok


def test_every_registered_check_actually_reported(tmp_path: Path) -> None:
    """A check that silently does nothing would let the gate pass vacuously."""
    document = build_conforming(tmp_path / "good.docx")
    report = evaluate(document, config=make_config(tmp_path))

    reported = {finding.check.split(".")[0] for finding in report.findings}
    assert {check.name for check in all_checks()} <= reported


# ---------------------------------------------------------------------------
# Negative: each check must be able to fail
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "check"),
    [
        (1, "geometry.page-size"),
        (2, "geometry.margins"),
        (3, "numbering.restart"),
        (4, "body.first-line-indent"),
        (5, "headers.rule"),
        (6, "captions.style"),
        (7, "captions.prefix"),
        (8, "tables.three-line"),
        (9, "frontmatter.boundary"),
        (11, "headers.odd-text"),
        (12, "footers.page-number"),
    ],
)
def test_a_deliberate_defect_makes_the_gate_fail(tmp_path: Path, version: int, check: str) -> None:
    document = build_conforming(tmp_path / f"broken-{version}.docx", version=version)
    report = evaluate(document, config=make_config(tmp_path))

    assert not report.ok, f"version {version} was expected to fail {check}"
    failed_checks = {finding.check for finding in report.failures()}
    assert check in failed_checks, f"expected {check}, got {sorted(failed_checks)}"
    assert all(finding.status is Status.FAIL for finding in report.failures())


def test_the_body_check_fails_when_no_paragraph_matches_the_body_styles(tmp_path: Path) -> None:
    """Zero matches must fail: a renamed style would otherwise pass vacuously."""
    document = build_conforming(tmp_path / "good.docx")
    style = DEFAULT_STYLE.merged(body_style_ids=("No Such Style",))
    report = evaluate(document, config=make_config(tmp_path, style=style))

    assert not report.ok
    assert "body.paragraphs" in {finding.check for finding in report.failures()}


def test_a_wrong_line_spacing_is_caught(tmp_path: Path) -> None:
    document = build_conforming(tmp_path / "good.docx")
    style = DEFAULT_STYLE.merged(body=DEFAULT_STYLE.body.__class__(line_spacing_twips=240))
    report = evaluate(document, config=make_config(tmp_path, style=style))

    assert "body.line-spacing" in {finding.check for finding in report.failures()}


def test_a_pinned_section_count_catches_a_missing_section(tmp_path: Path) -> None:
    document = build_conforming(tmp_path / "good.docx")
    config = make_config(tmp_path, checks=Checks(expect_sections=3))
    report = evaluate(document, config=config)

    assert "geometry.sections" in {finding.check for finding in report.failures()}


def test_a_pinned_caption_count_catches_a_lost_caption(tmp_path: Path) -> None:
    document = build_conforming(tmp_path / "good.docx")
    config = make_config(tmp_path, checks=Checks(expect_captions=50))
    report = evaluate(document, config=config)

    assert "captions.count" in {finding.check for finding in report.failures()}


# ---------------------------------------------------------------------------
# Skips are skips, not silent passes
# ---------------------------------------------------------------------------


def test_identity_check_skips_when_nothing_is_configured(tmp_path: Path) -> None:
    document = build_conforming(tmp_path / "good.docx")
    report = evaluate(document, config=make_config(tmp_path))

    identity = [f for f in report.findings if f.check == "frontmatter.identity"]
    assert len(identity) == 1
    assert identity[0].status is Status.SKIP
    assert "no identity strings are configured" in identity[0].message


def test_identity_check_can_fail_when_configured(tmp_path: Path) -> None:
    document = build_conforming(tmp_path / "good.docx")
    config = make_config(tmp_path)
    config = Config(
        root=config.root,
        paths=config.paths,
        identity=Identity(required=("Example University",)),
        checks=config.checks,
        style=config.style,
    )

    report = evaluate(document, config=config)

    assert "frontmatter.identity" in {finding.check for finding in report.failures()}


def test_a_skip_does_not_make_the_report_fail(tmp_path: Path) -> None:
    document = build_conforming(tmp_path / "good.docx")
    report = evaluate(document, config=make_config(tmp_path))

    assert any(finding.status is Status.SKIP for finding in report.findings)
    assert report.ok


# ---------------------------------------------------------------------------
# Report behaviour
# ---------------------------------------------------------------------------


def test_counts_and_json_shape(tmp_path: Path) -> None:
    document = build_conforming(tmp_path / "good.docx")
    report = evaluate(document, config=make_config(tmp_path))

    counts = report.counts()
    assert sum(counts.values()) == len(report.findings)
    assert counts["fail"] == 0

    payload = report.as_dict()
    assert payload["ok"] is True
    assert payload["document"] == str(document)
    assert isinstance(payload["findings"], list)


# ---------------------------------------------------------------------------
# Even-page running heads: the feature, the flag, and what the gate can see
# ---------------------------------------------------------------------------


def _settings_part(*, even_and_odd: bool) -> str:
    flag = '<w:evenAndOddHeaders w:val="true"/>' if even_and_odd else ""
    return part_xml(root="w:settings", namespaces={"w": W}, body=flag)


def build_with_even_header(
    path: Path,
    *,
    even_and_odd: bool | None,
    even_text: str = "Chapter One",
    default_text: str = RUNNING_HEAD,
) -> Path:
    """A document whose even pages are given their own running head.

    *even_and_odd* of ``None`` means "no settings part at all", which is the shape
    a renderer produces when it writes no document-wide flags — and the shape in
    which the even header was silently dead.
    """
    parts = {
        "word/header1.xml": _header_part(default_text).encode("utf-8"),
        "word/header2.xml": _even_header_part(even_text).encode("utf-8"),
        "word/footer1.xml": _footer_part().encode("utf-8"),
    }
    overrides = {
        "/word/header1.xml": CONTENT_TYPE["header"],
        "/word/header2.xml": CONTENT_TYPE["header"],
        "/word/footer1.xml": CONTENT_TYPE["footer"],
    }
    relationships = [
        ("rIdHeader", "header", "header1.xml"),
        ("rIdEven", "header", "header2.xml"),
        ("rIdFooter", "footer", "footer1.xml"),
    ]
    if even_and_odd is not None:
        parts["word/settings.xml"] = _settings_part(even_and_odd=even_and_odd).encode("utf-8")
        overrides["/word/settings.xml"] = CONTENT_TYPE["settings"]
        relationships.append(("rIdSettings", "settings", "settings.xml"))

    body = "\n".join(
        [
            COVER,
            paragraph_xml("A heading", style="Heading1"),
            paragraph_xml("A body paragraph.", style="BodyText"),
            paragraph_xml("Fig. 1 A caption.", style="ImageCaption"),
            _table(table_xml([["Head", "Value"], ["a", "1"]])),
            _sect_pr(even=True),
        ]
    )
    return build_docx(
        path,
        body=body,
        styles=STYLES,
        extra_parts=parts,
        extra_overrides=overrides,
        document_relationships=relationships,
    )


def status_of(document: Path, config: Config, check: str) -> Status | None:
    for finding in evaluate(document, config=config).findings:
        if finding.check == check:
            return finding.status
    return None


def test_the_flag_that_activates_even_page_headers_is_checked(tmp_path: Path) -> None:
    """The defect this check exists for: a configured feature that does nothing.

    A stage created and referenced a chapter-title running head for even pages, Word
    ignored it because ``w:evenAndOddHeaders`` was never set, and every other header
    check passed — the default head was present and correct, the rule was present,
    the folio was the right size. A document that is wrong in a way no check can see
    is the failure this project exists to make impossible.
    """
    document = build_with_even_header(tmp_path / "dead.docx", even_and_odd=None)
    config = make_config(tmp_path)

    assert status_of(document, config, "headers.even-pages") is Status.FAIL

    report = evaluate(document, config=config)
    finding = next(f for f in report.findings if f.check == "headers.even-pages")
    assert "evenAndOddHeaders" in finding.message
    assert "word/header2.xml" in finding.detail


def test_an_even_header_with_the_flag_set_passes(tmp_path: Path) -> None:
    """The control: the same document, with the one line that makes it work."""
    document = build_with_even_header(tmp_path / "alive.docx", even_and_odd=True)

    assert status_of(document, make_config(tmp_path), "headers.even-pages") is Status.PASS


def test_an_explicitly_disabled_flag_does_not_count_as_set(tmp_path: Path) -> None:
    """Three states, not two: absent, false, and true are different documents."""
    document = build_with_even_header(tmp_path / "off.docx", even_and_odd=False)

    assert status_of(document, make_config(tmp_path), "headers.even-pages") is Status.FAIL


def test_the_running_head_must_be_in_the_header_the_odd_pages_print(tmp_path: Path) -> None:
    """Searching every part lets the wrong part pay for the right one.

    Odd pages print the ``default`` header. A check that accepts the wording
    wherever it appears will pass a document whose default head is wrong, because
    the *even* head happens to contain the text — a pass earned by a part printed on
    the other side of the sheet.
    """
    document = build_with_even_header(
        tmp_path / "swapped.docx",
        even_and_odd=True,
        default_text="SOMETHING ELSE",
        even_text=RUNNING_HEAD,
    )
    config = make_config(tmp_path)

    assert status_of(document, config, "headers.odd-text") is Status.FAIL


def test_the_rule_is_asked_of_the_parts_the_document_uses(tmp_path: Path) -> None:
    """An orphaned part must not be able to fail the gate.

    A stage that repoints a section at a new part leaves the old one behind, still
    reachable through the document's relationships. Asking the rule of every part in
    the package means asking it of something the document does not print.
    """
    document = build_with_even_header(tmp_path / "orphan.docx", even_and_odd=True)
    config = make_config(tmp_path)

    # Add a header part that is related to the document but referenced by no
    # section: exactly the shape a stage leaves behind when it repoints a section.
    with Package(document) as package:
        package.write(
            "word/header9.xml",
            _even_header_part("Orphan").replace(
                '<w:bottom w:val="single" w:sz="6" w:space="1" w:color="auto"/>', ""
            ).encode("utf-8"),
        )
        package.ensure_content_type_override("word/header9.xml", CONTENT_TYPE["header"])
        package.add_relationship("word/document.xml", "header", "header9.xml")
        package.save(document, allow_in_place=True)

    findings = evaluate(document, config=config).findings
    rules = next(f for f in findings if f.check == "headers.rule")
    orphans = next(f for f in findings if f.check == "headers.orphans")

    assert rules.status is Status.PASS, "an unreferenced part must not fail the rule check"
    assert orphans.status is Status.WARN
    assert orphans.detail == ("word/header9.xml",)
    assert all(f.status is not Status.FAIL for f in findings if f.check.startswith("headers."))
