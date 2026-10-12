"""``thesis-refinery ledger``: the digest baseline for files that must not change.

Two actions, and the asymmetry between them is the design.

``build`` records the digest of every tracked file. It is run when the author
decides the repository is in the state it should stay in.

``verify`` re-measures and compares. It **fails** on any difference, in either
direction: a file whose bytes changed, a file that vanished, and a file that
appeared are all drift. That last one is easy to leave out and is the one that
matters most for a cover page, because "somebody added a file" is how a
re-exported declaration page arrives.

The manifest path is the project root by default rather than tucked into the
output directory: a baseline that a build can overwrite is not a baseline.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from thesis_refinery.config import ConfigError, load
from thesis_refinery.exitcodes import ExitCode
from thesis_refinery.ledger import inventory, manifest

__all__ = ["DEFAULT_MANIFEST_NAME", "add_arguments", "run"]

DEFAULT_MANIFEST_NAME = "manifest.json"
"""Where the baseline lives, relative to the project root."""


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "action",
        choices=("build", "verify", "inventory"),
        help=(
            "build: record digests. verify: compare against them and fail on any "
            "difference. inventory: print a human-readable inventory."
        ),
    )
    parser.add_argument(
        "project", nargs="?", metavar="PROJECT_DIR", help="project root (default: search upwards)"
    )
    parser.add_argument(
        "--manifest", metavar="PATH", help=f"baseline path (default: <project>/{DEFAULT_MANIFEST_NAME})"
    )
    parser.add_argument("--out", metavar="PATH", help="write the inventory or the report here")


def _manifest_path(args: argparse.Namespace, root: Path) -> Path:
    return Path(args.manifest) if args.manifest else root / DEFAULT_MANIFEST_NAME


def _exclusions(manifest_path: Path, root: Path) -> tuple[str, ...]:
    """The baseline must not be part of the baseline.

    Building the manifest creates the file, so a manifest that included itself
    would report its own creation as drift on the very next ``verify`` -- which is
    exactly what happened the first time this was run by hand: a fresh baseline
    verified as one added file.

    Resolving the path relative to the root also covers ``--manifest`` pointing
    somewhere inside the tree. A path outside the tree needs no exclusion, and
    :meth:`Path.relative_to` raising is how we know.
    """
    try:
        return (manifest_path.resolve().relative_to(root.resolve()).as_posix(),)
    except ValueError:
        return ()


def run(args: argparse.Namespace) -> int:
    try:
        config = load(args.project)
    except ConfigError as exc:
        print(f"thesis-refinery ledger: {exc}")
        return ExitCode.USAGE

    if args.action == "inventory":
        return _inventory(config.root, args)

    path = _manifest_path(args, config.root)
    exclusions = _exclusions(path, config.root)

    if args.action == "build":
        try:
            document = manifest.build(config.root, ignore=exclusions)
        except manifest.ManifestError as exc:
            print(f"thesis-refinery ledger: cannot build a baseline: {exc}")
            return ExitCode.ENVIRONMENT
        manifest.write(document, path)
        print(f"{len(document['files'])} file(s) recorded in {path}")
        return ExitCode.OK

    if not path.is_file():
        print(f"thesis-refinery ledger: no baseline at {path}; run `ledger build` first")
        return ExitCode.ENVIRONMENT

    verification = manifest.verify(manifest.load(path), config.root, ignore=exclusions)
    print(manifest.render(verification))

    if not verification.ok:
        return ExitCode.FAILED

    return ExitCode.OK


def _inventory(root: Path, args: argparse.Namespace) -> int:
    recorded = inventory.build(root)
    report = inventory.render_markdown(recorded)

    if args.out:
        from thesis_refinery.docxlib.save import atomic_write_bytes

        atomic_write_bytes(Path(args.out), report.encode("utf-8"))
        print(f"inventory written to {args.out}")
    else:
        print(report)

    counts = inventory.summary(recorded)
    summarised = ", ".join(f"{kind} {count}" for kind, count in sorted(counts.items()))
    print(f"{len(recorded.entries)} file(s): {summarised}")
    return ExitCode.OK
