"""Verification: the gates.

The package is organised so that the part that *decides* is separate from the
part that *reports*, and the part that *resolves* is separate from both:

``findings``
    What a check concluded, and the aggregation. Nothing here does I/O.
``styles``
    Effective formatting: direct formatting over style over inherited style.
    Without this, a checker either reports every paragraph as unformatted or
    cannot see the one paragraph somebody overrode by hand.
``context``
    A document plus the configuration it is being tested against, resolved once.
``checks``
    One module per area. A check returns findings and never prints or raises.
``report``
    Two renderers, for a human and for a machine.
``gate``
    The only place that returns an exit code.

The rule that keeps this honest: **the applier and the checker share the
specification, never the traversal.** Both read ``thesis_refinery.style``; each
walks the document its own way. If they shared a walk, a bug in the walk would
cancel itself out and a broken document would pass.
"""

from __future__ import annotations

__all__ = [
    "all_checks",
    "evaluate",
    "findings",
    "gate",
    "report",
    "run_all",
    "styles",
]


def __getattr__(name: str):
    """Lazy re-exports, so importing the package does not pull in lxml."""
    if name in ("all_checks", "run_all"):
        from thesis_refinery.verify.checks import all_checks, run_all

        return {"all_checks": all_checks, "run_all": run_all}[name]
    if name == "evaluate":
        from thesis_refinery.verify.gate import evaluate

        return evaluate
    if name in ("findings", "gate", "report", "styles"):
        from importlib import import_module

        return import_module(f"thesis_refinery.verify.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
