"""Tests for the chemistry profile's caption and subscript handling.

The profile's spectra-appendix module is not present in this checkout, so nothing
here imports it. What is present is tested properly, because a module that ships
untested would contradict the one rule this project states most loudly: a check
that has never been observed failing is not a check.

The property these tests care about most is idempotence. Both modules split runs,
and a run-splitter that is not idempotent produces a document that grows every
time the pipeline runs — which is invisible in a passing test that only looks at
the text, and very visible in a diff after twenty builds.
"""

from __future__ import annotations

import pytest
from lxml import etree

from thesis_refinery.docxlib.ns import q
from thesis_refinery.docxlib.synth import paragraph_xml, parse_fragment
from thesis_refinery.docxlib.xmlq import find, findall, iter_descendants, local_name, text_of
from thesis_refinery.profiles.chemistry import captions, subscripts

PREFIX = "Fig."


def paragraph(text: str) -> etree._Element:
    return parse_fragment(paragraph_xml(text))


def rendered(element: etree._Element) -> bytes:
    return etree.tostring(element)


def run_texts(element: etree._Element) -> list[str]:
    return [run.text or "" for run in iter_descendants(element, "w:t")]


# ---------------------------------------------------------------------------
# Figure captions
# ---------------------------------------------------------------------------


def test_formatting_a_caption_preserves_its_text() -> None:
    """Formatting is a run split. Losing a character while splitting is the bug."""
    element = paragraph("Fig. 3-1 A placeholder caption.")
    before = text_of(element)

    captions.format_figure_caption(element, prefix=PREFIX)

    assert text_of(element) == before


def test_formatting_a_caption_splits_it_into_more_than_one_run() -> None:
    element = paragraph("Fig. 3-1 A placeholder caption.")
    assert len(run_texts(element)) == 1

    assert captions.format_figure_caption(element, prefix=PREFIX) is True
    assert len(run_texts(element)) > 1


def test_the_prefix_run_is_bold() -> None:
    element = paragraph("Fig. 3-1 A placeholder caption.")
    captions.format_figure_caption(element, prefix=PREFIX)

    first = "".join(run_texts(element))[: len("Fig. 3")]
    assert first.startswith("Fig.")
    bold_runs = [
        run
        for run in iter_descendants(element, "w:r")
        if find(run, "w:rPr") is not None and find(find(run, "w:rPr"), "w:b") is not None
    ]
    assert bold_runs, "the prefix must be bold; otherwise the split achieved nothing visible"


def test_the_ordinal_is_superscript() -> None:
    element = paragraph("Fig. 3-1 A placeholder caption.")
    captions.format_figure_caption(element, prefix=PREFIX)

    superscripts = [
        align.get(q("val"))
        for align in iter_descendants(element, "w:vertAlign")
    ]
    assert subscripts.SUBSCRIPT not in superscripts
    assert "superscript" in superscripts


def test_formatting_a_caption_twice_changes_nothing_the_second_time() -> None:
    element = paragraph("Fig. 3-1 A placeholder caption.")
    captions.format_figure_caption(element, prefix=PREFIX)
    once = rendered(element)

    captions.format_figure_caption(element, prefix=PREFIX)

    assert rendered(element) == once


def test_a_caption_without_an_ordinal_is_still_handled() -> None:
    element = paragraph("Fig. A caption with no ordinal.")
    before = text_of(element)

    captions.format_figure_caption(element, prefix=PREFIX)

    assert text_of(element) == before


def test_formatting_reports_whether_it_changed_anything() -> None:
    """The return value is what lets a caller count what happened.

    A formatter that always reports success cannot be asserted on, and a stage
    built on it cannot tell "nothing needed doing" from "nothing matched".
    """
    element = paragraph("A paragraph that is not a caption at all.")

    assert captions.format_figure_caption(element, prefix=PREFIX) is False


# ---------------------------------------------------------------------------
# Spectrum captions
# ---------------------------------------------------------------------------


def test_a_spectrum_kind_is_superscripted() -> None:
    element = paragraph("Fig. 3-2 1H-NMR spectrum of compound E-1.")
    before = text_of(element)

    captions.format_spectrum_caption(element, prefix=PREFIX, superscript_tokens=("1", "13"))

    assert text_of(element) == before
    alignments = [a.get(q("val")) for a in iter_descendants(element, "w:vertAlign")]
    assert alignments.count("superscript") >= 1


def test_an_empty_token_list_still_preserves_the_text_and_stays_idempotent() -> None:
    """With no tokens there is nothing extra to superscript, but nothing may break.

    The ordinal is superscripted regardless -- that is the figure-caption shape,
    not the spectrum-kind rule -- so this asserts the properties that hold either
    way rather than the absence of a `w:vertAlign`.
    """
    element = paragraph("Fig. 3-2 13C-NMR spectrum of compound E-1.")
    before = text_of(element)

    captions.format_spectrum_caption(element, prefix=PREFIX, superscript_tokens=())
    once = rendered(element)

    captions.format_spectrum_caption(element, prefix=PREFIX, superscript_tokens=())

    assert text_of(element) == before
    assert rendered(element) == once


def test_formatting_a_spectrum_caption_twice_changes_nothing_the_second_time() -> None:
    element = paragraph("Fig. 3-2 13C-NMR spectrum of compound E-1.")
    captions.format_spectrum_caption(element, prefix=PREFIX, superscript_tokens=("1", "13"))
    once = rendered(element)

    captions.format_spectrum_caption(element, prefix=PREFIX, superscript_tokens=("1", "13"))

    assert rendered(element) == once


# ---------------------------------------------------------------------------
# Subscripts
# ---------------------------------------------------------------------------


def test_the_pattern_matches_a_software_reported_term() -> None:
    assert subscripts.TERM_PATTERN, "the pattern must be a module constant, not an inline literal"
    import re

    assert re.search(subscripts.TERM_PATTERN, "ΔG_Bind")


def test_find_terms_locates_a_run() -> None:
    element = paragraph("ΔG_Bind is reported by the modelling package.")
    found = subscripts.find_terms(element, pattern=subscripts.TERM_PATTERN)

    assert found


def test_find_terms_returns_nothing_for_plain_text() -> None:
    element = paragraph("No reported quantities here.")
    assert subscripts.find_terms(element, pattern=subscripts.TERM_PATTERN) == []


def test_applying_a_subscript_preserves_the_text_and_marks_the_tail() -> None:
    element = paragraph("The value ΔG_Bind was reported.")
    before = text_of(element)

    changed = subscripts.apply_subscripts(element, pattern=subscripts.TERM_PATTERN)

    assert changed >= 1
    assert text_of(element) == before
    alignments = [a.get(q("val")) for a in iter_descendants(element, "w:vertAlign")]
    assert subscripts.SUBSCRIPT in alignments


def test_applying_a_subscript_twice_changes_nothing_the_second_time() -> None:
    element = paragraph("The value ΔG_Bind was reported.")
    subscripts.apply_subscripts(element, pattern=subscripts.TERM_PATTERN)
    once = rendered(element)

    subscripts.apply_subscripts(element, pattern=subscripts.TERM_PATTERN)

    assert rendered(element) == once


def test_a_count_expectation_that_does_not_match_is_refused() -> None:
    """The whole point of the count: a stage that matched nothing must not pass.

    Formatting a term that is not there and formatting it correctly produce the
    same output, so without a count the two are indistinguishable.
    """
    element = paragraph("The value ΔG_Bind was reported.")

    with pytest.raises(subscripts.SubscriptCountError):
        subscripts.apply_subscripts(element, pattern=subscripts.TERM_PATTERN, expect=5)


def test_a_count_expectation_that_matches_is_accepted() -> None:
    element = paragraph("The value ΔG_Bind was reported.")
    found = len(subscripts.find_terms(element, pattern=subscripts.TERM_PATTERN))

    assert subscripts.apply_subscripts(element, pattern=subscripts.TERM_PATTERN, expect=found) >= 1


def test_a_pattern_that_matches_nothing_raises_when_a_count_was_expected() -> None:
    """This is the case an unguarded stage reports as success."""
    element = paragraph("Nothing to subscript here.")

    with pytest.raises(subscripts.SubscriptCountError):
        subscripts.apply_subscripts(element, pattern=subscripts.TERM_PATTERN, expect=1)


def test_a_pattern_that_matches_nothing_is_silent_without_an_expectation() -> None:
    element = paragraph("Nothing to subscript here.")
    assert subscripts.apply_subscripts(element, pattern=subscripts.TERM_PATTERN) == 0


# ---------------------------------------------------------------------------
# The profile package itself
# ---------------------------------------------------------------------------


def test_no_core_module_imports_the_profile() -> None:
    """An optional dependency that creeps into an import path stops being optional.

    Asserted by reading the source rather than by importing, because importing is
    exactly what would hide the problem.
    """
    from pathlib import Path

    package = Path(__file__).resolve().parent.parent / "src" / "thesis_refinery"
    for path in package.rglob("*.py"):
        if "profiles" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert "profiles" not in text or "import" not in text.split("profiles")[0][-20:], path


def test_the_registry_lists_chemistry() -> None:
    from thesis_refinery.profiles import available, load

    assert "chemistry" in available()
    assert load("chemistry").__name__ == "thesis_refinery.profiles.chemistry"


def test_an_unknown_profile_names_the_known_ones() -> None:
    from thesis_refinery.profiles import load

    with pytest.raises(ValueError, match="chemistry"):
        load("alchemy")


def test_the_chemistry_package_exports_only_what_exists() -> None:
    """`__all__` is a promise. A name in it that cannot be imported is a broken build.

    The spectra-appendix module was planned and is not present; the package must
    say so rather than raising ImportError on first attribute access.
    """
    from thesis_refinery.profiles import chemistry

    for name in chemistry.__all__:
        assert getattr(chemistry, name) is not None, name


def test_caption_runs_keep_their_element_names_recognisable() -> None:
    """A sanity check that the split produced runs, not something else."""
    element = paragraph("Fig. 3-1 A caption.")
    captions.format_figure_caption(element, prefix=PREFIX)

    names = {local_name(child.tag) for child in element}
    assert names <= {"pPr", "r", "bookmarkStart", "bookmarkEnd"}
    assert len(findall(element, "w:r")) > 1
