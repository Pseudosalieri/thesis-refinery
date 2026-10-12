"""The human report: same checks, no exit code.

Separate from :mod:`thesis_refinery.verify.gate` on purpose, and the separation is
the whole point.

The predecessor had a single script that printed a format report and always
exited 0. Because it looked like a check, nobody noticed for two months that
nothing was gating anything. So here the two are different commands with
different contracts:

* ``violations`` (this module) prints everything, including warnings and skips,
  and **always exits 0**. It is for a person reading output.
* ``verify`` (``gate.py``) prints failures first and **exits non-zero** when a
  check fails. It is for a build script.

Neither calls the other. If you are tempted to make this one fail when the gate
fails, that is the mistake described above, arriving again.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from thesis_refinery.config import ConfigError, load
from thesis_refinery.docxlib.save import atomic_write_bytes
from thesis_refinery.exitcodes import ExitCode
from thesis_refinery.verify.checks import all_checks
from thesis_refinery.verify.gate import evaluate
from thesis_refinery.verify.report import render_json, render_text

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="thesis-refinery audit",
        description=(
            "Print a format report for a document. Never fails: use `verify` when you "
            "want a non-zero exit code."
        ),
    )
    parser.add_argument("--docx", metavar="PATH", help="document to report on (default: the configured product)")
    parser.add_argument("--project-dir", "-C", metavar="DIR", help="project root containing thesis.yml")
    parser.add_argument("--prefix", metavar="PATH", help="cover source to compare the front matter against")
    parser.add_argument("--json", action="store_true", help="emit findings as JSON")
    parser.add_argument("--failures-only", action="store_true", help="omit passing checks")
    parser.add_argument("--out", metavar="PATH", help="also write the report to this file")
    args = parser.parse_args(argv)

    try:
        config = load(args.project_dir)
    except ConfigError as exc:
        print(f"thesis-refinery audit: {exc}")
        return ExitCode.USAGE

    document = Path(args.docx) if args.docx else config.paths.final_docx
    if not document.is_file():
        print(f"thesis-refinery audit: no such document: {document}")
        return ExitCode.ENVIRONMENT

    report = evaluate(
        document,
        config=config,
        prefix_source=Path(args.prefix) if args.prefix else None,
    )

    text = render_json(report) if args.json else _render(report, failures_only=args.failures_only)
    print(text)

    if args.out:
        atomic_write_bytes(Path(args.out), text.encode("utf-8"))
        print(f"\nwritten to {args.out}")

    # An audit reports; it does not judge. The count of checks that ran is
    # printed so that a report which examined nothing is visibly empty rather
    # than apparently clean.
    print(f"\n{len(all_checks())} check(s) ran.")
    return ExitCode.OK


def _render(report, *, failures_only: bool) -> str:
    """The same renderer the gate uses, with passing checks included by default.

    An audit is read by a person who wants the whole picture; a gate is read by a
    build script that only cares whether anything failed.
    """
    return render_text(report, verbose=not failures_only)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
