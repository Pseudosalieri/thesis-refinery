"""Process exit codes.

These are part of the command-line contract: a build script can branch on them,
so the numbers must stay stable across releases.

The convention that matters most is the split between :attr:`ExitCode.FAILED` and
:attr:`ExitCode.ENVIRONMENT`. A gate that reports drift and a machine that is
missing Quarto are very different problems, and a CI job usually wants to treat
them differently — retry the second, page someone about the first.
"""

from __future__ import annotations

from enum import IntEnum

__all__ = ["ExitCode"]


class ExitCode(IntEnum):
    """Exit codes returned by :func:`thesis_refinery.cli.main`."""

    OK = 0
    """Everything ran and every gate passed."""

    FAILED = 1
    """A gate failed: the document drifted from what was asked for."""

    USAGE = 2
    """Bad arguments or a malformed configuration file."""

    NOT_IMPLEMENTED = 3
    """The subcommand exists but is not implemented in this build."""

    ENVIRONMENT = 4
    """A required tool, file, or reference document is missing or inconsistent."""
