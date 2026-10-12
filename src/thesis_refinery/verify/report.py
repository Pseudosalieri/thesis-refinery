"""Rendering a report.

Two renderers, for two different readers.

:func:`render_text` is for a human reading a build log. It leads with the
failures, because a wall of PASS lines above a failure is a failure nobody
scrolls to.

:func:`render_json` is for a caller that wants to decide for itself — a CI job
that posts a comment, or a script that wants the count of drawings.

Neither renderer decides anything. An earlier version of this pipeline had an
"audit" script that was just this file with an implicit ``exit 0``, and it was
mistaken for a gate for two months.
"""

from __future__ import annotations

import json

from thesis_refinery.verify.findings import Finding, Report, Status

__all__ = ["render_json", "render_text"]

_MARKERS = {
    Status.PASS: "PASS",
    Status.FAIL: "FAIL",
    Status.WARN: "WARN",
    Status.SKIP: "SKIP",
}


def render_text(report: Report, *, verbose: bool = False) -> str:
    """A human-readable report: failures first, then everything else."""
    lines: list[str] = []
    if report.document:
        lines.append(f"document: {report.document}")

    for finding in report.failures():
        lines.append(f"  {_MARKERS[finding.status]}  {finding.check}: {finding.message}")
        for item in finding.detail:
            lines.append(f"        {item}")

    for status in (Status.WARN, Status.SKIP):
        for finding in report.with_status(status):
            lines.append(f"  {_MARKERS[status]}  {finding.check}: {finding.message}")
            for item in finding.detail:
                lines.append(f"        {item}")

    if verbose:
        for finding in report.with_status(Status.PASS):
            lines.append(f"  {_MARKERS[Status.PASS]}  {finding.check}: {finding.message}")

    counts = report.counts()
    summary = ", ".join(f"{status.value} {counts[status.value]}" for status in Status)
    lines.append("")
    lines.append(f"{'PASS' if report.ok else 'FAIL'}: {summary}")
    return "\n".join(lines)


def render_json(report: Report) -> str:
    return json.dumps(report.as_dict(), indent=2, sort_keys=False, ensure_ascii=False)


def render_findings(findings: tuple[Finding, ...]) -> str:
    """Render findings that have no enclosing report, for tests and debugging."""
    return render_text(Report(findings=findings))
