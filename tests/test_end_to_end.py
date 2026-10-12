"""End-to-end acceptance: the bundled example actually builds and passes the gates.

This is the only test that proves the tool works, as opposed to proving that its
parts work. Everything else in the suite tests a unit; this one runs the chain
the README's quickstart promises, and it is the test that would have caught every
integration mistake made while wiring the pipeline.

It skips cleanly rather than failing while parts of the pipeline are missing, and
says why. A test that fails because a stage has not been written yet trains people
to ignore failures.

Run it deliberately with:

    python -m pytest tests/test_end_to_end.py -q -m slow
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pytest

from thesis_refinery.config import load
from thesis_refinery.exitcodes import ExitCode

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO = REPO_ROOT / "examples" / "demo-thesis"
RAW_RENDER = DEMO / "manuscript" / "_outputs" / "index.docx"

pytestmark = pytest.mark.slow


def require_pipeline() -> None:
    """Skip with a reason when the pipeline is incomplete, rather than failing."""
    for module, what in (
        ("thesis_refinery.stages.format", "the formatting stage"),
        ("thesis_refinery.pipeline", "the build orchestrator"),
    ):
        try:
            __import__(module)
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on checkout state
            pytest.skip(f"{what} ({module}) is not present in this checkout: {exc}")


def test_the_demo_project_configuration_loads() -> None:
    """Cheap, and it fails whenever the example drifts away from the schema."""
    config = load(DEMO)

    assert config.paths.thesis_dir == DEMO / "manuscript"
    assert config.paths.chapters_dir == DEMO / "manuscript/chapters"
    assert config.style.header_footer.odd_page_text, "the running head must be set, not defaulted"


def test_the_demo_declares_no_profiles() -> None:
    """The bundled example must work with the core pipeline and nothing optional."""
    assert load(DEMO).profiles == ()


def test_the_style_template_mirror_agrees() -> None:
    """Quarto and this project each declare the template path; the build asserts they match.

    Asserting it here as well means a mismatch is caught by the unit suite, where
    the failure says which two paths disagree, rather than by a build.
    """
    from thesis_refinery.pipeline import assert_reference_docx_mirror

    assert_reference_docx_mirror(load(DEMO))


@pytest.mark.skipif(not RAW_RENDER.is_file(), reason="no raw render; run `thesis-refinery render` first")
def test_a_raw_render_fails_the_gates_before_formatting() -> None:
    """The gate must have something to say about an unformatted render.

    A raw pandoc render has no page setup, no running head and no three-line
    tables. If the gates passed it, they would be checking nothing -- and this is
    the direction of failure that is easy to miss, because a gate that passes
    looks like a gate that works.
    """
    require_pipeline()
    from thesis_refinery.verify.gate import evaluate

    report = evaluate(RAW_RENDER, config=load(DEMO))

    assert not report.ok, "an unformatted render must not pass the gates"
    failed = {finding.check for finding in report.failures()}
    assert {"geometry.page-size", "numbering.formats", "tables.three-line"} <= failed


def test_the_demo_builds_and_passes_its_own_gates() -> None:
    """The quickstart, run for real.

    Uses the raw render rather than shelling out to Quarto: rendering is the part
    that needs an external program, and it is covered by the `render` job in CI.
    What this test covers is everything after it, which is the part that has to
    be right for the tool to be worth using.
    """
    require_pipeline()
    if not RAW_RENDER.is_file():
        pytest.skip("no raw render; run `thesis-refinery render examples/demo-thesis` first")

    from thesis_refinery.pipeline import build

    result = build(load(DEMO), skip_render=True)

    assert result.ok, f"build failed at step {result.failed_step!r} (exit {result.exit_code})"
    assert result.final is not None
    assert result.final.is_file()
    assert "format" in result.steps_run
    assert "verify" in result.steps_run


def test_the_formatted_demo_is_byte_stable_across_two_builds() -> None:
    """A build that produces a different document each run cannot be diffed.

    Two consecutive builds of the same sources must produce the same bytes in
    ``word/document.xml``. If they do not, every later diff of two versions is
    full of noise that nobody can attribute to a change.
    """
    require_pipeline()
    if not RAW_RENDER.is_file():
        pytest.skip("no raw render")

    from lxml import etree

    from thesis_refinery.config import load as load_config
    from thesis_refinery.docxlib.pkgs import Package
    from thesis_refinery.pipeline import build

    config = load_config(DEMO)
    first = build(config, skip_render=True)
    assert first.ok
    assert first.final is not None
    with Package(first.final) as package:
        before = etree.tostring(package.xml("word/document.xml"))

    second = build(config, skip_render=True)
    assert second.ok
    assert second.final is not None
    with Package(second.final) as package:
        after = etree.tostring(package.xml("word/document.xml"))

    assert before == after


def test_the_demo_build_leaves_its_inputs_alone() -> None:
    """The example's sources are inputs. A build must not rewrite them."""
    require_pipeline()
    if not RAW_RENDER.is_file():
        pytest.skip("no raw render")

    from thesis_refinery.ledger.hashing import sha256_file
    from thesis_refinery.pipeline import build

    watched = [RAW_RENDER, DEMO / "thesis.yml", DEMO / "manuscript" / "index.qmd"]
    watched += sorted((DEMO / "manuscript" / "chapters").glob("*.qmd"))
    before = {path: sha256_file(path) for path in watched}

    build(load(DEMO), skip_render=True)

    assert {path: sha256_file(path) for path in watched} == before


def test_the_exit_code_is_the_gates_verdict() -> None:
    """A failed gate must be a non-zero exit, because that is what CI reads."""
    require_pipeline()
    from thesis_refinery.pipeline import BuildResult

    assert BuildResult(exit_code=ExitCode.OK).ok
    assert not BuildResult(exit_code=ExitCode.FAILED).ok
    assert not BuildResult(exit_code=ExitCode.ENVIRONMENT).ok


def test_a_freshly_scaffolded_project_builds(tmp_path: Path) -> None:
    """The documented onboarding path, run for real.

    This is the test that was missing when a freshly scaffolded project could not
    build itself. The cause was a case-sensitive style-name comparison: pandoc's
    built-in reference document names the style ``heading 1``, so a project with
    no ``reference.docx`` -- every project immediately after ``init`` -- was found
    to have no heading at all. That cascaded into four more findings, all of them
    misleading, and the first thing a new user does is run exactly this.

    It needs Quarto, because a new project has no render to reuse.
    """
    require_pipeline()
    if shutil.which("quarto") is None:
        pytest.skip("quarto is not on PATH, so a new project cannot be rendered")

    from thesis_refinery.commands.init import run as init_run
    from thesis_refinery.pipeline import build

    destination = tmp_path / "thesis"
    assert init_run(argparse.Namespace(dest=str(destination), force=False, title=None)) == ExitCode.OK

    result = build(load(destination))

    assert result.ok, f"a freshly scaffolded project failed at {result.failed_step!r}"
    assert result.final is not None and result.final.is_file()
