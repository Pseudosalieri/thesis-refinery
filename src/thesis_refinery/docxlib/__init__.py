"""Shared OOXML primitives.

Nothing in this subpackage knows what a thesis is. It exists so that the rest of
the project has exactly one way to do each of these things:

=================  ========================================================
``ns``             Namespaces, prefixed names, relationship and content types
``parse``          The one hardened XML parser, and byte-faithful serialisation
``units``          twips, half-points, eighth-points, EMU
``xmlq``           Querying and building elements, and the schema child order
``pkgs``           OPC package (zip) surgery, byte-preserving per part
``save``           Copy-before-write, atomic replace, tracked-file guard
``synth``          Building valid packages from scratch
``drawings``       Finding, measuring and resizing pictures
``tables``         Tables, three-line borders, and rule lines generally
``fields``         Field codes and ``w:updateFields``
=================  ========================================================

The problem is the opposite of exotic: the same namespace literal, the same
``w()``/``qn()`` helper, and the same "does this paragraph contain a picture"
question, each written out again and again. Consolidating them is why this
subpackage exists.
"""

from __future__ import annotations

__all__ = [
    "drawings",
    "fields",
    "ns",
    "parse",
    "pkgs",
    "save",
    "synth",
    "tables",
    "units",
    "xmlq",
]
