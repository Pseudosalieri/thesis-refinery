"""Shared OOXML primitives.

Nothing in this subpackage knows what a thesis is. It exists so that the rest of
the project has exactly one way to do each of these things:

===============  ==========================================================
``ns``           Namespaces, prefixed names, relationship types
``parse``        The single, hardened XML parser and byte-exact serialisation
``xmlq``         Querying and building elements without magic strings
``pkgs``         OPC package (zip) surgery, byte-preserving
``save``         Copy-before-write, atomic replace, refuse to clobber tracked files
``drawings``     Detecting and sizing inline/anchor drawings
``tables``       Table and three-line-border helpers
``fields``       Field codes (STYLEREF, NUMPAGES, TOC) and ``updateFields``
===============  ==========================================================

The pipeline this project was extracted from had eight copies of the same
namespace literal and five copies of the same ``w()``/``qn()`` helper, which is
precisely why this subpackage exists.
"""

from __future__ import annotations

__all__ = ["drawings", "fields", "ns", "parse", "pkgs", "save", "tables", "xmlq"]
