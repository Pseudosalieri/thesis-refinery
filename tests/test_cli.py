"""Tests for the command-line surface.

These deliberately test the *dispatch mechanism* rather than the behaviour of any
particular subcommand, so that landing a subcommand's implementation does not
have to edit this file.
"""

from __future__ import annotations

import argparse

import pytest

from thesis_refinery.cli import SUBCOMMANDS, Command, _dispatch, build_parser, main
from thesis_refinery.exitcodes import ExitCode


def subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    raise AssertionError("parser has no subparsers")


def test_subcommand_names_are_unique() -> None:
    names = [command.name for command in SUBCOMMANDS]
    assert len(names) == len(set(names))


def test_every_declared_subcommand_is_parseable() -> None:
    parser = build_parser()
    assert set(subparsers(parser)) == {command.name for command in SUBCOMMANDS}

    for command in SUBCOMMANDS:
        args = parser.parse_args([command.name, *mandatory_args(command.name)])
        assert args._command.name == command.name


def mandatory_args(name: str) -> list[str]:
    """Positional arguments a subcommand needs before it will parse."""
    return {
        "ledger": ["build"],
        "diff": ["old.docx", "new.docx"],
        "extract-prefix": ["--source", "a.docx", "--out", "b.docx"],
    }.get(name, [])


def test_no_subcommand_prints_help_and_reports_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == ExitCode.USAGE
    assert "usage: thesis-refinery" in capsys.readouterr().out


def test_unknown_subcommand_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["there-is-no-such-command"])
    assert excinfo.value.code == ExitCode.USAGE


def test_version_flag_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == ExitCode.OK
    assert "thesis-refinery" in capsys.readouterr().out


def test_dispatch_reports_a_declared_but_unbuilt_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A subcommand whose module does not exist yet must not raise a traceback."""
    ghost = Command(
        name="ghost",
        summary="not a real command",
        target="thesis_refinery.no_such_module:run",
        add_arguments=lambda p: None,
    )
    code = _dispatch(ghost, argparse.Namespace())
    assert code == ExitCode.NOT_IMPLEMENTED
    assert "not implemented" in capsys.readouterr().err


def test_dispatch_does_not_swallow_a_missing_dependency() -> None:
    """A missing *third-party* module is a real problem and must surface."""
    ghost = Command(
        name="ghost",
        summary="not a real command",
        target="definitely_not_installed_anywhere:run",
        add_arguments=lambda p: None,
    )
    with pytest.raises(ModuleNotFoundError):        _dispatch(ghost, argparse.Namespace())
