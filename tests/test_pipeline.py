"""Tests for the build orchestrator.

The end-to-end test proves the pipeline works; these prove it fails correctly.
That distinction matters more here than anywhere else in the project, because the
orchestrator is the component whose job is to stop.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from thesis_refinery.config import Checks, Config, ConfigError, Identity, ProjectPaths, Tools
from thesis_refinery.exitcodes import ExitCode
from thesis_refinery.pipeline import (
    STAGES,
    BuildError,
    Stage,
    add_build_arguments,
    add_render_arguments,
    assert_reference_docx_mirror,
    build,
    resolve_quarto,
    run_stage,
    stages,
)
from thesis_refinery.style import DEFAULT_STYLE

MINIMAL_CONFIG = """
project:
  thesis_dir: manuscript
  chapters_dir: manuscript/chapters
  output_dir: manuscript/_outputs
"""


def make_config(root: Path, **overrides: object) -> Config:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manuscript").mkdir(exist_ok=True)
    (root / "thesis.yml").write_text(MINIMAL_CONFIG, encoding="utf-8")
    values: dict[str, object] = {
        "root": root,
        "paths": ProjectPaths(
            root=root,
            thesis_dir=root / "manuscript",
            chapters_dir=root / "manuscript/chapters",
            output_dir=root / "manuscript/_outputs",
        ),
        "identity": Identity(),
        "checks": Checks(),
        "style": DEFAULT_STYLE,
        "tools": Tools(),
    }
    values.update(overrides)
    return Config(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tool resolution
# ---------------------------------------------------------------------------


def test_an_explicit_quarto_path_wins(tmp_path: Path) -> None:
    """Resolution order is explicit > env > configuration > PATH.

    There is deliberately no hard-coded installation path: the project this came
    from pinned one, the pin went stale, a PATH fallback quietly took over, and
    nobody learned for two months that the configured value was dead.
    """
    executable = tmp_path / "quarto.exe"
    executable.write_text("", encoding="utf-8")

    assert resolve_quarto(make_config(tmp_path / "project"), str(executable)) == str(executable)


def test_a_configured_quarto_path_is_used_when_there_is_no_explicit_one(tmp_path: Path) -> None:
    executable = tmp_path / "quarto"
    executable.write_text("", encoding="utf-8")
    config = make_config(tmp_path / "project", tools=Tools(quarto=str(executable)))

    assert resolve_quarto(config) == str(executable)


def test_a_configured_path_that_no_longer_exists_resolves_to_nothing(tmp_path: Path) -> None:
    """A stale pin must not silently become a different program."""
    config = make_config(tmp_path / "project", tools=Tools(quarto=str(tmp_path / "gone")))
    assert resolve_quarto(config) is None


def test_the_environment_variable_is_honoured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "quarto-env"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setenv("QUARTO_EXE", str(executable))

    assert resolve_quarto(make_config(tmp_path / "project")) == str(executable)


# ---------------------------------------------------------------------------
# The style-template mirror
# ---------------------------------------------------------------------------


def test_matching_template_declarations_are_accepted(tmp_path: Path) -> None:
    root = tmp_path / "project"
    config = make_config(root, reference_docx=root / "manuscript" / "reference.docx")
    config.paths.quarto_config.write_text(
        "format:\n  docx:\n    reference-doc: reference.docx\n", encoding="utf-8"
    )

    assert_reference_docx_mirror(config)  # must not raise


def test_disagreeing_template_declarations_stop_the_build(tmp_path: Path) -> None:
    """Quarto reads one file and this project reads the other.

    A documented duplication is a duplication that drifts, so the two are
    resolved and compared rather than described in a comment.
    """
    root = tmp_path / "project"
    config = make_config(root, reference_docx=root / "manuscript" / "reference.docx")
    config.paths.quarto_config.write_text(
        "format:\n  docx:\n    reference-doc: other.docx\n", encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="declared twice"):
        assert_reference_docx_mirror(config)


def test_an_absent_quarto_config_is_not_an_error(tmp_path: Path) -> None:
    """Nothing to disagree with: the render step will say so itself."""
    config = make_config(tmp_path / "project", reference_docx=tmp_path / "reference.docx")
    assert_reference_docx_mirror(config)


def test_an_absolute_template_declaration_is_resolved_as_written(tmp_path: Path) -> None:
    root = tmp_path / "project"
    absolute = tmp_path / "elsewhere" / "reference.docx"
    config = make_config(root, reference_docx=absolute)
    config.paths.quarto_config.write_text(
        f"format:\n  docx:\n    reference-doc: {absolute.as_posix()}\n", encoding="utf-8"
    )

    assert_reference_docx_mirror(config)


# ---------------------------------------------------------------------------
# Failures are reported, not raised
# ---------------------------------------------------------------------------


def test_build_without_a_render_to_reuse_is_an_environment_error(tmp_path: Path) -> None:
    result = build(make_config(tmp_path / "project"), skip_render=True)

    assert not result.ok
    assert result.failed_step == "render"
    assert result.exit_code == ExitCode.ENVIRONMENT


def test_build_without_quarto_is_an_environment_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("QUARTO_EXE", raising=False)
    monkeypatch.setenv("PATH", "")

    result = build(make_config(tmp_path / "project"))

    assert not result.ok
    assert result.failed_step == "render"
    assert result.exit_code == ExitCode.ENVIRONMENT


def test_a_mismatched_mirror_stops_the_build_before_rendering(tmp_path: Path) -> None:
    root = tmp_path / "project"
    config = make_config(root, reference_docx=root / "manuscript" / "reference.docx")
    config.paths.quarto_config.write_text(
        "format:\n  docx:\n    reference-doc: other.docx\n", encoding="utf-8"
    )

    result = build(config)

    assert not result.ok
    assert result.failed_step == "configure"
    assert result.exit_code == ExitCode.ENVIRONMENT
    assert result.steps_run == []


def test_a_missing_stage_is_reported_by_name(tmp_path: Path) -> None:
    """A partial checkout must say which step is absent, not raise from three frames down."""
    stage = Stage(
        name="not-built",
        summary="does not exist",
        module="thesis_refinery.stages.definitely_not_present",
        function="apply",
    )

    with pytest.raises(BuildError) as excinfo:
        run_stage(stage, tmp_path / "in.docx", tmp_path / "out.docx", make_config(tmp_path / "project"))

    assert excinfo.value.code == ExitCode.NOT_IMPLEMENTED
    assert "not-built" in str(excinfo.value)


def test_a_missing_third_party_dependency_propagates(tmp_path: Path) -> None:
    """Three states, not two: an absent stage, an absent dependency, a broken stage.

    An absent *stage* is a partial checkout and is reported by name. An absent
    *dependency* inside a stage is a broken installation and must surface, or the
    build would say "not implemented yet" about a feature that is implemented and
    merely uninstallable.
    """
    stage = Stage(
        name="broken",
        summary="imports something that is not installed",
        module="thesis_refinery.stages.format",
        function="apply",
    )

    def explode(name: str, package: str | None = None):
        raise ModuleNotFoundError("No module named 'openpyxl'", name="openpyxl")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("importlib.import_module", explode)
        with pytest.raises(ModuleNotFoundError, match="openpyxl"):
            run_stage(stage, tmp_path / "in.docx", tmp_path / "out.docx", make_config(tmp_path / "project"))


# ---------------------------------------------------------------------------
# The stage table
# ---------------------------------------------------------------------------


def test_stage_names_are_unique() -> None:
    names = [stage.name for stage in STAGES]
    assert len(names) == len(set(names))


def test_every_stage_is_importable_and_exposes_apply() -> None:
    """A stage in the table that cannot be imported is a build that always fails."""
    from importlib import import_module

    for stage in STAGES:
        module = import_module(stage.module)
        assert callable(getattr(module, stage.function)), stage.name


def test_the_stages_helper_yields_the_table() -> None:
    assert list(stages()) == list(STAGES)


def test_the_product_path_is_named_after_no_stage() -> None:
    """The last stage writes `index-final.docx`, so no stage may be called "final".

    A stage named `final` would collide with the documented product path, and the
    collision would be silent: the stage's own output and the product would be the
    same file, so a later stage's input would be its own output.
    """
    assert all(stage.name != "final" for stage in STAGES)


# ---------------------------------------------------------------------------
# Argument contract
# ---------------------------------------------------------------------------


def test_build_declares_every_render_option_plus_its_own() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    add_build_arguments(parser)
    options = {option for action in parser._actions for option in action.option_strings}

    render_parser = argparse.ArgumentParser(add_help=False)
    add_render_arguments(render_parser)
    render_options = {option for action in render_parser._actions for option in action.option_strings}

    assert render_options <= options
    assert {"--skip-render", "--no-verify"} <= options
