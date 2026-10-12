"""The contract between the top-level CLI and each command's handler.

Each command's arguments are declared twice: once in its own module (so it can be
run standalone) and once in `cli.py` (so that building `--help` does not import
lxml). That duplication buys a fast help text, and it is exactly the kind of
duplication that drifts.

So it is checked. Every attribute a handler reads is asserted to be one the CLI
actually sets, and the two argument lists are asserted to declare the same
option strings. A handler that starts reading `args.something_new` fails here
rather than with an `AttributeError` in front of a user.
"""

from __future__ import annotations

import argparse

import pytest

from thesis_refinery.cli import SUBCOMMANDS, build_parser
from thesis_refinery.commands.diff import add_arguments as add_diff_arguments
from thesis_refinery.commands.ledger import add_arguments as add_ledger_arguments
from thesis_refinery.pipeline import add_build_arguments, add_render_arguments
from thesis_refinery.verify.audit import add_arguments as add_audit_arguments
from thesis_refinery.verify.gate import add_arguments as add_verify_arguments

# What each handler reads off its namespace. Written by hand from the handler
# bodies on purpose: if it were derived from the handler it could not catch a
# handler that started reading something new.
HANDLER_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "render": ("project", "quarto", "all_formats"),
    "build": ("project", "quarto", "all_formats", "skip_render", "no_verify"),
    "verify": ("docx", "project_dir", "prefix", "json", "verbose"),
    "audit": ("docx", "project_dir", "prefix", "json", "failures_only", "out"),
    "ledger": ("action", "project", "manifest", "out"),
    "diff": ("old", "new", "out"),
}

# Positional arguments each command needs before it will parse at all.
REQUIRED = {
    "ledger": ["build"],
    "diff": ["old.docx", "new.docx"],
    "extract-prefix": ["--source", "a.docx", "--out", "b.docx"],
}

MODULE_ARGUMENTS = {
    "render": add_render_arguments,
    "build": add_build_arguments,
    "verify": add_verify_arguments,
    "audit": add_audit_arguments,
    "ledger": add_ledger_arguments,
    "diff": add_diff_arguments,
}


def options_of(add_arguments) -> set[str]:
    """Every option string a set of argument definitions declares.

    ``add_help=False`` on both probes: the automatic ``-h/--help`` is argparse's,
    not the command's, and comparing it would be noise.
    """
    parser = argparse.ArgumentParser(add_help=False)
    add_arguments(parser)
    return {option for action in parser._actions for option in action.option_strings}


def cli_options(name: str) -> set[str]:
    parser = argparse.ArgumentParser(prog="probe", add_help=False)
    subparsers = parser.add_subparsers()
    for command in SUBCOMMANDS:
        if command.name != name:
            continue
        sub = subparsers.add_parser(command.name, add_help=False)
        command.add_arguments(sub)
        return {option for action in sub._actions for option in action.option_strings}
    raise AssertionError(f"no subcommand named {name!r}")


@pytest.mark.parametrize(("name", "attributes"), sorted(HANDLER_ATTRIBUTES.items()))
def test_the_cli_sets_every_attribute_its_handler_reads(name: str, attributes: tuple[str, ...]) -> None:
    args = build_parser().parse_args([name, *REQUIRED.get(name, [])])
    for attribute in attributes:
        assert hasattr(args, attribute), f"{name} handler reads args.{attribute}, which the CLI never sets"


@pytest.mark.parametrize(("name", "add_arguments"), sorted(MODULE_ARGUMENTS.items()))
def test_the_cli_and_the_module_declare_the_same_options(name: str, add_arguments) -> None:
    """Two lists of options that must agree, so they are compared."""
    assert cli_options(name) == options_of(add_arguments)


def test_a_project_dir_given_before_the_subcommand_survives() -> None:
    """argparse applies subparser defaults to the shared namespace.

    Without `default=SUPPRESS` on the subparser's `--project-dir`,
    `thesis-refinery -C elsewhere verify` silently loses the `elsewhere`: the
    subparser writes its own default over the value the user supplied. The
    failure is invisible -- the command runs, against the wrong project.
    """
    args = build_parser().parse_args(["-C", "elsewhere", "verify"])
    assert args.project_dir == "elsewhere"


def test_a_project_dir_given_after_the_subcommand_also_works() -> None:
    args = build_parser().parse_args(["verify", "--project-dir", "elsewhere"])
    assert args.project_dir == "elsewhere"


def test_the_global_project_dir_defaults_to_none() -> None:
    assert build_parser().parse_args(["verify"]).project_dir is None


def test_build_inherits_every_render_option() -> None:
    """--all-formats and --quarto must exist on build too, or the docs lie."""
    assert options_of(add_render_arguments) <= options_of(add_build_arguments)


def test_every_handler_attribute_is_covered_by_this_file() -> None:
    """A new command must be added here, or the contract goes unchecked."""
    wired = {command.name for command in SUBCOMMANDS if ":" in command.target}
    assert wired - {"init", "extract-prefix"} <= set(HANDLER_ATTRIBUTES)
