"""Tests for docxlib.units."""

from __future__ import annotations

import pytest

from thesis_refinery.docxlib.units import (
    EMU_PER_INCH,
    EMU_PER_MM,
    inches_to_emu,
    mm_to_emu,
    mm_to_twips,
    pt_to_eighths,
    pt_to_half_points,
    twips_to_mm,
    twips_to_pt,
)


@pytest.mark.parametrize(
    ("millimetres", "twips"),
    [
        (25.0, 1417),  # the conventional Chinese thesis margin
        (20.0, 1134),
        (12.7, 720),  # exactly half an inch
        (210.0, 11906),  # A4 width, as Word writes it
        (297.0, 16838),  # A4 height
    ],
)
def test_mm_to_twips_matches_words_own_values(millimetres: float, twips: int) -> None:
    assert mm_to_twips(millimetres) == twips


def test_twips_to_mm_round_trips_within_rounding_error() -> None:
    for millimetres in (25.0, 20.0, 12.7, 210.0):
        assert twips_to_mm(mm_to_twips(millimetres)) == pytest.approx(millimetres, abs=0.01)


def test_twips_to_pt() -> None:
    assert twips_to_pt(20) == 1.0
    assert twips_to_pt(360) == 18.0


def test_pt_to_half_points() -> None:
    assert pt_to_half_points(10.5) == 21  # the caption size Word expects
    assert pt_to_half_points(12) == 24
    assert pt_to_half_points(9) == 18


def test_pt_to_eighths() -> None:
    assert pt_to_eighths(1.5) == 12  # the heavy three-line rule
    assert pt_to_eighths(0.75) == 6  # the light rule under the header row
    assert pt_to_eighths(0.0) == 0


def test_emu_conversions() -> None:
    assert mm_to_emu(25.4) == EMU_PER_INCH
    assert inches_to_emu(1.0) == EMU_PER_INCH
    assert mm_to_emu(210.0) == pytest.approx(210 * EMU_PER_MM, abs=1)


def test_conversions_round_rather_than_truncate() -> None:
    """Truncation would shrink every measurement that is not already exact.

    0.6 pt is 1.2 half-points, which is closer to 1 than to 0; integer division
    would silently drop it. 0.1 pt is genuinely less than half a point, so 0 is
    the correct answer there, not a collapse.
    """
    assert pt_to_half_points(0.6) == 1
    assert pt_to_eighths(0.2) == 2  # 1.6 eighths
    assert mm_to_twips(0.02) > 0  # 1.13 twips
    assert mm_to_emu(0.001) > 0

    assert pt_to_half_points(0.1) == 0  # smaller than the unit: correctly zero
