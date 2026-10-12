"""Bookkeeping: what files exist, what their bytes are, and what changed.

Nothing in this subpackage knows how a thesis is *formatted*. It answers three
questions that are asked before and after every formatting run, and answers them
with names and digests rather than with opinions:

=================  ========================================================
``hashing``        Which paths count, and the SHA-256 of their bytes
``inventory``      A sorted snapshot of a tree: path, size, digest, kind
``manifest``       A committed digest baseline, and the gate that detects drift
``diff``           A paragraph-level diff between two ``.docx`` revisions
=================  ========================================================

The two halves are used at different moments. ``inventory`` and ``diff`` are for
a person reading a report: "what is in this tree", "what did this Word round trip
actually change". ``manifest`` is for a machine deciding whether to fail: the
files that are supposed to be immutable are recorded once, and any later run that
finds different bytes is drift, not a warning.

The predecessor project kept this bookkeeping as three ad-hoc scripts that each
walked the tree their own way and disagreed about which files counted. That is
why the file-set rules live in exactly one module here (``hashing``) while the
verification walk does not share code with the build walk -- see
``manifest.verify``, and ``CONTRIBUTING.md`` for why sharing a walk is a bug.
"""

from __future__ import annotations

__all__ = [
    "diff",
    "hashing",
    "inventory",
    "manifest",
]
