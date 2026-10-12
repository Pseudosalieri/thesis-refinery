"""Findings: what a check concluded, and how the report aggregates them.

Every check in this package returns findings rather than printing or raising.
That separation is what lets the same checks serve three callers: the gate
(whose exit code matters), the human report (which never fails), and the test
suite (which asserts on individual findings).

A check that cannot fail is decoration, so :class:`Status` has exactly one
failing value and the report's :attr:`Report.ok` is defined as "no FAIL". ``WARN``
and ``SKIP`` are deliberately not failures: a warning nobody can act on and a
skip that was not asked for are both noise, and a gate that fails on noise gets
disabled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "Expectations",
    "Finding",
    "Report",
    "Status",
    "failed",
    "passed",
    "skipped",
    "warned",
]


class Status(Enum):
    """The outcome of one check."""

    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    """Worth saying, not worth stopping for."""

    SKIP = "skip"
    """Not applicable here, e.g. a front-matter check with no source document."""


@dataclass(frozen=True)
class Finding:
    """One conclusion from one check."""

    check: str
    """Stable identifier, e.g. ``"geometry.page-size"``. Used to suppress or filter."""

    status: Status
    message: str
    detail: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "check": self.check,
            "status": self.status.value,
            "message": self.message,
            "detail": list(self.detail),
        }


@dataclass(frozen=True)
class Report:
    """Every finding from one run, plus the roll-up."""

    findings: tuple[Finding, ...] = ()
    document: str = ""

    @property
    def ok(self) -> bool:
        """True when nothing failed. Warnings and skips do not make it false."""
        return not any(finding.status is Status.FAIL for finding in self.findings)

    def failures(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.status is Status.FAIL)

    def with_status(self, status: Status) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.status is status)

    def counts(self) -> dict[str, int]:
        counted = {status.value: 0 for status in Status}
        for finding in self.findings:
            counted[finding.status.value] += 1
        return counted

    def extend(self, findings: tuple[Finding, ...]) -> Report:
        return Report(findings=(*self.findings, *findings), document=self.document)

    def as_dict(self) -> dict[str, object]:
        return {
            "document": self.document,
            "ok": self.ok,
            "counts": self.counts(),
            "findings": [finding.as_dict() for finding in self.findings],
        }


def passed(check: str, message: str, detail: tuple[str, ...] = ()) -> Finding:
    return Finding(check=check, status=Status.PASS, message=message, detail=detail)


def failed(check: str, message: str, detail: tuple[str, ...] = ()) -> Finding:
    return Finding(check=check, status=Status.FAIL, message=message, detail=detail)


def warned(check: str, message: str, detail: tuple[str, ...] = ()) -> Finding:
    return Finding(check=check, status=Status.WARN, message=message, detail=detail)


def skipped(check: str, message: str, detail: tuple[str, ...] = ()) -> Finding:
    return Finding(check=check, status=Status.SKIP, message=message, detail=detail)


@dataclass(frozen=True)
class Expectations:
    """What a check is allowed to conclude from, including resolved counts.

    ``None`` means "derive it from the document" — the ``auto`` setting. A check
    receiving ``None`` must compute the count itself and report it, which is how
    the build log ends up saying what the document actually contains instead of
    silently agreeing with a stale number.
    """

    captions: int | None = None
    body_paragraphs: int | None = None
    reference_entries: int | None = None
    sections: int | None = None
    drawings: int | None = None
    extra: dict[str, int | None] = field(default_factory=dict)
