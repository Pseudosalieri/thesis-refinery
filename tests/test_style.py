"""Tests for the style specification.

These assert the *values Word actually needs*, not merely that the dataclass
holds numbers. A specification that is internally consistent but produces a
11907-twip page is still wrong.
"""

from __future__ import annotations

import dataclasses

import pytest

from thesis_refinery.style import (
    DEFAULT_STYLE,
    CaptionSpec,
    HeaderFooterSpec,
    MarginSpec,
    PageSpec,
    ParagraphSpec,
    StyleSpec,
)


def test_a4_page_converts_to_the_twips_word_writes() -> None:
    assert DEFAULT_STYLE.page.width_twips == 11906
    assert DEFAULT_STYLE.page.height_twips == 16838


def test_default_margins_convert_to_the_expected_twips() -> None:
    margins = DEFAULT_STYLE.margins
    assert margins.top_twips == 1417
    assert margins.bottom_twips == 1417
    assert margins.body_side_twips == 1134
    assert margins.front_matter_side_twips == 1417
    assert margins.header_distance_twips == 720


def test_body_indent_is_two_full_width_characters() -> None:
    """480 twips is 24 pt, i.e. two 12 pt characters: the Chinese convention."""
    assert DEFAULT_STYLE.body.first_line_indent_twips == 480


def test_line_spacing_is_one_and_a_half() -> None:
    # 360 twips is 18 pt, and 18 / 12 = 1.5
    assert DEFAULT_STYLE.body.line_spacing_twips == 360
    assert DEFAULT_STYLE.body.line_spacing_twips / 20 == 18


def test_three_line_table_borders_are_heavy_light_heavy() -> None:
    table = DEFAULT_STYLE.table
    assert table.outer_size_eighths == 12  # 1.5 pt
    assert table.header_size_eighths == 6  # 0.75 pt
    assert table.inner_border == "nil"
    assert table.vertical_border == "nil"


def test_caption_sizes_convert_to_half_points() -> None:
    assert DEFAULT_STYLE.caption.size_half_points == 21  # 10.5 pt


def test_footer_size_converts_to_half_points() -> None:
    assert DEFAULT_STYLE.header_footer.footer_size_half_points == 18  # 9 pt


def test_caption_styles_accept_both_pandoc_and_word_spellings() -> None:
    """pandoc emits `ImageCaption`; Word displays `Image Caption`. Match both."""
    assert "ImageCaption" in DEFAULT_STYLE.caption.image_style_ids
    assert "Image Caption" in DEFAULT_STYLE.caption.image_style_ids
    assert "TableCaption" in DEFAULT_STYLE.caption.table_style_ids


def test_heading_style_matches_the_english_spelling() -> None:
    assert DEFAULT_STYLE.heading_style_matches("Heading 1", 1)
    assert DEFAULT_STYLE.heading_style_matches("Heading 2", 2)
    assert not DEFAULT_STYLE.heading_style_matches("Heading 2", 1)


def test_heading_style_matches_the_localised_spelling() -> None:
    """The localisation trap: after `thesis_format` runs, Heading 1 becomes 标题 1.

    A scan that only knows the English name runs to the end of the document and
    the symptom shows up as a mangled appendix, not as a broken scan.
    """
    assert DEFAULT_STYLE.heading_style_matches("标题 1", 1)
    assert DEFAULT_STYLE.heading_style_matches("标题 3", 3)


def test_heading_style_matches_ignores_case() -> None:
    """pandoc's built-in reference document spells the style `heading 1`.

    A project rendered without a `reference.docx` -- every project immediately
    after `init` -- therefore carries a lower-case name, and a case-sensitive
    comparison finds no heading at all. That failure cascades: no heading means no
    front-matter boundary, every paragraph is classified as front matter, and the
    body check then fails for want of anything to inspect.
    """
    assert DEFAULT_STYLE.heading_style_matches("heading 1", 1)
    assert DEFAULT_STYLE.heading_style_matches("HEADING 2", 2)
    assert DEFAULT_STYLE.heading_style_matches("Heading 3", 3)


def test_heading_style_match_tolerates_surrounding_whitespace() -> None:
    assert DEFAULT_STYLE.heading_style_matches("  Heading 1 ", 1)


def test_heading_style_match_rejects_an_unknown_level() -> None:
    assert not DEFAULT_STYLE.heading_style_matches("Heading 9", 9)


def test_heading_style_match_rejects_an_unrelated_style() -> None:
    assert not DEFAULT_STYLE.heading_style_matches("Body Text", 1)
    assert not DEFAULT_STYLE.heading_style_matches("", 1)


def test_style_spec_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_STYLE.body = ParagraphSpec(first_line_indent_twips=0)  # type: ignore[misc]


def test_merged_replaces_only_the_named_section() -> None:
    custom = DEFAULT_STYLE.merged(margins=MarginSpec(top_mm=30.0))

    assert custom.margins.top_mm == 30.0
    assert custom.margins.bottom_mm == DEFAULT_STYLE.margins.bottom_mm
    assert custom.page == DEFAULT_STYLE.page


def test_defaults_are_independent_dataclass_instances() -> None:
    """A mutable default shared between instances would be a classic trap.

    Every nested spec is frozen, so this is about identity: two specs built
    separately must not alias one another's sections.
    """
    first = StyleSpec()
    second = StyleSpec()
    assert first.page is not second.page
    assert first.page == second.page


def test_toc_tab_default_is_none_meaning_derive_it() -> None:
    """An explicit tab stop means a human tuned it; None means "work it out"."""
    assert DEFAULT_STYLE.toc_right_tab_twips is None


def test_nested_specs_are_frozen_too() -> None:
    for spec in (PageSpec(), MarginSpec(), CaptionSpec(), HeaderFooterSpec()):
        assert dataclasses.is_dataclass(spec)
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.__setattr__("nonsense", 1)
