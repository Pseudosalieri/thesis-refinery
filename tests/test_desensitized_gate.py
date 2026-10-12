"""Tests for the desensitisation gate.

The point of these tests is the second one: a gate that has never been observed
failing is not a gate. The planted token is assembled at runtime from fragments
so that this file does not itself contain the string it is testing for.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from check_desensitized import main as gate_main

CLEAN = "A perfectly ordinary sentence about page margins.\n"


def git_repo(tmp_path: Path) -> Path:
    """A throwaway git repository. The gate asks git what it would publish."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    return tmp_path


def test_gate_passes_on_a_clean_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = git_repo(tmp_path)
    (repo / "notes.md").write_text(CLEAN, encoding="utf-8")

    assert gate_main(["--root", str(repo)]) == 0
    assert "PASS" in capsys.readouterr().out


def test_gate_fails_when_a_compound_code_is_planted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo(tmp_path)
    (repo / "notes.md").write_text(CLEAN, encoding="utf-8")
    # Split so this file does not match the pattern it is testing.
    planted = "PT" + "14"
    (repo / "leak.md").write_text(f"compound {planted} was the best one\n", encoding="utf-8")

    assert gate_main(["--root", str(repo)]) == 1

    captured = capsys.readouterr()
    assert "FAIL" in captured.err
    assert "leak.md" in captured.out


def test_gate_fails_on_a_private_absolute_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo(tmp_path)
    # Escaped so the literal never appears in this file.
    windows_path = "D:\\" + "\u5de5\u4f5c\u533a" + "\\somewhere\\thesis.docx"
    (repo / "config.md").write_text(f"source: {windows_path}\n", encoding="utf-8")

    assert gate_main(["--root", str(repo)]) == 1
    assert "private-path" in capsys.readouterr().out

def test_gate_ignores_files_git_would_not_publish(tmp_path: Path) -> None:
    repo = git_repo(tmp_path)
    (repo / "notes.md").write_text(CLEAN, encoding="utf-8")
    (repo / ".gitignore").write_text("_outputs/\n", encoding="utf-8")
    (repo / "_outputs").mkdir()
    planted = "PT" + "14"
    (repo / "_outputs" / "index.md").write_text(f"{planted}\n", encoding="utf-8")

    assert gate_main(["--root", str(repo)]) == 0


def test_gate_reports_an_environment_error_outside_a_git_repo(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        gate_main(["--root", str(tmp_path)])
    assert excinfo.value.code == 4


def test_gate_skips_itself() -> None:
    """The gate's own deny-list necessarily contains the strings it denies."""
    repo_root = Path(__file__).resolve().parent.parent
    gate = repo_root / "tools" / "check_desensitized.py"
    assert gate.is_file()

    result = subprocess.run(
        [sys.executable, str(gate), "--root", str(repo_root), "--quiet"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
