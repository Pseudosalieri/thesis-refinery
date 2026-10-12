"""Command handlers for the top-level CLI.

Each module here exposes ``add_arguments(parser)`` and ``run(args) -> int``. The
top-level CLI builds its own subparser for each command — so that ``--help``
stays fast and does not import ``lxml`` — and hands the resulting namespace to
``run``. ``tests/test_cli_contract.py`` asserts that every attribute a handler
reads is one the CLI actually sets, because the two argument lists are written
separately and duplicated definitions that are not checked are duplicated
definitions that drift.
"""

from __future__ import annotations

__all__: list[str] = []
