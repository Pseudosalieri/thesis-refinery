#!/usr/bin/env python3
"""Desensitisation gate.

This repository is a generalisation of a private thesis pipeline. Nothing that
identifies the original thesis, its author, its institution, or the machine it
was written on may appear in a tracked file.

The gate is deliberately dumb and deliberately strict: a deny-list of regular
expressions applied to every file git would publish. It runs in CI on every
push, and locally as ``python tools/check_desensitized.py``.

Scope is ``git ls-files --cached --others --exclude-standard``, i.e. exactly the
set of files that would end up in a clone. Ignored build products are not
scanned, because scanning them would make the gate fail for files nobody can
download anyway.

Two rules keep the gate honest:

* It skips **itself**. A deny-list necessarily contains the strings it denies.
* It skips files it cannot decode as UTF-8. Those are binaries; the deny-list is
  about text. Binary assets are excluded by policy, not by this script.

Exit codes: 0 clean, 1 hits found, 4 could not determine the file list.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# The deny-list. Grouped by what leaks, not by where it leaks.
# --------------------------------------------------------------------------
DENY: tuple[tuple[str, str], ...] = (
    # -- names of people ---------------------------------------------------
    ("person", r"高宏曜"),
    ("person", r"吴慧南"),
    ("person", r"翟鑫"),
    ("person", r"刘亚婧"),
    ("person", r"类红瑞"),
    ("person", r"周心怡"),
    ("person", r"202020494"),
    # -- institutions ------------------------------------------------------
    ("institution", r"沈阳药科大学"),
    ("institution", r"制药工程学院"),
    # -- the source thesis: title, topic, target --------------------------
    ("thesis-title", r"分子杂合"),
    ("thesis-title", r"吲哚"),
    ("thesis-title", r"构效关系"),
    ("thesis-topic", r"\bATX\b"),
    ("thesis-topic", r"\bENPP\w*\b"),
    ("thesis-topic", r"lysoPLD"),
    ("thesis-topic", r"\bLPA\b"),
    # -- compound / label codes invented for the source thesis -------------
    ("code", r"\bPT\d{1,2}\b"),
    ("code", r"\bFS-3\b"),
    ("code", r"\bEN-004\b"),
    ("code", r"\bL-3000\b"),
    ("code", r"\bFTP-198\b"),
    ("code", r"\bLHR-\d+\b"),
    # -- the private machine and the private repositories ------------------
    ("private-path", r"工作区"),
    ("private-path", r"ThirdPartySoftwares"),
    ("private-repo", r"ARIADNETICS"),
    ("private-repo", r"ThesisRefinery"),
    ("private-repo", r"ThesisFormatting"),
    ("private-repo", r"molecule docking"),
    ("private-repo", r"_chaotic"),
    # -- file names from the source thesis's data set ----------------------
    ("private-data", r"化合物映射表"),
    ("private-data", r"氢谱数据整理"),
    ("private-data", r"毕业--"),
)

SELF = Path(__file__).resolve()


def candidate_files(root: Path) -> list[Path]:
    """Files git would publish, relative to *root*."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"check_desensitized: cannot list files via git: {exc}", file=sys.stderr)
        raise SystemExit(4) from exc

    names = [n for n in out.stdout.decode("utf-8", "surrogateescape").split("\0") if n]
    return [root / n for n in names if (root / n).is_file()]


def scan(path: Path, patterns: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    """Return ``line: category: excerpt`` strings for every hit in *path*."""
    if path.resolve() == SELF:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []

    hits: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for category, pattern in patterns:
            match = pattern.search(line)
            if match:
                hits.append(f"{lineno}: {category}: {match.group(0)!r}")
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail if anything identifying leaked into the repo.")
    parser.add_argument("--root", default=".", help="repository root (default: the current directory)")
    parser.add_argument("--quiet", action="store_true", help="only print the summary line")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    patterns = [(category, re.compile(pattern)) for category, pattern in DENY]

    files = candidate_files(root)
    if not files:
        print("check_desensitized: no files to scan", file=sys.stderr)
        return 0

    total_hits = 0
    flagged_files = 0
    for path in sorted(files):
        hits = scan(path, patterns)
        if not hits:
            continue
        flagged_files += 1
        total_hits += len(hits)
        if not args.quiet:
            rel = path.relative_to(root)
            print(f"\n{rel}")
            for hit in hits:
                print(f"  L{hit}")

    if total_hits:
        print(
            f"\nFAIL: {total_hits} hit(s) in {flagged_files} file(s) out of {len(files)} scanned.",
            file=sys.stderr,
        )
        print("Nothing that identifies the source thesis may be published.", file=sys.stderr)
        return 1

    print(f"PASS: {len(files)} file(s) scanned, 0 hits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
