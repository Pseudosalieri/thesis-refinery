"""Command-line interface.

The subcommand table below is the whole public surface of the tool. Each
subcommand names a ``module:function`` target that is imported lazily, so that
``thesis-refinery --help`` stays fast and does not drag in ``lxml``.

Every handler takes the parsed :class:`argparse.Namespace` and returns an
:class:`~thesis_refinery.exitcodes.ExitCode` (or an ``int``).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from importlib import import_module
from typing import NamedTuple

from thesis_refinery import PROGRAM, __version__
from thesis_refinery.exitcodes import ExitCode

__all__ = ["SUBCOMMANDS", "build_parser", "main"]

_OWN_ROOT = __name__.partition(".")[0]


class Command(NamedTuple):
    """One row of the subcommand table."""

    name: str
    summary: str
    target: str
    add_arguments: Callable[[argparse.ArgumentParser], None]


# ---------------------------------------------------------------------------
# Per-subcommand arguments
# ---------------------------------------------------------------------------
# These deliberately duplicate the argument definitions inside each command's own
# module. The duplication buys a fast `--help`: building the parser must not
# import lxml, which a lazy target would otherwise force. A test asserts that
# every attribute a handler reads is one the CLI actually sets, because
# duplicated definitions that are not checked are duplicated definitions that
# drift.
#
# `-C/--project-dir` is declared on the top-level parser *and* here, with
# `default=SUPPRESS` so that the subparser does not overwrite a value the user
# passed before the subcommand. argparse applies subparser defaults to the shared
# namespace, so without SUPPRESS `thesis-refinery -C x verify` would silently
# lose the `x`.


def _add_init_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("dest", nargs="?", metavar="DEST", default="my-thesis",
                   help="directory to create (default: ./my-thesis)")
    p.add_argument("--force", action="store_true", help="write into a non-empty DEST anyway")
    p.add_argument("--title", metavar="TEXT", help="thesis title to put in index.qmd")


def _add_render_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("project", nargs="?", metavar="PROJECT_DIR", help="project root (default: search upwards)")
    p.add_argument("--quarto", metavar="PATH", help="Quarto executable (default: resolve from PATH)")
    p.add_argument("--profile", action="append", metavar="NAME", help="enable a domain profile (repeatable)")
    p.add_argument("--all-formats", action="store_true", help="also render PDF (needs a LaTeX engine)")


def _add_build_args(p: argparse.ArgumentParser) -> None:
    _add_render_args(p)
    p.add_argument("--skip-render", action="store_true", help="reuse the existing render product")
    p.add_argument("--no-verify", action="store_true", help="skip the gates (useful while iterating)")


def _add_verify_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--docx", metavar="PATH", help="document to check (default: the configured final product)")
    p.add_argument("--project-dir", "-C", metavar="DIR", default=argparse.SUPPRESS,
                   help="project root containing thesis.yml")
    p.add_argument("--prefix", metavar="PATH", help="also check front-matter fidelity against this source .docx")
    p.add_argument("--json", action="store_true", help="emit findings as JSON on stdout")
    p.add_argument("--verbose", "-v", action="store_true", help="include passing checks in the text report")


def _add_audit_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--docx", metavar="PATH", help="document to report on (default: the configured final product)")
    p.add_argument("--project-dir", "-C", metavar="DIR", default=argparse.SUPPRESS,
                   help="project root containing thesis.yml")
    p.add_argument("--prefix", metavar="PATH", help="cover source to compare the front matter against")
    p.add_argument("--json", action="store_true", help="emit findings as JSON")
    p.add_argument("--failures-only", action="store_true", help="omit passing checks")
    p.add_argument("--out", metavar="PATH", help="tee the report to this file")


def _add_ledger_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("action", choices=("build", "verify", "inventory"),
                   help="build: record digests. verify: compare and fail on any difference. "
                        "inventory: print a human-readable inventory.")
    p.add_argument("project", nargs="?", metavar="PROJECT_DIR", help="project root (default: search upwards)")
    p.add_argument("--manifest", metavar="PATH", help="baseline path (default: <project>/manifest.json)")
    p.add_argument("--out", metavar="PATH", help="write the inventory or the report here")


def _add_diff_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("old", metavar="OLD.docx", help="baseline document")
    p.add_argument("new", metavar="NEW.docx", help="document to compare against the baseline")
    p.add_argument("--out", metavar="PATH", help="write the report here instead of stdout")


def _add_extract_prefix_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--source", metavar="PATH", required=True, help="document to cut the front matter from")
    p.add_argument("--out", metavar="PATH", required=True, help="destination .docx")
    p.add_argument("--pages", type=int, default=5, metavar="N", help="how many leading pages to keep (default: 5)")
    p.add_argument("--quiet", action="store_true", help="print nothing on success")


SUBCOMMANDS: tuple[Command, ...] = (
    Command("init", "scaffold a new thesis project",
            "thesis_refinery.commands.init:run", _add_init_args),
    Command("render", "run the Quarto render step only",
            "thesis_refinery.pipeline:run_render", _add_render_args),
    Command("build", "render, apply the style, and run the gates",
            "thesis_refinery.pipeline:run_build", _add_build_args),
    Command("verify", "run the gates; non-zero exit when the document drifts",
            "thesis_refinery.verify.gate:run", _add_verify_args),
    Command("audit", "print a human-readable format report; never fails",
            "thesis_refinery.verify.audit:run", _add_audit_args),
    Command("ledger", "build or verify the digest baseline of tracked files",
            "thesis_refinery.commands.ledger:run", _add_ledger_args),
    Command("diff", "diff two .docx versions paragraph by paragraph",
            "thesis_refinery.commands.diff:run", _add_diff_args),
    Command("extract-prefix", "cut the leading N pages out of a .docx as a front-matter source",
            "thesis_refinery.commands.prefix:run", _add_extract_prefix_args),
)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser. Exposed separately so tests can introspect it."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Turn an already-written Word thesis into a reproducible build artifact.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The markdown sources are the source of truth; the .docx is a build product.\n"
            "`verify` is the part that matters: it fails the build when the document drifts.\n"
            "\n"
            "Exit codes: 0 ok, 1 gate failed, 2 bad usage, 3 not implemented, 4 environment."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {__version__}")
    parser.add_argument(
        "-C", "--project-dir", metavar="DIR", default=None,
        help="project root containing thesis.yml (default: search upwards from the current directory)",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    for command in SUBCOMMANDS:
        sub = subparsers.add_parser(
            command.name,
            help=command.summary,
            description=command.summary,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        command.add_arguments(sub)
        sub.set_defaults(_command=command)

    return parser


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _is_our_own_missing_module(exc: ModuleNotFoundError) -> bool:
    """True when the *missing* module belongs to this package.

    That means the target is a declared-but-unbuilt part of the interface. A
    missing *third-party* dependency imported from inside one of our modules
    reports a different ``exc.name`` and must propagate instead: that is a broken
    installation, not an unimplemented feature.
    """
    missing = exc.name or ""
    return missing == _OWN_ROOT or missing.startswith(f"{_OWN_ROOT}.")


def _dispatch(command: Command, args: argparse.Namespace) -> int:
    """Import and call the handler named by ``command.target``."""
    module_name, _, func_name = command.target.partition(":")
    try:
        module = import_module(module_name)
    except ModuleNotFoundError as exc:
        if not _is_our_own_missing_module(exc):
            raise
        print(
            f"{PROGRAM}: '{command.name}' is declared but not implemented in this build",
            file=sys.stderr,
        )
        return ExitCode.NOT_IMPLEMENTED

    handler: Callable[[argparse.Namespace], int] = getattr(module, func_name)
    return int(handler(args))


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    command: Command | None = getattr(args, "_command", None)
    if command is None:
        parser.print_help()
        return ExitCode.USAGE

    return _dispatch(command, args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
