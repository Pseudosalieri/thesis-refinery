"""thesis-refinery — turn an already-written thesis into a reproducible build artifact.

The package is organised in four layers, from bottom to top:

``docxlib``
    Shared OOXML primitives. Nothing in here knows what a thesis is.
``style`` / ``config``
    The single style specification and the project configuration that overrides it.
``stages`` / ``ledger``
    Pipeline stages that read one document and write a new one, plus bookkeeping tools.
``verify``
    Gates that read a document and refuse to lie about it.

Domain-specific behaviour lives in ``profiles`` and is opt-in.
"""

from __future__ import annotations

__all__ = ["PROGRAM", "__version__"]

PROGRAM = "thesis-refinery"

try:  # pragma: no cover - depends on installation state
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _version

    try:
        __version__ = _version(PROGRAM)
    except PackageNotFoundError:
        __version__ = "0.0.0+unknown"
except ImportError:  # pragma: no cover - Python < 3.8 is not supported anyway
    __version__ = "0.0.0+unknown"
