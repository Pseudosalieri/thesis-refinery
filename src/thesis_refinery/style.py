"""The single style specification.

Every formatting fact lives here, exactly once. The code that **applies** a fact
and the code that **checks** it both read it from this module. Their traversal
code stays separate — a shared walk would let a bug cancel itself out and report
a broken document as fine — but the facts are shared data.

That distinction is the whole reason this project exists. Its predecessor
implemented 26 formatting facts in one place and then re-typed 21 of them as
literals across roughly 60 sites in its audit scripts. The two copies drifted,
the audit started checking for a document nobody had asked for, and the failure
looked like a content bug for two months.

Values are stored in the units OOXML uses (see
:mod:`thesis_refinery.docxlib.units`) and are named after the measurement, not
after the XML attribute, so a reader can tell what they mean without a schema
open in another window.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from thesis_refinery.docxlib import units

__all__ = [
    "DEFAULT_STYLE",
    "CaptionSpec",
    "HeaderFooterSpec",
    "MarginSpec",
    "PageSpec",
    "ParagraphSpec",
    "StyleSpec",
    "TableSpec",
]


@dataclass(frozen=True)
class PageSpec:
    """Physical page size."""

    width_mm: float = 210.0
    height_mm: float = 297.0

    @property
    def width_twips(self) -> int:
        return units.mm_to_twips(self.width_mm)

    @property
    def height_twips(self) -> int:
        return units.mm_to_twips(self.height_mm)


@dataclass(frozen=True)
class MarginSpec:
    """Page margins and header/footer distances.

    Body pages and front-matter pages get different side margins: a bound thesis
    is usually given a wider gutter on the cover and declaration pages, which are
    produced separately from the body.
    """

    top_mm: float = 25.0
    bottom_mm: float = 25.0
    body_side_mm: float = 20.0
    front_matter_side_mm: float = 25.0
    header_distance_mm: float = 12.7
    footer_distance_mm: float = 12.7

    @property
    def top_twips(self) -> int:
        return units.mm_to_twips(self.top_mm)

    @property
    def bottom_twips(self) -> int:
        return units.mm_to_twips(self.bottom_mm)

    @property
    def body_side_twips(self) -> int:
        return units.mm_to_twips(self.body_side_mm)

    @property
    def front_matter_side_twips(self) -> int:
        return units.mm_to_twips(self.front_matter_side_mm)

    @property
    def header_distance_twips(self) -> int:
        return units.mm_to_twips(self.header_distance_mm)

    @property
    def footer_distance_twips(self) -> int:
        return units.mm_to_twips(self.footer_distance_mm)


@dataclass(frozen=True)
class ParagraphSpec:
    """Body paragraph metrics."""

    first_line_indent_twips: int = 480
    """Two full-width characters at 12 pt, i.e. the conventional Chinese indent."""

    line_spacing_twips: int = 360
    """1.5 lines, expressed as an exact spacing value."""

    space_before_twips: int = 0
    space_after_twips: int = 0


@dataclass(frozen=True)
class TableSpec:
    """Three-line table (三线表) borders, in eighth-points.

    The convention is one heavy rule above the header, one light rule below it,
    one heavy rule at the foot, and no vertical or inner rules at all.
    """

    outer_size_eighths: int = 12
    header_size_eighths: int = 6
    inner_border: str = "nil"
    vertical_border: str = "nil"


@dataclass(frozen=True)
class CaptionSpec:
    """Figure and table captions.

    Style ids are matched case-insensitively against the document's style
    definitions, because pandoc and Word disagree about the spelling
    (``ImageCaption`` versus ``Image Caption``).
    """

    image_style_ids: tuple[str, ...] = ("Image Caption", "ImageCaption")
    table_style_ids: tuple[str, ...] = ("Table Caption", "TableCaption")
    image_prefixes: tuple[str, ...] = ("Fig.", "Figure", "图")
    table_prefixes: tuple[str, ...] = ("Tab.", "Table", "表")
    font: str = "Times New Roman"
    size_pt: float = 10.5

    @property
    def size_half_points(self) -> int:
        return units.pt_to_half_points(self.size_pt)


@dataclass(frozen=True)
class HeaderFooterSpec:
    """Running heads and page numbers.

    ``odd_page_text`` is the running head on odd pages. It is a *default*, not a
    fact: every institution specifies its own wording, and printing the wrong
    one on every page of a submitted thesis is worse than printing nothing. Set
    it in ``thesis.yml``.
    """

    odd_page_text: str = "硕士学位论文"
    even_page_uses_heading: bool = True
    rule_below_header: bool = True
    footer_size_pt: float = 9.0
    front_matter_number_format: str = "upperRoman"
    body_number_format: str = "decimal"

    @property
    def footer_size_half_points(self) -> int:
        return units.pt_to_half_points(self.footer_size_pt)


@dataclass(frozen=True)
class StyleSpec:
    """The complete formatting specification for one thesis."""

    page: PageSpec = field(default_factory=PageSpec)
    margins: MarginSpec = field(default_factory=MarginSpec)
    body: ParagraphSpec = field(default_factory=ParagraphSpec)
    table: TableSpec = field(default_factory=TableSpec)
    caption: CaptionSpec = field(default_factory=CaptionSpec)
    header_footer: HeaderFooterSpec = field(default_factory=HeaderFooterSpec)

    heading_styles: tuple[str, ...] = ("Heading 1", "Heading 2", "Heading 3")
    """Style names of the numbered heading levels, in order.

    Also the set of names that may appear localised — a document formatted for a
    Chinese locale carries ``标题 1`` instead. Both spellings are accepted when
    reading; see :meth:`heading_style_matches`.
    """

    localized_heading_styles: tuple[str, ...] = ("标题 1", "标题 2", "标题 3")

    body_style_ids: tuple[str, ...] = ("First Paragraph", "Body Text", "BodyText", "Normal")

    toc_right_tab_twips: int | None = None
    """Right-aligned tab stop for TOC page numbers.

    ``None`` means "derive from the text width". An explicit value is needed when
    a human has hand-tuned the tab stop in Word, which is a legitimate thing for
    an author to do and must not be silently overwritten.
    """

    def heading_style_matches(self, style_name: str, level: int) -> bool:
        """True when *style_name* is *level*'s heading style, in either script.

        Accepting both spellings is what stops the localisation trap: a stage
        that scans "until the next Heading 1" silently runs to the end of the
        document once the style has been renamed to ``标题 1``, and the symptom
        looks like a broken appendix rather than a broken scan.
        """
        candidates = {
            self.heading_styles[level - 1] if level <= len(self.heading_styles) else "",
            self.localized_heading_styles[level - 1] if level <= len(self.localized_heading_styles) else "",
        }
        return style_name.strip() in {c for c in candidates if c}

    def merged(self, **overrides: Any) -> StyleSpec:
        """A copy with top-level sections replaced by :func:`dataclasses.replace`."""
        return replace(self, **overrides)


DEFAULT_STYLE = StyleSpec()
"""The specification used when ``thesis.yml`` says nothing about style."""
