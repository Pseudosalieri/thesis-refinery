"""Tests for `thesis-refinery init`.

The property that matters: what `init` writes must be a project the rest of the
toolchain accepts. A scaffolder whose output does not load is worse than no
scaffolder, because the first thing a new user does is trust it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from thesis_refinery.commands.init import DEFAULT_DESTINATION, run, templates
from thesis_refinery.config import load
from thesis_refinery.exitcodes import ExitCode


def namespace(dest: Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {"dest": str(dest), "force": False, "title": None}
    values.update(overrides)
    return argparse.Namespace(**values)


# ---------------------------------------------------------------------------
# The templates themselves
# ---------------------------------------------------------------------------


def test_the_templates_are_non_empty_and_relative() -> None:
    files = templates()
    assert files
    for relative, text in files.items():
        assert not Path(relative).is_absolute(), relative
        assert text.strip(), relative


def test_the_index_includes_every_chapter_that_is_written() -> None:
    """A scaffolder whose index omits a chapter produces a build that silently drops it."""
    files = templates()
    index = files["manuscript/index.qmd"]

    chapters = {name for name in files if name.startswith("manuscript/chapters/")}
    assert chapters
    for chapter in chapters:
        assert Path(chapter).name in index, chapter


def test_the_index_title_can_be_set() -> None:
    files = templates("A Specific Title")
    assert "A Specific Title" in files["manuscript/index.qmd"]


def test_no_template_uses_an_underscore_for_a_subscript() -> None:
    """The project's notation rule: `_` in markdown is emphasis, not a subscript."""
    for relative, text in templates().items():
        if relative.endswith(".qmd") or relative.endswith(".yml"):
            assert "IC_50" not in text, relative


# ---------------------------------------------------------------------------
# What it writes
# ---------------------------------------------------------------------------


def test_init_writes_a_project_the_configuration_loader_accepts(tmp_path: Path) -> None:
    destination = tmp_path / "thesis"

    assert run(namespace(destination)) == ExitCode.OK

    config = load(destination)
    assert config.paths.thesis_dir == destination / "manuscript"
    assert config.paths.chapters_dir == destination / "manuscript/chapters"


def test_init_writes_every_template(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    destination = tmp_path / "thesis"
    assert run(namespace(destination)) == ExitCode.OK

    for relative in templates():
        assert (destination / relative).is_file(), relative
    assert "wrote" in capsys.readouterr().out


def test_init_says_what_to_do_next(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A scaffolder that does not say what is missing produces a build that fails mysteriously."""
    run(namespace(tmp_path / "thesis"))
    output = capsys.readouterr().out

    assert "identity" in output
    assert "odd_page_text" in output


def test_init_uses_the_default_destination_name() -> None:
    assert DEFAULT_DESTINATION == "my-thesis"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_init_refuses_a_non_empty_destination(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    destination = tmp_path / "thesis"
    destination.mkdir()
    (destination / "something-important.txt").write_text("do not clobber me\n", encoding="utf-8")

    assert run(namespace(destination)) == ExitCode.USAGE
    assert "not empty" in capsys.readouterr().out
    assert (destination / "something-important.txt").read_text(encoding="utf-8") == "do not clobber me\n"


def test_force_writes_into_a_non_empty_destination(tmp_path: Path) -> None:
    destination = tmp_path / "thesis"
    destination.mkdir()
    (destination / "something-important.txt").write_text("kept\n", encoding="utf-8")

    assert run(namespace(destination, force=True)) == ExitCode.OK

    assert (destination / "thesis.yml").is_file()
    # --force overwrites what it owns and leaves everything else alone.
    assert (destination / "something-important.txt").read_text(encoding="utf-8") == "kept\n"


def test_an_empty_existing_destination_needs_no_force(tmp_path: Path) -> None:
    destination = tmp_path / "thesis"
    destination.mkdir()

    assert run(namespace(destination)) == ExitCode.OK


def test_init_is_idempotent_under_force(tmp_path: Path) -> None:
    destination = tmp_path / "thesis"

    run(namespace(destination))
    first = (destination / "thesis.yml").read_text(encoding="utf-8")
    run(namespace(destination, force=True))

    assert (destination / "thesis.yml").read_text(encoding="utf-8") == first
