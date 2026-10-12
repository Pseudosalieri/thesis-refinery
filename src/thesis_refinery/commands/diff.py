"""``thesis-refinery diff``: compare two versions of a document, paragraph by paragraph.

This exists because of the one workflow the pipeline cannot automate: the author
edits the thesis in Word. That is a legitimate channel, but its output is a file
with no history, and the differences have to be found and folded back into
markdown by hand. A paragraph-level diff is what makes that tractable, and the
alternative — reading two 90-page documents side by side — is what makes people
give up and let the markdown drift.

The report is a report. It never fails: a diff that found differences has done
its job, not failed at it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from thesis_refinery.docxlib.save import atomic_write_bytes
from thesis_refinery.exitcodes import ExitCode
from thesis_refinery.ledger import diff as diff_module

__all__ = ["add_arguments", "run"]


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("old", metavar="OLD.docx", help="baseline document")
    parser.add_argument("new", metavar="NEW.docx", help="document to compare against the baseline")
    parser.add_argument("--out", metavar="PATH", help="write the report here instead of stdout")


def run(args: argparse.Namespace) -> int:
    old, new = Path(args.old), Path(args.new)
    for path in (old, new):
        if not path.is_file():
            print(f"thesis-refinery diff: no such document: {path}")
            return ExitCode.ENVIRONMENT

    try:
        report = diff_module.diff(old, new)
    except (KeyError, ValueError) as exc:
        print(f"thesis-refinery diff: cannot read a document: {exc}")
        return ExitCode.ENVIRONMENT

    text = diff_module.render_markdown(report)

    if args.out:
        atomic_write_bytes(Path(args.out), text.encode("utf-8"))
        counts = report.counts()
        print(
            f"{counts.get('added', 0)} added, {counts.get('removed', 0)} removed, "
            f"{counts.get('changed', 0)} changed; written to {args.out}"
        )
    else:
        print(text)

    # Folding the changes back into markdown is the next step, and only the
    # author can do it. Say so rather than leaving the report as a dead end.
    print(
        "\nFold these into the markdown sources before the next build. An unfolded "
        "Word edit is a silent fork: the repository and the submitted file stop "
        "being the same document.",
    )
    return ExitCode.OK
