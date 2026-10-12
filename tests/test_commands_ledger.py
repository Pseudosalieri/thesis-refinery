"""Tests for the ledger command.

The headline test is the round trip, because the round trip is what caught the
bug this file exists to prevent: `ledger build` creates the baseline, and a
baseline that includes itself reports its own creation as drift on the very next
`verify`. A fresh manifest verifying as one added file is the symptom, and it is
invisible until somebody runs the two commands in sequence on a clean tree.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from thesis_refinery.commands.ledger import DEFAULT_MANIFEST_NAME, _exclusions, run
from thesis_refinery.exitcodes import ExitCode

MINIMAL_CONFIG = """
project:
  thesis_dir: manuscript
  chapters_dir: manuscript/chapters
  output_dir: manuscript/_outputs
"""


def make_project(root: Path, *, files: dict[str, str] | None = None) -> Path:
    (root / "manuscript" / "chapters").mkdir(parents=True, exist_ok=True)
    (root / "thesis.yml").write_text(MINIMAL_CONFIG, encoding="utf-8")
    (root / "manuscript" / "index.qmd").write_text("# A thesis\n", encoding="utf-8")
    for name, text in (files or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def namespace(project: Path, action: str, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {"action": action, "project": str(project), "manifest": None, "out": None}
    values.update(overrides)
    return argparse.Namespace(**values)


# ---------------------------------------------------------------------------
# Exclusions
# ---------------------------------------------------------------------------


def test_the_baseline_excludes_itself(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    assert _exclusions(root / DEFAULT_MANIFEST_NAME, root) == (DEFAULT_MANIFEST_NAME,)


def test_a_baseline_in_a_subdirectory_excludes_its_relative_path(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    assert _exclusions(root / "docs" / "baseline.json", root) == ("docs/baseline.json",)


def test_a_baseline_outside_the_tree_excludes_nothing(tmp_path: Path) -> None:
    """Nothing to exclude, and an exception is how we know that."""
    root = make_project(tmp_path / "project")
    outside = tmp_path / "elsewhere" / DEFAULT_MANIFEST_NAME
    assert _exclusions(outside, root) == ()


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


def test_build_then_verify_reports_no_drift(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_project(tmp_path, files={"manuscript/cover.docx": "not really a docx\n"})

    assert run(namespace(root, "build")) == ExitCode.OK
    assert (root / DEFAULT_MANIFEST_NAME).is_file()
    capsys.readouterr()

    assert run(namespace(root, "verify")) == ExitCode.OK
    assert "OK" in capsys.readouterr().out


def test_a_changed_file_fails_the_verification(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_project(tmp_path)
    assert run(namespace(root, "build")) == ExitCode.OK

    (root / "manuscript" / "index.qmd").write_text("# A different thesis\n", encoding="utf-8")

    assert run(namespace(root, "verify")) == ExitCode.FAILED
    assert "changed" in capsys.readouterr().out


def test_a_deleted_file_fails_the_verification(tmp_path: Path) -> None:
    root = make_project(tmp_path, files={"manuscript/extra.qmd": "text\n"})
    assert run(namespace(root, "build")) == ExitCode.OK

    (root / "manuscript" / "extra.qmd").unlink()

    assert run(namespace(root, "verify")) == ExitCode.FAILED


def test_an_added_file_fails_the_verification(tmp_path: Path) -> None:
    """An added file is drift too, and that direction is easy to forget.

    A cover page re-exported under a new name arrives as an addition, not as a
    modification.
    """
    root = make_project(tmp_path)
    assert run(namespace(root, "build")) == ExitCode.OK

    (root / "manuscript" / "newly-arrived.docx").write_text("x\n", encoding="utf-8")

    assert run(namespace(root, "verify")) == ExitCode.FAILED


def test_verify_without_a_baseline_is_an_environment_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_project(tmp_path)
    assert run(namespace(root, "verify")) == ExitCode.ENVIRONMENT
    assert "no baseline" in capsys.readouterr().out


def test_an_explicit_baseline_path_is_respected(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    elsewhere = root / "docs" / "baseline.json"

    assert run(namespace(root, "build", manifest=str(elsewhere))) == ExitCode.OK
    assert elsewhere.is_file()
    assert not (root / DEFAULT_MANIFEST_NAME).exists()
    assert run(namespace(root, "verify", manifest=str(elsewhere))) == ExitCode.OK


def test_output_directory_is_not_part_of_the_baseline(tmp_path: Path) -> None:
    """Build products change on every run; a baseline that covered them is noise."""
    root = make_project(tmp_path)
    assert run(namespace(root, "build")) == ExitCode.OK

    outputs = root / "manuscript" / "_outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "index-final.docx").write_text("a build product\n", encoding="utf-8")

    assert run(namespace(root, "verify")) == ExitCode.OK


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def test_inventory_writes_a_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_project(tmp_path)
    destination = root / "docs" / "INVENTORY.md"

    assert run(namespace(root, "inventory", out=str(destination))) == ExitCode.OK

    assert destination.is_file()
    assert "thesis.yml" in destination.read_text(encoding="utf-8")
    assert "inventory written to" in capsys.readouterr().out


def test_a_missing_project_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(namespace(tmp_path, "inventory")) == ExitCode.USAGE
    assert "thesis.yml" in capsys.readouterr().out
