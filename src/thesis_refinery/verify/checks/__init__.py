"""The checks, one module per area of the document.

A check is a function ``run(context) -> Iterator[Finding]``. It reads the
resolved context, concludes, and yields findings; it never prints and never
raises for a condition it is supposed to detect. That uniformity is what lets the
gate, the human report and the test suite all be built from the same set.

Each module also exposes ``CHECK``, a :class:`Check` naming itself. Names are
stable and dotted (``geometry.page-size``) because they appear in build logs and
in failure output, and somebody will grep for one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from thesis_refinery.verify.context import Context
from thesis_refinery.verify.findings import Finding

__all__ = ["Check", "all_checks", "run_all"]


@dataclass(frozen=True)
class Check:
    """One check, named and described for the report."""

    name: str
    description: str
    run: Callable[[Context], Iterator[Finding]]


def all_checks() -> tuple[Check, ...]:
    """Every registered check, in report order.

    Imported here rather than at module scope so that adding a check is a
    one-line change in one place, and so the import graph stays acyclic.
    """
    from thesis_refinery.verify.checks import (
        body,
        captions,
        frontmatter,
        geometry,
        headers,
        numbering,
        tables,
    )

    return (
        geometry.CHECK,
        numbering.CHECK,
        headers.CHECK,
        body.CHECK,
        captions.CHECK,
        tables.CHECK,
        frontmatter.CHECK,
    )


def run_all(context: Context) -> Iterator[Finding]:
    """Run every check, in order, yielding findings as they arrive."""
    for check in all_checks():
        yield from check.run(context)
