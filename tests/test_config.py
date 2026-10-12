"""Tests for project configuration.

The theme throughout: a configuration problem must be reported, never absorbed.
Every test here corresponds to a way the predecessor silently did the wrong
thing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from thesis_refinery.config import (
    AUTO,
    CONFIG_FILENAME,
    Checks,
    ConfigError,
    find_project_root,
    load,
)


def write_config(directory: Path, text: str) -> Path:
    (directory / CONFIG_FILENAME).write_text(text, encoding="utf-8")
    return directory


MINIMAL = """
project:
  thesis_dir: manuscript
  chapters_dir: manuscript/chapters
  output_dir: manuscript/_outputs
"""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_find_project_root_walks_upwards(tmp_path: Path) -> None:
    write_config(tmp_path, MINIMAL)
    deep = tmp_path / "manuscript" / "chapters"
    deep.mkdir(parents=True)

    assert find_project_root(deep) == tmp_path
    assert find_project_root(tmp_path / "manuscript") == tmp_path


def test_find_project_root_accepts_a_file(tmp_path: Path) -> None:
    write_config(tmp_path, MINIMAL)
    assert find_project_root(tmp_path / "manuscript" / "index.qmd") == tmp_path


def test_find_project_root_raises_when_there_is_none(tmp_path: Path) -> None:
    """Guessing at "the current directory" would operate on the wrong project."""
    with pytest.raises(ConfigError, match=r"no thesis\.yml found"):
        find_project_root(tmp_path)


def test_load_accepts_a_directory_a_file_and_nothing(tmp_path: Path, monkeypatch) -> None:
    write_config(tmp_path, MINIMAL)

    assert load(tmp_path).root == tmp_path
    assert load(tmp_path / CONFIG_FILENAME).root == tmp_path

    monkeypatch.chdir(tmp_path)
    assert load(None).root == tmp_path


def test_load_reports_a_directory_without_a_config(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not contain"):
        load(tmp_path)


def test_load_reports_a_path_that_does_not_exist(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="neither a directory nor a file"):
        load(tmp_path / "nowhere")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def test_defaults_are_used_when_project_is_absent(tmp_path: Path) -> None:
    write_config(tmp_path, "{}\n")
    config = load(tmp_path)

    assert config.paths.thesis_dir == tmp_path / "manuscript"
    assert config.paths.chapters_dir == tmp_path / "manuscript/chapters"
    assert config.paths.output_dir == tmp_path / "manuscript/_outputs"


def test_paths_are_resolved_against_the_project_root_not_the_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    write_config(tmp_path, MINIMAL)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    config = load(tmp_path)

    assert config.paths.output_dir == tmp_path / "manuscript/_outputs"
    assert config.paths.quarto_config == tmp_path / "manuscript/_quarto.yml"


def test_optional_document_paths_resolve_and_default_to_none(tmp_path: Path) -> None:
    write_config(tmp_path, MINIMAL + "\nreference_docx: manuscript/reference.docx\n")
    config = load(tmp_path)

    assert config.reference_docx == tmp_path / "manuscript/reference.docx"
    assert config.frontmatter_docx is None


# ---------------------------------------------------------------------------
# Unknown keys are errors
# ---------------------------------------------------------------------------


def test_unknown_top_level_key_is_refused(tmp_path: Path) -> None:
    write_config(tmp_path, MINIMAL + "\nstyl: {}\n")

    with pytest.raises(ConfigError, match="unknown key"):
        load(tmp_path)


def test_unknown_project_key_is_refused(tmp_path: Path) -> None:
    write_config(tmp_path, "\nproject:\n  thesis_dirs: manuscript\n")

    with pytest.raises(ConfigError, match="thesis_dirs"):
        load(tmp_path)


def test_unknown_style_section_key_is_refused(tmp_path: Path) -> None:
    write_config(tmp_path, MINIMAL + "\nstyle:\n  margins:\n    top_mm: 25\n    depths_mm: 1\n")

    with pytest.raises(ConfigError, match="depths_mm"):
        load(tmp_path)


def test_unknown_style_section_is_refused(tmp_path: Path) -> None:
    write_config(tmp_path, MINIMAL + "\nstyle:\n  pagee: {width_mm: 210}\n")

    with pytest.raises(ConfigError, match="pagee"):
        load(tmp_path)


def test_unknown_checks_key_is_refused(tmp_path: Path) -> None:
    write_config(tmp_path, MINIMAL + "\nchecks:\n  expect_caption: 10\n")

    with pytest.raises(ConfigError, match="expect_caption"):
        load(tmp_path)


def test_unknown_identity_key_is_refused(tmp_path: Path) -> None:
    write_config(tmp_path, MINIMAL + "\nidentity:\n  requires: [x]\n")

    with pytest.raises(ConfigError, match="requires"):
        load(tmp_path)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def test_checks_default_to_auto(tmp_path: Path) -> None:
    """Hard-coded counts rot silently; deriving them is the default for a reason."""
    write_config(tmp_path, MINIMAL)
    config = load(tmp_path)

    assert config.checks == Checks()
    assert config.checks.expect_captions == AUTO


def test_checks_accept_an_integer_and_an_explicit_auto(tmp_path: Path) -> None:
    write_config(tmp_path, MINIMAL + "\nchecks:\n  expect_captions: 103\n  expect_drawings: auto\n")
    config = load(tmp_path)

    assert config.checks.expect_captions == 103
    assert config.checks.expect_drawings == AUTO


def test_checks_accept_a_numeric_string(tmp_path: Path) -> None:
    write_config(tmp_path, MINIMAL + "\nchecks:\n  expect_sections: '14'\n")
    assert load(tmp_path).checks.expect_sections == 14


@pytest.mark.parametrize("bad", ["many", "1.5", "true"])
def test_a_nonsensical_check_value_is_refused(tmp_path: Path, bad: str) -> None:
    write_config(tmp_path, MINIMAL + f"\nchecks:\n  expect_captions: {bad}\n")
    with pytest.raises(ConfigError, match="expect_captions"):
        load(tmp_path)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_identity_is_empty_by_default(tmp_path: Path) -> None:
    """No built-in default identity: a fallback would verify someone else's thesis."""
    write_config(tmp_path, MINIMAL)
    config = load(tmp_path)

    assert config.identity.required == ()
    assert config.identity.forbidden == ()


def test_identity_lists_are_read(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        MINIMAL + "\nidentity:\n  required: [Example University]\n  forbidden: [Wrong Faculty]\n",
    )
    config = load(tmp_path)

    assert config.identity.required == ("Example University",)
    assert config.identity.forbidden == ("Wrong Faculty",)


def test_a_bare_string_where_a_list_is_expected_is_refused(tmp_path: Path) -> None:
    write_config(tmp_path, MINIMAL + "\nidentity:\n  required: Example University\n")
    with pytest.raises(ConfigError, match="list of strings"):
        load(tmp_path)


# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------


def test_style_defaults_apply_when_the_section_is_absent(tmp_path: Path) -> None:
    write_config(tmp_path, MINIMAL)
    assert load(tmp_path).style.page.width_mm == 210.0


def test_style_sections_override_only_what_they_name(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        MINIMAL + "\nstyle:\n  margins:\n    top_mm: 30\n  page:\n    width_mm: 215.9\n",
    )
    config = load(tmp_path)

    assert config.style.margins.top_mm == 30.0
    assert config.style.margins.bottom_mm == 25.0  # untouched default
    assert config.style.page.width_mm == 215.9
    assert config.style.page.height_mm == 297.0


def test_style_top_level_key_is_accepted(tmp_path: Path) -> None:
    write_config(tmp_path, MINIMAL + '\nstyle:\n  header_footer:\n    odd_page_text: "EXAMPLE THESIS"\n')
    assert load(tmp_path).style.header_footer.odd_page_text == "EXAMPLE THESIS"


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


def test_profiles_default_to_empty(tmp_path: Path) -> None:
    write_config(tmp_path, MINIMAL)
    assert load(tmp_path).profiles == ()


def test_profiles_are_read(tmp_path: Path) -> None:
    write_config(tmp_path, MINIMAL + "\nprofiles: [chemistry]\n")
    assert load(tmp_path).profiles == ("chemistry",)


def test_a_bare_string_profile_is_refused(tmp_path: Path) -> None:
    write_config(tmp_path, MINIMAL + "\nprofiles: chemistry\n")
    with pytest.raises(ConfigError, match="profiles"):
        load(tmp_path)


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------


def test_invalid_yaml_is_reported_with_the_file_name(tmp_path: Path) -> None:
    write_config(tmp_path, "project: [unclosed\n")

    with pytest.raises(ConfigError, match="not valid YAML"):
        load(tmp_path)


def test_a_non_mapping_document_is_refused(tmp_path: Path) -> None:
    write_config(tmp_path, "- just\n- a list\n")

    with pytest.raises(ConfigError, match="mapping at the top level"):
        load(tmp_path)


def test_an_empty_file_is_treated_as_all_defaults(tmp_path: Path) -> None:
    write_config(tmp_path, "")
    config = load(tmp_path)

    assert config.paths.thesis_dir == tmp_path / "manuscript"
