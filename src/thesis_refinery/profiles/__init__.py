"""Optional domain profiles: the part of a build that only some theses need.

A profile is opt-in twice over. It is named in ``thesis.yml`` (``profiles: []``
by default), and it may want a dependency the base install does not carry — the
``chemistry`` profile reads a workbook and therefore needs ``openpyxl``. Neither
:mod:`thesis_refinery.pipeline` nor :mod:`thesis_refinery.docxlib` imports
anything from here, and nothing here may be imported while a core module is being
imported: an optional dependency that creeps into an import path stops being
optional, and becomes a crash on a machine that installed the base package and
never asked for the extra.

What a profile is *not* is a place for a formatting fact. Anything a conforming
thesis must be able to state in a specification belongs in
:mod:`thesis_refinery.style`, where the code that applies it and the code that
checks it both read it. A profile is for work that exists only because of a
discipline: an appendix of instrument output, a notation a piece of software
reports its results in, a figure-caption shape one faculty requires and another
forbids.

:data:`PROFILES` maps a profile name to a module path as a **string** on purpose.
Importing each candidate module to find out whether it exists would make this
registry the very thing that drags the optional dependency into an import — the
failure mode it exists to prevent.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

__all__ = ["PROFILES", "available", "load"]

PROFILES: dict[str, str] = {
    "chemistry": "thesis_refinery.profiles.chemistry",
}
"""Every profile this project ships, keyed by the name ``thesis.yml`` names it by."""


def available() -> tuple[str, ...]:
    """The profile names, sorted, as a command-line surface would list them."""
    return tuple(sorted(PROFILES))


def load(name: str) -> ModuleType:
    """Import the profile *name* and return its module.

    Raises :class:`ValueError` for a name that is not in :data:`PROFILES`. The
    error names the profiles that do exist, because a mistyped ``profiles:``
    entry in ``thesis.yml`` is otherwise a build that quietly does less than its
    author believes: the appendix step would simply not be part of the pipeline,
    and every later assertion would agree with the document it produced.
    """
    module_path = PROFILES.get(name)
    if module_path is None:
        raise ValueError(f"unknown profile {name!r}; known: {', '.join(available())}")
    return import_module(module_path)
