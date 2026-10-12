"""The chemistry profile: numeric-instrument theses, opt-in and self-contained.

Two modules, each answering one question that only a numeric thesis asks:

``captions``
    The run shape of a figure caption: ``Fig. 3`` bold, the ``-1`` ordinal (and
    the ``1`` of ``1H-NMR``) superscript, the rest in the caption style's font.
``subscripts``
    Terms a modelling package reports, such as ``ΔG_Bind``, with the descriptor
    set as a subscript rather than left running inline.

A third module is planned and **not present**: ``spectra``, an appendix of
instrument output driven by an author-supplied manifest. It is named here rather
than omitted because a gap that is written down is a gap somebody can fill, and
because it is the reason this profile exists at all. It is deliberately *not* in
``__all__``: a name in ``__all__`` is a promise, and a promise that raises
``ModuleNotFoundError`` on first access is worse than an acknowledged absence.

Nothing here is imported by the core pipeline, and nothing here imports
:mod:`thesis_refinery.stages`, :mod:`thesis_refinery.verify` or
:mod:`thesis_refinery.ledger`: a profile may *use* the document primitives, but a
pipeline that cannot be built without a discipline's extra is not optional.
Every function in this package takes the document primitives it needs as
arguments — an element to modify, a callable that finds an image — so a thesis
project can wire the profile into its own build order rather than inheriting one.

The modules are re-exported lazily. ``import thesis_refinery.profiles.chemistry``
is therefore cheap and pulls in no XML machinery until a caller asks for a
specific piece of it.
"""

from __future__ import annotations

from importlib import import_module

__all__ = ["captions", "subscripts"]


def __getattr__(name: str):
    """Import a submodule on first access, so the package import stays cheap."""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
