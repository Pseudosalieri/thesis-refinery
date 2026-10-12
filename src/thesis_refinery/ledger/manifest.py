"""A digest baseline for the files that are supposed to be immutable.

The failure this module exists to prevent is *silent* drift. A declaration page
re-exported from Word with a stray edit, a cover page somebody "just fixes" in
place, a figure re-saved at a different compression setting: none of them
announce themselves, all of them change bytes, and the only moment they are cheap
to fix is before the thesis is filed.

So the bytes of a file that must not change are recorded once and compared on
every later run. :func:`verify` does not warn, score, or tolerate: it returns
three sorted tuples -- ``changed``, ``missing``, ``added`` -- and
:attr:`Verification.ok` is true only when all three are empty. A gate with a
tolerance setting is a gate that gets tuned until it passes.

Two consequences of taking that seriously:

* A file that could not be read while the manifest was being built is refused
  outright. Omitting it would produce a baseline that claims to cover a file it
  never measured, which is the one thing a baseline must not do.
* Unknown keys and unknown versions are errors rather than ignored. A manifest
  this build does not fully understand is one it must not report "no drift"
  about.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thesis_refinery.docxlib import save
from thesis_refinery.ledger import inventory as inventory_module
from thesis_refinery.ledger.hashing import (
    DEFAULT_SKIP_DIRS,
    is_ignored,
    sha256_file,
)

__all__ = [
    "JSON_INDENT",
    "MANIFEST_VERSION",
    "MAX_LISTED",
    "ManifestError",
    "Verification",
    "build",
    "load",
    "render",
    "verify",
    "write",
]

MANIFEST_VERSION = 1
"""Version of the manifest document this build writes and accepts."""

JSON_INDENT = 2
"""Indentation of the written manifest, so a diff of two of them is readable."""

MAX_LISTED = 20
"""Paths shown per group by :func:`render` before it summarises the rest."""

_TOP_LEVEL_KEYS = frozenset({"version", "root", "files"})
_FILE_KEYS = frozenset({"sha256", "size"})
_DIGEST = re.compile(r"[0-9a-f]{64}")
_DRIVE = re.compile(r"[A-Za-z]:")


class ManifestError(ValueError):
    """Raised when a manifest is unreadable, malformed, or of another version."""


@dataclass(frozen=True)
class Verification:
    """What comparing a tree against its manifest found.

    Three tuples rather than three sets, because the report is read as text and
    two runs must produce the same text; and three *named* lists rather than one
    list of tagged records, because the three answers are decisions, not detail:
    a missing declaration page and an extra build artefact are a missing file and
    an added file, and they call for different responses.
    """

    changed: tuple[str, ...]
    """Recorded, still present, different bytes."""

    missing: tuple[str, ...]
    """Recorded, and no longer readable on disk."""

    added: tuple[str, ...]
    """Present on disk and not recorded."""

    def __post_init__(self) -> None:
        """Sort and de-duplicate each list.

        A caller cannot then be surprised by the order its own walk happened to
        produce, and a report is comparable between two machines as plain text.
        """
        object.__setattr__(self, "changed", tuple(sorted(set(self.changed))))
        object.__setattr__(self, "missing", tuple(sorted(set(self.missing))))
        object.__setattr__(self, "added", tuple(sorted(set(self.added))))

    @property
    def ok(self) -> bool:
        """True when nothing changed, nothing is missing, and nothing appeared."""
        return not (self.changed or self.missing or self.added)


# ---------------------------------------------------------------------------
# Building and writing
# ---------------------------------------------------------------------------


def build(
    root: str | Path,
    *,
    skip_dirs: Sequence[str] = DEFAULT_SKIP_DIRS,
    ignore: Sequence[str] = (),
) -> dict[str, Any]:
    """A minimal manifest of *root*: ``{"version", "root", "files"}``.

    ``files`` maps each root-relative posix path to its ``sha256`` and ``size``.
    Nothing else is recorded -- no timestamps, no mtimes, no absolute paths --
    because every extra field is one more thing that can differ between two
    machines describing the same tree, and a baseline that differs for a reason
    unrelated to the thesis trains its reader to ignore it.

    The manifest is a projection of :func:`thesis_refinery.ledger.inventory.build`,
    so the two can never disagree about which files exist or what they hash to.
    They are both on the *writing* side of the ledger; the gate that reads a
    manifest walks the tree on its own, and :func:`verify` explains why.
    """
    snapshot = inventory_module.build(root, skip_dirs=skip_dirs, ignore=ignore)
    unreadable = [
        entry.path for entry in snapshot.entries if entry.kind == inventory_module.MISSING
    ]
    if unreadable:
        raise ManifestError(
            "cannot record a digest for "
            + ", ".join(unreadable)
            + ": a manifest that skips a file it could not read claims a coverage it "
            "does not have. Remove the path, or exclude it with `ignore`."
        )

    return {
        "version": MANIFEST_VERSION,
        "root": snapshot.root,
        "files": {
            entry.path: {"sha256": entry.sha256, "size": entry.size}
            for entry in snapshot.entries
        },
    }


def write(
    manifest: Mapping[str, Any],
    path: str | Path,
    *,
    guard: bool = False,
) -> Path:
    """Validate *manifest*, write it to *path* as UTF-8 JSON, and return *path*.

    Written through :func:`thesis_refinery.docxlib.save.atomic_write_bytes`, so an
    interrupted run cannot leave a half-written baseline behind -- a truncated
    manifest would be the worst possible state, since it would report real files
    as missing.

    The tracked-file guard is **off** by default here, unlike every other write in
    this project, and for one reason: this is the file a user has to be able to
    rewrite. From its second commit onwards the manifest is tracked by git, and
    refusing to update it would make the baseline un-updatable at the exact moment
    an intentional change has been accepted -- so the manifest would be edited by
    hand instead, which is how a baseline starts lying. No authored content is at
    risk: the file is machine-written and its previous revision is in git. Pass
    ``guard=True`` for the strict refusal.

    The manifest is validated before writing, and the file is written in the form
    :func:`load` would return. A file written here is what a later gate will read,
    so writing one this module would refuse to load -- or would load back
    differently -- is a trap set for the future.
    """
    destination = Path(path)
    canonical = _checked(manifest, where=str(destination))
    save.atomic_write_bytes(destination, _dumps(canonical), guard=guard)
    return destination


def load(path: str | Path) -> dict[str, Any]:
    """Read a manifest written by :func:`write`, refusing anything malformed.

    Every failure -- an unreadable path, invalid JSON, a missing key, an unknown
    version -- raises :class:`ManifestError` with the path in the message. A
    loader that returned a partially filled dictionary would move the failure to
    the gate, where it would look like drift in the thesis rather than a problem
    with the baseline.
    """
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read manifest {source}: {exc}") from exc

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{source} is not valid JSON: {exc}") from exc

    return _checked(document, where=str(source))


def _dumps(manifest: Mapping[str, Any]) -> bytes:
    """Canonical JSON bytes: sorted keys, fixed indent, one trailing newline.

    Deterministic output is not cosmetic here. The manifest is committed, so
    regenerating it must produce a diff of exactly the files whose bytes changed;
    if key order or spacing moved around, every regeneration would be a
    hundred-line diff and nobody would review it.
    """
    text = json.dumps(manifest, indent=JSON_INDENT, sort_keys=True, ensure_ascii=False)
    return text.encode("utf-8") + b"\n"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _checked(document: Any, *, where: str) -> dict[str, Any]:
    """*document* as a manifest, or :class:`ManifestError`.

    Returns a normalised copy, so a caller cannot hold a manifest whose digests
    are upper case and then be told a file changed because of the difference.
    """
    if not isinstance(document, Mapping):
        raise ManifestError(
            f"{where}: a manifest must be a JSON object, not {type(document).__name__}"
        )

    unknown = set(document) - _TOP_LEVEL_KEYS
    if unknown:
        raise ManifestError(
            f"{where}: unknown key(s) {', '.join(sorted(unknown))}; "
            f"known: {', '.join(sorted(_TOP_LEVEL_KEYS))}"
        )

    version = document.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != MANIFEST_VERSION:
        raise ManifestError(
            f"{where}: manifest version {version!r} is not the version this build writes "
            f"({MANIFEST_VERSION}). Regenerate the manifest rather than guessing at its meaning."
        )

    root = document.get("root")
    if not isinstance(root, str):
        raise ManifestError(f"{where}: root must be a string, not {type(root).__name__}")

    files = document.get("files")
    if not isinstance(files, Mapping):
        raise ManifestError(f"{where}: files must be an object, not {type(files).__name__}")

    records: dict[str, dict[str, Any]] = {}
    for name, raw in files.items():
        records[_checked_path(name, where=where)] = _checked_file(name, raw, where=where)

    return {
        "version": MANIFEST_VERSION,
        "root": root,
        "files": dict(sorted(records.items())),
    }


def _checked_path(name: Any, *, where: str) -> str:
    """A manifest key as a safe root-relative posix path.

    Absolute paths, backslashes, colons and ``..`` segments are refused. The
    colon rule over-refuses a file name that really contains one, and that is the
    intended trade: on Windows ``a:b.txt`` is drive-relative and resolves
    somewhere else entirely, and a baseline that means one thing on the machine
    that wrote it and another on the machine that reads it is worse than a
    refusal to describe an exotic name. The alternative -- a manifest that can
    point ``verify`` at a file outside the tree it is supposed to describe -- is
    not a trade at all.
    """
    if not isinstance(name, str) or not name:
        raise ManifestError(f"{where}: a file entry has the empty or non-string path {name!r}")
    if name.startswith("/") or "\\" in name or _DRIVE.match(name):
        raise ManifestError(
            f"{where}: {name!r} is not a root-relative posix path; a manifest key is "
            "relative, uses forward slashes, and has no drive letter"
        )
    if ".." in name.split("/"):
        raise ManifestError(f"{where}: {name!r} escapes the tree it is supposed to describe")
    return name


def _checked_file(name: str, raw: Any, *, where: str) -> dict[str, Any]:
    """One ``{"sha256", "size"}`` record, validated field by field."""
    if not isinstance(raw, Mapping):
        raise ManifestError(
            f"{where}: the record for {name!r} must be an object, not {type(raw).__name__}"
        )

    unknown = set(raw) - _FILE_KEYS
    if unknown:
        raise ManifestError(
            f"{where}: the record for {name!r} has unknown key(s) "
            f"{', '.join(sorted(unknown))}; known: {', '.join(sorted(_FILE_KEYS))}"
        )

    digest = raw.get("sha256")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest.lower()):
        raise ManifestError(
            f"{where}: the sha256 recorded for {name!r} must be 64 hex digits, got {digest!r}"
        )

    size = raw.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ManifestError(
            f"{where}: the size recorded for {name!r} must be a non-negative integer, got {size!r}"
        )

    return {"sha256": digest.lower(), "size": size}


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify(
    manifest: Mapping[str, Any],
    root: str | Path,
    *,
    skip_dirs: Sequence[str] = DEFAULT_SKIP_DIRS,
    ignore: Sequence[str] = (),
) -> Verification:
    """Compare the tree at *root* against *manifest*.

    The exclusions are applied to **both** sides. A file the ledger is not
    responsible for is neither expected nor reported, so adding an ``ignore``
    entry to a project cannot produce a phantom "missing" for a file that is
    still sitting there.

    The walk here is a second implementation on purpose, rather than a call to
    ``inventory.build``. If the gate enumerated files with the same code the
    baseline was built from, a bug that dropped a directory from the walk would
    drop it from both sides of the comparison and the gate would report "no
    drift" for files it never looked at. Sharing a walk between a writer and its
    checker is how a bug cancels itself out -- see ``CONTRIBUTING.md``. The
    digest primitive is shared; the traversal is not.

    The manifest's recorded ``root`` is a label for humans, not an input: a
    baseline generated on one machine has to be verifiable on another, where the
    checkout lives somewhere else entirely.

    A recorded file whose size already disagrees is reported as changed without
    being hashed -- its bytes cannot match a digest taken over a different number
    of them.
    """
    recorded = _checked(manifest, where="manifest")
    expected: Mapping[str, Mapping[str, Any]] = recorded["files"]
    base = Path(root)
    if not base.is_dir():
        # Not "everything is missing": drift and a mistyped path are different
        # problems, and a report that conflated them would send its reader to
        # restore files that were never gone.
        raise ManifestError(f"cannot verify {base}: it is not a directory")

    changed: list[str] = []
    missing: list[str] = []
    added: list[str] = []
    seen: set[str] = set()

    for relative, size in _walk(base, skip_dirs, ignore):
        seen.add(relative)
        record = expected.get(relative)
        if record is None:
            added.append(relative)
            continue
        if size == record["size"]:
            try:
                digest = sha256_file(base / relative)
            except OSError:
                missing.append(relative)
                continue
            if digest == record["sha256"]:
                continue
        changed.append(relative)

    for relative in expected:
        if relative not in seen and not _excluded(relative, skip_dirs, ignore):
            missing.append(relative)

    return Verification(changed=tuple(changed), missing=tuple(missing), added=tuple(added))


def _walk(
    base: Path,
    skip_dirs: Sequence[str],
    ignore: Sequence[str],
) -> Iterator[tuple[str, int]]:
    """Yield ``(posix_relpath, size)`` for every file the ledger is responsible for.

    Implemented with ``os.scandir`` rather than the ``pathlib`` recursion in
    ``ledger.hashing`` so that it is a genuinely separate traversal, and so that
    "do not follow a symlinked directory" is stated at the call site instead of
    inherited. Nothing here depends on the order of the results: the verification
    sorts them.
    """
    skipped = frozenset(skip_dirs)
    pending: list[tuple[Path, str]] = [(base, "")]
    while pending:
        directory, prefix = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                relative = f"{prefix}{entry.name}"
                if is_ignored(relative, ignore):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in skipped:
                        pending.append((Path(entry.path), f"{relative}/"))
                elif entry.is_file(follow_symlinks=True):
                    yield relative, entry.stat(follow_symlinks=True).st_size


def _excluded(relpath: str, skip_dirs: Sequence[str], ignore: Sequence[str]) -> bool:
    """True when the ledger is not responsible for *relpath*, on either side.

    The same predicate has to answer for a path that is on disk and for one that
    is only in the manifest; that is why it is written against the path string
    and not against a directory entry.
    """
    if is_ignored(relpath, ignore):
        return True
    # A file *named* like a skipped directory is still a file: `iter_files` skips
    # directories, and so does this.
    return any(part in skip_dirs for part in relpath.split("/")[:-1])


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def render(verification: Verification, *, limit: int = MAX_LISTED) -> str:
    """A short report: a verdict line, then the paths, grouped by kind.

    The counts are printed even when they are zero, so the shape of a passing and
    a failing report is the same and a reader can tell "nothing was added" from
    "additions were not checked". Each group is capped at *limit* paths: a report
    is for deciding what to do next, and a thousand-line list of files is a
    different tool's job. The text ends with a newline.
    """
    lines = [f"manifest verify: {'OK' if verification.ok else 'FAILED'} - {_counts(verification)}"]
    if verification.ok:
        return "\n".join(lines) + "\n"

    for title, paths in (
        ("changed", verification.changed),
        ("missing", verification.missing),
        ("added", verification.added),
    ):
        if not paths:
            continue
        lines.append(f"{title}:")
        lines.extend(f"  {path}" for path in paths[:limit])
        if len(paths) > limit:
            lines.append(f"  ... and {len(paths) - limit} more")
    lines.append(
        "If the change was intended, rebuild the manifest and commit it; "
        "otherwise restore the recorded bytes."
    )
    return "\n".join(lines) + "\n"


def _counts(verification: Verification) -> str:
    """The three counts, in the order the groups are reported."""
    return (
        f"{len(verification.changed)} changed, "
        f"{len(verification.missing)} missing, "
        f"{len(verification.added)} added"
    )
