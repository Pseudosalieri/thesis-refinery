"""The build: render, then apply the stages in order, then run the gates.

This is the one entry point that ties everything together, and it is the reason
`build.ps1` no longer exists. The predecessor's orchestrator was a 300-line
PowerShell script that hard-coded the absolute path of one script, one Quarto
installation, and a step table that had drifted out of step with its own
documentation. Here the ordering is data, the paths come from the configuration,
and the tool resolution has no hard-coded guess to go stale.

Three properties worth naming, all of them reactions to that history:

**Fail fast, and say which step.** Any non-zero exit stops the build. Every stage
writes a new file rather than editing one in place, so a failure has exactly one
candidate input and the intermediate products are still on disk to look at.

**Assert the mirror.** Quarto cannot read this project's ``thesis.yml``, so the
style template path appears twice: once in the configuration and once in
``_quarto.yml``. Rather than document the duplication, the build resolves both
and refuses to run if they disagree. A documented duplication is a duplication
that drifts.

**A missing stage is reported, not crashed on.** Stages are imported lazily and
by name, so a build on a partial checkout says which step is absent instead of
raising an ImportError from three frames deep.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from thesis_refinery.config import Config, ConfigError, load
from thesis_refinery.exitcodes import ExitCode

__all__ = [
    "BuildError",
    "BuildResult",
    "Stage",
    "add_build_arguments",
    "add_render_arguments",
    "build",
    "main",
    "render",
    "render_main",
    "run_build",
    "run_render",
    "stages",
]


class BuildError(RuntimeError):
    """A build step failed. Carries the step name and the exit code to report."""

    def __init__(self, step: str, code: int, message: str = "") -> None:
        self.step = step
        self.code = code
        detail = f": {message}" if message else ""
        super().__init__(f"step {step!r} failed with exit code {code}{detail}")

RESOURCE_PATH_ARGUMENT = "--resource-path=chapters"
"""Without this, every figure referenced from a chapter file silently vanishes.

Chapter sources refer to ``raw/media/...``, which is relative to the chapter
directory. pandoc resolves relative resource paths against the input file's
directory, so a render without this argument replaces all of them with their alt
text -- and the document still renders, which is why it went unnoticed until the
figures were counted.
"""


@dataclass
class BuildResult:
    """What a build produced, and whether it passed."""

    final: Path | None = None
    steps_run: list[str] = field(default_factory=list)
    failed_step: str | None = None
    exit_code: int = ExitCode.OK

    @property
    def ok(self) -> bool:
        return self.exit_code == ExitCode.OK


def _log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


# ---------------------------------------------------------------------------
# Tool resolution
# ---------------------------------------------------------------------------


def resolve_quarto(config: Config, explicit: str | None = None) -> str | None:
    """Find the Quarto executable, or ``None``.

    Order: explicit argument, then ``QUARTO_EXE``, then ``thesis.yml``, then
    PATH. There is deliberately no hard-coded installation path: the project this
    came from pinned one, the pin went stale, a PATH fallback quietly took over,
    and nobody learned for two months that the configured value was dead.
    """
    for candidate in (explicit, os.environ.get("QUARTO_EXE"), config.tools.quarto):
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        return resolved or (candidate if Path(candidate).is_file() else None)
    return shutil.which("quarto")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def assert_reference_docx_mirror(config: Config) -> None:
    """Refuse to build when the two declarations of the style template disagree.

    Quarto reads ``_quarto.yml``; this project reads ``thesis.yml``. The path
    therefore appears twice, and the only honest way to keep two copies of a fact
    in step is to check them against each other every time.
    """
    configured = config.reference_docx
    if configured is None:
        return

    quarto_config = config.paths.quarto_config
    if not quarto_config.is_file():
        return

    try:
        document = yaml.safe_load(quarto_config.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read {quarto_config}: {exc}") from exc

    declared = (
        (document.get("format") or {}).get("docx", {}).get("reference-doc")
        if isinstance(document.get("format"), dict)
        else None
    )
    if not declared:
        return

    mirror = Path(declared)
    if not mirror.is_absolute():
        mirror = config.paths.thesis_dir / mirror

    if mirror.resolve() != configured.resolve():
        raise ConfigError(
            f"the style template is declared twice and the two disagree:\n"
            f"  thesis.yml  reference_docx        -> {configured}\n"
            f"  _quarto.yml format.docx.reference-doc -> {mirror}\n"
            "one of them is wrong, and a render would use the one you did not edit"
        )


def render(config: Config, *, quarto: str | None = None, all_formats: bool = False) -> Path:
    """Render the markdown sources to ``.docx``, returning the product path."""
    executable = resolve_quarto(config, quarto)
    if executable is None:
        raise ConfigError(
            "cannot find quarto. Install it, or set QUARTO_EXE, or set tools.quarto "
            "in thesis.yml. `verify` and `audit` do not need it."
        )

    config.paths.output_dir.mkdir(parents=True, exist_ok=True)
    # Default to docx only. `--all-formats` asks Quarto for everything the
    # project declares, which also means PDF and therefore a LaTeX engine and
    # the fonts installed: neither of which the gates need, and both of which
    # are missing on a clean CI machine.
    command = [executable, "render", str(config.paths.index_source.name), RESOURCE_PATH_ARGUMENT]
    if not all_formats:
        command[3:3] = ["--to", "docx"]

    _log(f"render: {' '.join(command)}")
    completed = subprocess.run(command, cwd=str(config.paths.thesis_dir), check=False)
    if completed.returncode != 0:
        raise BuildError("render", completed.returncode)

    produced = config.paths.output_dir / "index.docx"
    if not produced.is_file():
        raise BuildError(
            "render",
            ExitCode.ENVIRONMENT,
            f"quarto reported success but {produced} does not exist",
        )
    return produced


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def _always_enabled(config: Config) -> bool:
    """The default: a stage that every build runs."""
    return True


def _configured_by_section(name: str) -> Callable[[Config], bool]:
    """A stage that runs exactly when ``thesis.yml`` gives it a ``stages.<name>`` section."""
    return lambda config: name in config.stages


def _configured_by_frontmatter(config: Config) -> bool:
    """The front-matter stage runs when the project names a cover document.

    Its input is a project-level path rather than a stage section, because the
    cover is a fact about the project — and because ``verify`` needs the same path
    to check the imported pages against their source.
    """
    return config.frontmatter_docx is not None


@dataclass(frozen=True)
class Stage:
    """One transformation between the render and the gates.

    ``enabled`` decides whether the stage is part of *this* project's build. The
    default is "always", which is right for the stage that applies the style
    specification: a thesis that is not formatted to its specification is not a
    thesis this tool produced.

    An optional stage is switched on by its own configuration. That keeps the rule
    this project states everywhere else — a stage that runs either did its work or
    said why it could not — while letting a project with no abbreviation table leave
    the abbreviation stage out of its pipeline, rather than failing a build over a
    feature it never asked for.
    """

    name: str
    summary: str
    module: str
    function: str
    enabled: Callable[[Config], bool] = _always_enabled


STAGES: tuple[Stage, ...] = (
    Stage(
        name="abbreviations",
        summary="rebuild the abbreviation table from its markdown source",
        module="thesis_refinery.stages.abbreviations",
        function="apply",
        enabled=_configured_by_section("abbreviations"),
    ),
    Stage(
        name="references",
        summary="convert bibliography entries to the citation style",
        module="thesis_refinery.stages.references",
        function="apply",
        enabled=_configured_by_section("references"),
    ),
    Stage(
        name="format",
        summary="apply the style specification",
        module="thesis_refinery.stages.format",
        function="apply",
    ),
)


def run_stage(stage: Stage, source: Path, destination: Path, config: Config) -> Path:
    """Run one stage, or explain which part of the pipeline is missing."""
    from importlib import import_module

    try:
        module = import_module(stage.module)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if not missing.startswith("thesis_refinery."):
            raise
        raise BuildError(
            stage.name,
            ExitCode.NOT_IMPLEMENTED,
            f"{stage.module} is not present in this build",
        ) from exc

    apply_stage: Callable[..., Path] = getattr(module, stage.function)
    return apply_stage(
        source,
        destination,
        style=config.style,
        options=config.stages.get(stage.name, {}),
    )


def unknown_stage_options(config: Config) -> tuple[str, ...]:
    """Stage names in ``thesis.yml`` that no stage answers to.

    Checked here rather than in :mod:`thesis_refinery.config` because this is the
    one module that knows the stage table. A misspelled name is otherwise a section
    of the configuration file that quietly configures nothing — the same failure as
    a misspelled key, one level up.
    """
    known = {stage.name for stage in STAGES}
    return tuple(sorted(set(config.stages) - known))


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------


def build(
    config: Config,
    *,
    quarto: str | None = None,
    skip_render: bool = False,
    skip_verify: bool = False,
    all_formats: bool = False,
) -> BuildResult:
    """Run the whole pipeline. Never raises for a failing gate; returns the result."""
    result = BuildResult()

    try:
        assert_reference_docx_mirror(config)
    except ConfigError as exc:
        _log(f"configuration refused: {exc}")
        result.failed_step = "configure"
        result.exit_code = ExitCode.ENVIRONMENT
        return result

    unknown = unknown_stage_options(config)
    if unknown:
        known = ", ".join(stage.name for stage in STAGES)
        _log(
            f"configuration refused: thesis.yml configures unknown stage(s) "
            f"{', '.join(unknown)}; known stages: {known}"
        )
        result.failed_step = "configure"
        result.exit_code = ExitCode.USAGE
        return result

    current = config.paths.output_dir / "index.docx"
    if skip_render:
        if not current.is_file():
            _log(f"cannot skip the render: {current} does not exist")
            result.failed_step = "render"
            result.exit_code = ExitCode.ENVIRONMENT
            return result
        _log(f"render: skipped, reusing {current}")
    else:
        try:
            current = render(config, quarto=quarto, all_formats=all_formats)
        except (BuildError, ConfigError) as exc:
            _log(f"render failed: {exc}")
            result.failed_step = "render"
            result.exit_code = getattr(exc, "code", ExitCode.ENVIRONMENT)
            return result
        result.steps_run.append("render")

    active = [stage for stage in STAGES if stage.enabled(config)]
    skipped = [stage for stage in STAGES if stage not in active]

    for stage in skipped:
        _log(f"{stage.name}: not configured, so not part of this build")

    for position, stage in enumerate(active):
        # The last stage writes the documented product path; the others write a
        # file named after themselves, so that a failure has a localisable input.
        # `verify` defaults to the product path, and the README names it, so it
        # has to be the real thing rather than a copy of it.
        #
        # "Last" means last *of the stages this project runs*, not last in the
        # table: a build that leaves an optional stage out must still produce
        # `index-final.docx` rather than a file named after whichever stage
        # happened to come last.
        is_last = position == len(active) - 1
        destination = (
            config.paths.final_docx
            if is_last
            else config.paths.output_dir / f"index-{stage.name}.docx"
        )
        _log(f"{stage.name}: {stage.summary}")
        try:
            current = run_stage(stage, current, destination, config)
        except (BuildError, ConfigError) as exc:
            _log(f"{stage.name} failed: {exc}")
            result.failed_step = stage.name
            result.exit_code = getattr(exc, "code", ExitCode.FAILED)
            return result
        result.steps_run.append(stage.name)

    result.final = current

    if skip_verify:
        _log("verify: skipped")
        return result

    from thesis_refinery.verify.gate import evaluate
    from thesis_refinery.verify.report import render_text

    _log("verify: running the gates")
    report = evaluate(current, config=config)
    print(render_text(report, verbose=False))
    result.steps_run.append("verify")

    if not report.ok:
        result.failed_step = "verify"
        result.exit_code = ExitCode.FAILED

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def add_render_arguments(parser: argparse.ArgumentParser) -> None:
    """The argument contract shared with ``cli.SUBCOMMANDS``; a test checks it."""
    parser.add_argument(
        "project", nargs="?", metavar="PROJECT_DIR", help="project root (default: search upwards)"
    )
    parser.add_argument(
        "--quarto", metavar="PATH", help="Quarto executable (default: resolve from PATH)"
    )
    parser.add_argument(
        "--profile", action="append", metavar="NAME", help="enable a domain profile (repeatable)"
    )
    parser.add_argument(
        "--all-formats", action="store_true", help="also render PDF (needs a LaTeX engine)"
    )


def add_build_arguments(parser: argparse.ArgumentParser) -> None:
    """The argument contract shared with ``cli.SUBCOMMANDS``; a test checks it."""
    add_render_arguments(parser)
    parser.add_argument("--skip-render", action="store_true", help="reuse the existing render product")
    parser.add_argument("--no-verify", action="store_true", help="skip the gates (useful while iterating)")


def run_render(args: argparse.Namespace) -> int:
    try:
        config = load(args.project)
        produced = render(config, quarto=args.quarto, all_formats=args.all_formats)
    except ConfigError as exc:
        print(f"thesis-refinery render: {exc}")
        return ExitCode.USAGE
    except BuildError as exc:
        print(f"thesis-refinery render: {exc}")
        return ExitCode(exc.code)

    print(produced)
    return ExitCode.OK


def run_build(args: argparse.Namespace) -> int:
    try:
        config = load(args.project)
    except ConfigError as exc:
        print(f"thesis-refinery build: {exc}")
        return ExitCode.USAGE

    result = build(
        config,
        quarto=args.quarto,
        skip_render=args.skip_render,
        skip_verify=args.no_verify,
        all_formats=args.all_formats,
    )

    if result.ok:
        print(f"\nBUILD OK ({len(result.steps_run)} step(s)): {result.final}")
    else:
        print(f"\nBUILD FAILED at step {result.failed_step!r}", file=sys.stderr)
    return result.exit_code


def render_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="thesis-refinery render",
        description="Render the markdown sources to .docx. Applies no style and runs no gates.",
    )
    add_render_arguments(parser)
    return run_render(parser.parse_args(argv))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="thesis-refinery build",
        description="Render, apply the style, and run the gates.",
    )
    add_build_arguments(parser)
    return run_build(parser.parse_args(argv))


def stages() -> Iterator[Stage]:
    """The stage table, for documentation and for a dry run."""
    yield from STAGES


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
