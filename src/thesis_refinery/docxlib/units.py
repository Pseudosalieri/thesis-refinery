"""Unit conversions.

OOXML mixes four units, and mixing them up is one of the quietest ways to get a
document subtly wrong:

===============  ==========================  ==============================
Unit             Meaning                     Where it appears
===============  ==========================  ==============================
twip            1/1440 inch                 margins, indents, spacing
half-point      1/2 point                   ``w:sz``
eighth-point    1/8 point                   border ``w:sz``
EMU             1/914400 inch               drawing extents
===============  ==========================  ==============================

Everything here rounds rather than truncates, and every function is a total
function of its argument: no unit ever silently becomes zero because of integer
division.
"""

from __future__ import annotations

__all__ = [
    "EMU_PER_INCH",
    "EMU_PER_MM",
    "TWIPS_PER_INCH",
    "inches_to_emu",
    "mm_to_emu",
    "mm_to_twips",
    "pt_to_eighths",
    "pt_to_half_points",
    "twips_to_mm",
    "twips_to_pt",
]

TWIPS_PER_INCH = 1440
EMU_PER_INCH = 914_400
MM_PER_INCH = 25.4
EMU_PER_MM = EMU_PER_INCH / MM_PER_INCH


def mm_to_twips(millimetres: float) -> int:
    """Millimetres to twips, e.g. ``25 mm`` → ``1417``."""
    return round(millimetres / MM_PER_INCH * TWIPS_PER_INCH)


def twips_to_mm(twips: float) -> float:
    """Twips to millimetres, e.g. ``1417`` → ``24.998...``."""
    return twips / TWIPS_PER_INCH * MM_PER_INCH


def twips_to_pt(twips: float) -> float:
    """Twips to points."""
    return twips / 20


def pt_to_half_points(points: float) -> int:
    """Points to ``w:sz`` units, e.g. ``10.5 pt`` → ``21``."""
    return round(points * 2)


def pt_to_eighths(points: float) -> int:
    """Points to border ``w:sz`` units, e.g. ``1.5 pt`` → ``12``."""
    return round(points * 8)


def mm_to_emu(millimetres: float) -> int:
    """Millimetres to EMU, e.g. ``210 mm`` → ``7560000``."""
    return round(millimetres * EMU_PER_MM)


def inches_to_emu(inches: float) -> int:
    """Inches to EMU, e.g. ``6.2 in`` → ``5669280``."""
    return round(inches * EMU_PER_INCH)
