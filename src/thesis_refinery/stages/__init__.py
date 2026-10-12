"""Pipeline stages: one transformation per file, one document in and one out.

Every stage in this package obeys the same three rules, and they are the reason a
build failure is diagnosable rather than mysterious:

* it **reads** one document and **writes a new one** — never in place. A stage
  that broke something left the input it broke still on disk, and "which stage did
  this?" always has exactly one candidate answer.
* every formatting value comes from :mod:`thesis_refinery.style`. A millimetre
  value typed into a stage is a second copy of a fact that already exists, and the
  predecessor project's two copies of twenty-one facts is what this package is
  built to prevent.
* it changes only the parts it means to change. Writes go through
  :class:`~thesis_refinery.docxlib.pkgs.Package`, whose copy-on-write parts make
  "this stage touched nothing else" an assertion a test can make instead of a
  promise a reader has to believe.

============================  ==================================================
``format``                    page setup, numbering, running heads, three-line tables
============================  ==================================================

Submodules are deliberately **not** imported here: ``thesis-refinery --help`` must
stay fast, and importing a stage pulls in ``lxml``. Import the stage you need —
``from thesis_refinery.stages import format``.
"""

from __future__ import annotations

__all__ = ["format"]
