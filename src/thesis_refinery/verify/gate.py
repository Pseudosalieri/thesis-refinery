"""The gate: run every check and decide the exit code.

This module is the only place in the verification package that returns an exit
code, and it is separate from the human report on purpose. The predecessor had a
single script that printed a format report and always exited 0, and because it
looked like a check, nobody noticed that nothing was gating anything. Reports
here never fail; :func:`evaluate` never prints; only :func:`main` decides.

Exit codes follow :class:`~thesis_refinery.exitcodes.ExitCode`: a document that
drifts is ``FAILED`` (1), while a missing file or an unreadable configuration is
``ENVIRONMENT`` (4). A CI job usually wants to treat those differently — retry
the second, read the first.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from thesis_refinery.config import Config, ConfigError, load
from thesis_refinery.docxlib.save import TrackedFileError
from thesis_refinery.exitcodes import ExitCode
from thesis_refinery.verify.checks import all_checks, run_all
from thesis_refinery.verify.context import open_document
from thesis_refinery.verify.findings import Report
from thesis_refinery.verify.report import render_json, render_text

__all__ = ["add_arguments", "evaluate", "main", "run"]


def evaluate(
    document: Path,
    *,
    config: Config,
    prefix_source: Path | None = None,
) -> Report:
    """Run every check against *document* and return the report.

    Does not raise for a document that fails a check — that is what a report is
    for. Raises only for a document that cannot be read at all.
    """
    document = Path(document)
    source = prefix_source if prefix_source is not None else config.frontmatter_docx

    with open_document(
        document,
        style=config.style,
        identity=config.identity,
        checks=config.checks,
        prefix_source=source,
    ) as context:
        findings = tuple(run_all(context))

    return Report(findings=findings, document=str(document))


# ---------------------------------------------------------------------------
# Command-line entry points
# ---------------------------------------------------------------------------
# The argument names below are a contract with ``cli.SUBCOMMANDS``. The top-level
# CLI builds its own subparser for each command — so that `--help` stays fast and
# does not import lxml — and hands the resulting namespace to `run`. A test
# asserts that the two agree, because duplicated argument definitions that are
# not checked are duplicated argument definitions that drift.


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--docx", metavar="PATH", help="document to check (default: the configured product)"
    )
    parser.add_argument(
        "--project-dir", "-C", metavar="DIR", help="project root containing thesis.yml"
    )
    parser.add_argument(
        "--prefix", metavar="PATH", help="cover source to compare the front matter against"
    )
    parser.add_argument("--json", action="store_true", help="emit findings as JSON")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="include passing checks in the text report"
    )


def run(args: argparse.Namespace) -> int:
    """Run the gate for an already-parsed namespace."""
    try:
        config = load(args.project_dir)
    except ConfigError as exc:
        print(f"thesis-refinery verify: {exc}")
        return ExitCode.USAGE

    document = Path(args.docx) if args.docx else config.paths.final_docx
    if not document.is_file():
        print(f"thesis-refinery verify: no such document: {document}")
        return ExitCode.ENVIRONMENT

    try:
        report = evaluate(
            document,
            config=config,
            prefix_source=Path(args.prefix) if args.prefix else None,
        )
    except TrackedFileError as exc:
        print(f"thesis-refinery verify: {exc}")
        return ExitCode.ENVIRONMENT

    print(render_json(report) if args.json else render_text(report, verbose=args.verbose))

    if not report.ok:
        print()
        print(f"{len(report.failures())} check(s) failed. {len(all_checks())} check(s) ran.")
    return ExitCode.OK if report.ok else ExitCode.FAILED


def main(argv: Sequence[str] | None = None) -> int:
    """Standalone entry point, for ``python -m thesis_refinery.verify.gate``."""
    parser = argparse.ArgumentParser(
        prog="thesis-refinery verify",
        description="Run the gates. Non-zero exit when the document drifts.",
    )
    add_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
