"""Tests for the reference-entry stage.

The conversions are easy to write and easy to write *inertly*: a rule that matches
nothing produces exactly the output of a rule that matched correctly. So every test
here asserts on a change rather than on the absence of an error, and the two
assertions the stage makes about a replaced entry each get a negative case — because
the failure they exist to catch is silent non-conversion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.pkgs import Package
from thesis_refinery.docxlib.synth import build_docx, paragraph_xml, style_xml, styles_xml
from thesis_refinery.docxlib.xmlq import Element, find, iter_descendants, text_of
from thesis_refinery.stages import references
from thesis_refinery.stages.references import OptionsError
from thesis_refinery.style import DEFAULT_STYLE

DOCUMENT = "word/document.xml"

STYLES = styles_xml(
    [
        style_xml("Normal", "Normal", default=True),
        style_xml("Heading1", "Heading 1", based_on="Normal", outline_level=0),
        style_xml("BodyText", "Body Text", based_on="Normal"),
        style_xml("ImageCaption", "Image Caption", based_on="Normal"),
        style_xml("TableCaption", "Table Caption", based_on="Normal"),
    ]
)

HEADING = "参考文献"


def build_document(path: Path, entries: list[str], *, tail: str = "") -> Path:
    """Entries are plain text, unless one is given as raw XML.

    The exception exists for the hyperlink case: ``paragraph_xml`` escapes what it
    is given, so a caller that wants a real ``w:hyperlink`` has to supply the
    element rather than a string that looks like one.
    """
    body = "\n".join(
        [
            paragraph_xml("Chapter One", style="Heading1"),
            paragraph_xml(HEADING, style="Heading1"),
            *[
                entry
                if entry.lstrip().startswith("<w:p")
                else paragraph_xml(entry, style="BodyText")
                for entry in entries
            ],
            tail,
            "<w:sectPr/>",
        ]
    )
    return build_docx(path, body=body, styles=STYLES)


def hyperlink_entry(text: str, link: str) -> str:
    """A reference entry whose DOI is a real ``w:hyperlink``, not text that resembles one."""
    return (
        '<w:p><w:pPr><w:pStyle w:val="BodyText"/></w:pPr>'
        f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'
        f'<w:hyperlink r:id="rId9"><w:r><w:t>{link}</w:t></w:r></w:hyperlink>'
        "</w:p>"
    )


def entry_paragraphs(path: Path) -> list[Element]:
    with Package(path) as package:
        document = package.xml(DOCUMENT)
        body = find(document, "w:body")
        return [
            child
            for child in body
            if child.tag == q("p") and references.ENTRY_NUMBER.match(text_of(child).strip())
        ]


def text_of_entries(path: Path) -> list[str]:
    return [text_of(paragraph) for paragraph in entry_paragraphs(path)]


def options(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {"heading": HEADING}
    values.update(overrides)
    return values


def run(source: Path, destination: Path, **overrides: object) -> Path:
    return references.apply(
        source, destination, style=DEFAULT_STYLE, options=options(**overrides)
    )


# ---------------------------------------------------------------------------
# Author case
# ---------------------------------------------------------------------------


def test_all_caps_surnames_become_initial_capital() -> None:
    assert references.author_case("SURNAME A B, OTHER C") == "Surname A B, Other C"


def test_a_hyphenated_surname_is_capitalised_segment_by_segment() -> None:
    """The rule is about the first letter of each name, not of the string."""
    assert references.author_case("SALGADO-POLO J") == "Salgado-Polo J"


def test_a_non_latin_surname_is_left_alone() -> None:
    """`isalpha` is the gate, so a name nobody can case is not rewritten.

    A version keying on "upper equals value" alone would corrupt it instead of
    reformatting it.
    """
    assert references.author_case("李 明") == "李 明"


def test_an_already_cased_surname_is_unchanged() -> None:
    assert references.author_case("Surname A") == "Surname A"


def test_initials_are_not_touched() -> None:
    assert references.author_case("SMITH J K") == "Smith J K"


# ---------------------------------------------------------------------------
# Italic spans
# ---------------------------------------------------------------------------


def test_the_journal_name_and_every_et_al_are_spans() -> None:
    text = "[1] SMITH J, et al. A study. Journal of Things, 2024, 12(3): 1-9."
    spans = references.italic_spans(text)

    covered = [text[start:end] for start, end in spans]
    assert "Journal of Things" in covered
    assert covered.count("et al.") == 1


def test_text_with_no_journal_yields_only_the_et_al_spans() -> None:
    assert references.italic_spans("[1] SMITH J. A book.") == []


# ---------------------------------------------------------------------------
# Writing over runs
# ---------------------------------------------------------------------------


def test_a_shorter_replacement_does_not_leave_the_old_tail(tmp_path: Path) -> None:
    """The defect an ordinary splice has: the text after the range stays put.

    A Chinese entry becoming an English one is always shorter, so this is not an
    edge case — it is the normal case for the one conversion that replaces a whole
    entry.
    """
    document = build_document(tmp_path / "in.docx", ["[1] 一篇很长的中文期刊著录，作者等"])
    paragraph = entry_paragraphs(document)[0]

    references.write_text_over_runs(paragraph, 0, len(text_of(paragraph)), "[1] Short.")

    assert text_of(paragraph) == "[1] Short."


def test_text_inside_a_hyperlink_is_cleared_but_the_link_survives(tmp_path: Path) -> None:
    """The recorded incident: the old DOI display text was appended to the new record.

    The hyperlink element and its relationship stay, so the DOI still resolves; it
    simply stops carrying text.
    """
    document = build_document(
        tmp_path / "in.docx",
        [hyperlink_entry("[1] Old record. ", "10.1000/old")],
    )
    paragraph = entry_paragraphs(document)[0]
    assert iter_descendants(paragraph, "w:hyperlink"), "the fixture must contain a real hyperlink"
    assert "10.1000/old" in text_of(paragraph)

    references.write_text_over_runs(paragraph, 0, len(text_of(paragraph)), "[1] New record.")

    assert text_of(paragraph) == "[1] New record."
    assert list(iter_descendants(paragraph, "w:hyperlink")), (
        "the hyperlink element must survive so the DOI still points somewhere"
    )


def test_writing_over_a_range_no_run_covers_is_refused(tmp_path: Path) -> None:
    document = build_document(tmp_path / "in.docx", ["[1] Short."])
    paragraph = entry_paragraphs(document)[0]

    with pytest.raises(ValueError, match="no run covers"):
        references.write_text_over_runs(paragraph, 400, 500, "x")


# ---------------------------------------------------------------------------
# The conversions, through the stage
# ---------------------------------------------------------------------------


def test_et_al_replaces_the_chinese_marker(tmp_path: Path) -> None:
    source = build_document(tmp_path / "in.docx", ["[1] SMITH J, 等. A study. Journal, 2024, 1(1): 1."])
    destination = tmp_path / "out.docx"

    run(source, destination)

    assert "等" not in text_of_entries(destination)[0]
    assert "et al." in text_of_entries(destination)[0]


def test_a_doubled_full_stop_after_et_al_is_collapsed(tmp_path: Path) -> None:
    """`等.` becomes `et al..` first; the second stop is the one already there."""
    source = build_document(tmp_path / "in.docx", ["[1] SMITH J, 等. A study. Journal, 2024, 1(1): 1."])
    destination = tmp_path / "out.docx"

    run(source, destination)

    assert "et al.." not in text_of_entries(destination)[0]


def test_the_journal_name_gets_an_italic_flag(tmp_path: Path) -> None:
    source = build_document(
        tmp_path / "in.docx", ["[1] SMITH J. A study. Journal of Things, 2024, 12(3): 1-9."]
    )
    destination = tmp_path / "out.docx"

    run(source, destination)

    paragraph = entry_paragraphs(destination)[0]
    italic = [
        run_element
        for run_element in iter_descendants(paragraph, "w:r")
        if (properties := find(run_element, "w:rPr")) is not None
        and find(properties, "w:i") is not None
    ]
    assert italic, "an italic run must appear"
    assert "Journal of Things" in "".join(text_of(r) for r in italic)


def test_surnames_are_converted_in_the_document(tmp_path: Path) -> None:
    source = build_document(tmp_path / "in.docx", ["[1] SURNAME A B. A study. Journal, 2024, 1(1): 1."])
    destination = tmp_path / "out.docx"

    run(source, destination)

    assert text_of_entries(destination)[0].startswith("[1] Surname A B.")


def test_a_conversion_can_be_switched_off(tmp_path: Path) -> None:
    """Each rule is a configured rule, so each has to be observable as one."""
    source = build_document(tmp_path / "in.docx", ["[1] SURNAME A B. A study. Journal, 2024, 1(1): 1."])
    destination = tmp_path / "out.docx"

    run(source, destination, surname_case=False, journal_italic=False)

    assert text_of_entries(destination)[0].startswith("[1] SURNAME A B.")


def test_the_text_of_an_untouched_entry_is_preserved(tmp_path: Path) -> None:
    """Nothing but the configured rules may touch an entry.

    Asserted with every rule switched off, so that what is being tested is the
    *absence* of change rather than the presence of a particular conversion.
    """
    entry = "[1] SMITH J. A book. Publisher, 2024."
    source = build_document(tmp_path / "in.docx", [entry])
    destination = tmp_path / "out.docx"

    run(source, destination, et_al=False, surname_case=False, journal_italic=False)

    assert text_of_entries(destination)[0] == entry


# ---------------------------------------------------------------------------
# Whole-entry overrides, and the assertions that make them meaningful
# ---------------------------------------------------------------------------


def test_an_override_replaces_the_whole_entry(tmp_path: Path) -> None:
    source = build_document(tmp_path / "in.docx", ["[3] 中文期刊著录，作者等. 标题. 期刊, 2025, 1(1): 1."])
    destination = tmp_path / "out.docx"

    run(
        source,
        destination,
        overrides={3: "XU Y Y, et al. A title. Chinese Journal, 2025, 1(1): 1."},
        required_fragments={3: ["XU Y Y", "Chinese Journal"]},
    )

    assert text_of_entries(destination)[0] == "[3] XU Y Y, et al. A title. Chinese Journal, 2025, 1(1): 1."


def test_an_override_is_applied_after_the_surname_rule(tmp_path: Path) -> None:
    """Otherwise a fully-cased surname in the replacement gets pushed back to
    initial capitals by the rule that ran before it."""
    source = build_document(tmp_path / "in.docx", ["[3] 中文著录."])
    destination = tmp_path / "out.docx"

    run(
        source,
        destination,
        overrides={3: "XU Y Y, et al. A title. Journal, 2025, 1(1): 1."},
        required_fragments={3: ["XU Y Y"]},
    )

    assert "XU Y Y" in text_of_entries(destination)[0]


def test_cjk_left_in_a_replaced_entry_is_refused(tmp_path: Path) -> None:
    """The assertion exists because silent non-conversion looks like success."""
    source = build_document(tmp_path / "in.docx", ["[3] 中文著录."])
    destination = tmp_path / "out.docx"

    with pytest.raises(OptionsError, match="still contains CJK"):
        run(
            source,
            destination,
            # Required fragments are satisfied, so only the CJK rule can fire.
            overrides={3: "XU Y Y 中文"},
            required_fragments={3: ["XU Y Y"]},
        )


def test_a_missing_required_fragment_is_refused(tmp_path: Path) -> None:
    source = build_document(tmp_path / "in.docx", ["[3] 中文著录."])
    destination = tmp_path / "out.docx"

    with pytest.raises(OptionsError, match="missing the required fragment"):
        run(
            source,
            destination,
            overrides={3: "XU Y Y, et al. A title."},
            required_fragments={3: ["A fragment that is not there"]},
        )


def test_an_override_without_fragments_is_refused(tmp_path: Path) -> None:
    """A whole-entry replacement with nothing asserted about it cannot be told
    apart from one that silently did not happen."""
    source = build_document(tmp_path / "in.docx", ["[3] 中文著录."])

    with pytest.raises(OptionsError, match="no required_fragments"):
        run(source, tmp_path / "out.docx", overrides={3: "XU Y Y."})


# ---------------------------------------------------------------------------
# Finding the bibliography
# ---------------------------------------------------------------------------


def test_the_scan_stops_at_the_next_level_one_heading(tmp_path: Path) -> None:
    """The paragraph after the next chapter is left exactly as it was.

    Asserted on *that paragraph's* text rather than on the set of paragraphs that
    look like entries: `[9] NOT AN ENTRY` still looks like one, and a scan that ran
    on into it would convert it. Leaving it alone is the observable difference.
    """
    source = build_document(
        tmp_path / "in.docx",
        ["[1] SURNAME A. A study. Journal, 2024, 1(1): 1."],
        tail=paragraph_xml("Chapter Two", style="Heading1")
        + paragraph_xml("[9] NOT AN ENTRY. Something else.", style="BodyText"),
    )
    destination = tmp_path / "out.docx"

    run(source, destination)

    with Package(destination) as package:
        text = text_of(package.xml(DOCUMENT))

    assert "[9] NOT AN ENTRY. Something else." in text, (
        "a paragraph past the next level-1 heading must not be converted"
    )
    assert text_of_entries(destination)[0] == "[1] Surname A. A study. Journal, 2024, 1(1): 1."


def test_the_scan_stops_at_a_caption(tmp_path: Path) -> None:
    """A caption run on into would be rewritten as if it were an entry."""
    source = build_document(
        tmp_path / "in.docx",
        ["[1] SMITH J. A study. Journal, 2024, 1(1): 1."],
        tail=paragraph_xml("Fig. 1 A caption.", style="ImageCaption"),
    )
    destination = tmp_path / "out.docx"

    run(source, destination)

    with Package(destination) as package:
        text = text_of(package.xml(DOCUMENT))
    assert "Fig. 1 A caption." in text


def test_a_missing_heading_says_which_option_to_set(tmp_path: Path) -> None:
    source = build_document(tmp_path / "in.docx", ["[1] SMITH J. A study."])

    with pytest.raises(OptionsError, match="no paragraph reads"):
        run(source, tmp_path / "out.docx", heading="文献")


def test_a_bibliography_with_no_entries_is_refused(tmp_path: Path) -> None:
    """Nothing to convert is indistinguishable from a conversion that failed."""
    source = build_document(tmp_path / "in.docx", [])

    with pytest.raises(OptionsError, match="has no entries"):
        run(source, tmp_path / "out.docx")


def test_a_short_scan_is_caught_before_anything_is_rewritten(tmp_path: Path) -> None:
    """A scan that stopped early takes the rest of the document with it."""
    source = build_document(tmp_path / "in.docx", ["[1] SMITH J. A study."])

    with pytest.raises(OptionsError, match="expects at least 48"):
        run(source, tmp_path / "out.docx", expect_entries=48)


# ---------------------------------------------------------------------------
# The stage's contract
# ---------------------------------------------------------------------------


def test_the_source_is_never_modified(tmp_path: Path) -> None:
    source = build_document(tmp_path / "in.docx", ["[1] SURNAME A. A study. Journal, 2024, 1(1): 1."])
    before = source.read_bytes()

    run(source, tmp_path / "out.docx")

    assert source.read_bytes() == before


def test_applying_twice_changes_nothing_the_second_time(tmp_path: Path) -> None:
    source = build_document(tmp_path / "in.docx", ["[1] SURNAME A. A study. Journal, 2024, 1(1): 1."])
    first = tmp_path / "first.docx"
    second = tmp_path / "second.docx"

    run(source, first)
    run(first, second)

    with Package(first) as left, Package(second) as right:
        assert left.read(DOCUMENT) == right.read(DOCUMENT)


def test_an_unknown_option_is_refused_and_the_known_ones_named(tmp_path: Path) -> None:
    with pytest.raises(OptionsError, match="Known:") as excinfo:
        references.apply(
            tmp_path / "in.docx",
            tmp_path / "out.docx",
            style=DEFAULT_STYLE,
            options={"heading": HEADING, "surname": True},
        )
    assert "surname" in str(excinfo.value)
