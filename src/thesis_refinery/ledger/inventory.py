"""A sorted snapshot of a tree: every file, its size, its digest, its kind.

The inventory is the ledger's unit of truth. Everything else in the package is a
projection of it: the manifest keeps the digests and drops the rest, the markdown
renderer groups it for a human, and the diff asks a different question entirely.
Keeping one snapshot type means the answer to "what is in this tree" cannot be
subtly different depending on which tool asked.

Two decisions in here are worth more than the code around them.

**Order is not inherited from the filesystem.** Entries are sorted by their
root-relative posix path as they are constructed, so two runs over equal trees
produce equal inventories, and :func:`to_json` produces equal *bytes*. A ledger
whose output reorders itself between runs cannot be diffed, and a baseline nobody
can diff is a baseline nobody checks.

**The kind is decided by decoding, not by sampling.** A file is ``text`` when it
decodes as UTF-8 *in full*; anything else is ``binary``. The popular heuristic
samples the first few kilobytes for a NUL byte or a printable-character ratio,
and that heuristic is exactly wrong for this tool: a markdown chapter with one
control character in it would be classified ``binary``, then classified ``text``
after an unrelated edit, and a diff of two inventories would report a change of
kind that no author made. Decoding the whole file costs one pass over bytes we
are already streaming past to hash, and it cannot flip for a reason unrelated to
the content.
"""

from __future__ import annotations

import hashlib
import json
import re
from codecs import getincrementaldecoder
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thesis_refinery.ledger import hashing
from thesis_refinery.ledger.hashing import DEFAULT_SKIP_DIRS, is_ignored, iter_files

__all__ = [
    "BINARY",
    "KINDS",
    "MISSING",
    "TEXT",
    "Entry",
    "Inventory",
    "InventoryError",
    "build",
    "entry_for",
    "from_json",
    "render_markdown",
    "summary",
    "to_json",
]

TEXT = "text"
BINARY = "binary"
MISSING = "missing"

KINDS: tuple[str, ...] = (TEXT, BINARY, MISSING)
"""Every value :attr:`Entry.kind` may take."""

JSON_INDENT = 2
"""Indentation of the JSON form: 2 spaces, so `git diff` on it is readable."""

DIGEST_CHARS = 12
"""Hex digits of the digest shown in the markdown table. The JSON keeps all 64."""

ROOT_GROUP = "(root)"
"""Group heading for entries that live directly in the tree root."""

_SIZE_STEP = 1024
_SIZE_UNITS: tuple[str, ...] = ("B", "KiB", "MiB", "GiB", "TiB")

_ENTRY_KEYS = frozenset({"path", "size", "sha256", "kind"})
_INVENTORY_KEYS = frozenset({"root", "entries"})
_DIGEST = re.compile(r"[0-9a-f]{64}")


class InventoryError(ValueError):
    """Raised when an inventory cannot be read back from its JSON form.

    A loader that shrugged at a malformed entry would hand its caller an
    inventory with a file silently missing from it, and every later answer of
    "no drift" would be an answer about a tree that was never described.
    """


@dataclass(frozen=True)
class Entry:
    """One file, as the ledger sees it."""

    path: str
    """Root-relative posix path. Never absolute: see :func:`build`."""

    size: int
    """Size in bytes, or ``0`` for an entry whose bytes could not be read."""

    sha256: str
    """Lower-case hex SHA-256, or ``""`` for a :data:`MISSING` entry."""

    kind: str
    """One of :data:`KINDS`."""

    def __post_init__(self) -> None:
        """Refuse an entry that contradicts itself.

        The kind decides what the other two fields may say: a file whose bytes
        were read carries a 64-digit digest, and one that could not be read
        carries neither a digest nor a size. The other combinations would let an
        entry claim to describe bytes it never measured -- and a digest that was
        never taken is, later, indistinguishable from a file that matched it.

        ``summary`` also groups by ``kind`` and the markdown report prints it, so
        a fourth value would flow into both without anything noticing.
        """
        if self.kind not in KINDS:
            raise ValueError(
                f"unknown entry kind {self.kind!r}; expected one of {', '.join(KINDS)}"
            )
        if self.kind == MISSING:
            if self.sha256 or self.size:
                raise ValueError(
                    f"{self.path!r} is {MISSING} but records size {self.size} and digest "
                    f"{self.sha256!r}; an entry with no readable bytes has nothing to record"
                )
            return
        if not _DIGEST.fullmatch(self.sha256):
            raise ValueError(
                f"{self.path!r} is {self.kind} but its sha256 {self.sha256!r} is not 64 "
                "lower-case hex digits"
            )
        if self.size < 0:
            raise ValueError(f"{self.path!r} has a negative size {self.size}")


@dataclass(frozen=True)
class Inventory:
    """A complete, ordered description of the files under one root."""

    root: str
    """The root as the caller spelled it, with forward slashes, never resolved.

    Resolving would stamp this machine's absolute path -- a user name, a home
    directory, a checkout location -- into a file that is meant to be committed
    and compared against a baseline. It would also make two machines disagree
    about a tree they both describe correctly.
    """

    entries: tuple[Entry, ...]

    def __post_init__(self) -> None:
        """Sort the entries, and refuse two entries for one path.

        Sorting here rather than at each call site is what makes :func:`to_json`
        byte-stable for equal inventories, and it stops a report's order from
        depending on the order a caller happened to add files in. Duplicate paths
        are refused because the file set would then be ambiguous: ``summary``
        would count one file twice and a byte total would be wrong in a way no
        reader could see.
        """
        entries = tuple(sorted(self.entries, key=lambda entry: entry.path))
        duplicated = sorted(
            path for path, count in Counter(entry.path for entry in entries).items() if count > 1
        )
        if duplicated:
            raise ValueError(f"inventory lists {', '.join(duplicated)} more than once")
        object.__setattr__(self, "entries", entries)


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def build(
    root: str | Path,
    *,
    skip_dirs: Sequence[str] = DEFAULT_SKIP_DIRS,
    ignore: Sequence[str] = (),
) -> Inventory:
    """Walk *root* and record every file the ledger is responsible for.

    ``skip_dirs`` names directories to stay out of entirely: build products and
    caches, matched by name at any depth (see
    :data:`~thesis_refinery.ledger.hashing.DEFAULT_SKIP_DIRS`). ``ignore``
    excludes project-relative directory prefixes that this project has decided
    are not part of the thesis -- an archive of old drafts, a folder of publisher
    PDFs, a vendored template.

    The difference between the two rules is deliberate. ``skip_dirs`` is a fact
    about tooling, and applies to any tree; ``ignore`` is a decision about *this*
    tree and is matched from its root, so ``drafts`` means ``./drafts`` and never
    ``manuscript/drafts``. It is applied to the enumerated paths rather than used
    to prune the walk, because the same predicate has to answer for a file that is
    no longer on disk -- which is what :func:`thesis_refinery.ledger.manifest.verify`
    asks it.
    """
    base = Path(root)
    entries = tuple(
        entry_for(base, relative)
        for relative in iter_files(base, skip_dirs=skip_dirs)
        if not is_ignored(relative.as_posix(), ignore)
    )
    return Inventory(root=base.as_posix(), entries=entries)


def entry_for(root: str | Path, relative: str | Path) -> Entry:
    """The :class:`Entry` describing *relative* under *root*.

    A file that cannot be read -- deleted while the walk was running, a symlink
    whose target is gone, a path this process may not open -- becomes a
    :data:`MISSING` entry rather than an exception. A snapshot that dies halfway
    through is worth less than one that says "this path was here a moment ago and
    has no readable bytes"; the summary counts it, so it cannot be mistaken for a
    file that is merely empty.
    """
    base = Path(root)
    posix = Path(relative).as_posix()
    try:
        size, digest, kind = _measure(base / relative)
    except OSError:
        return Entry(path=posix, size=0, sha256="", kind=MISSING)
    return Entry(path=posix, size=size, sha256=digest, kind=kind)


def _measure(path: Path) -> tuple[int, str, str]:
    """Size, digest and kind of *path*, from a single pass over its bytes.

    One pass rather than "hash it, then look at it": a file rewritten between two
    reads would give a digest of one revision and a kind of another, and this
    package's only real claim is that an entry describes one consistent set of
    bytes.

    Decoding is incremental, so a multi-byte character split across a block
    boundary is still a character. Once the bytes have failed to decode we stop
    decoding -- the verdict cannot change back -- but the hashing continues to the
    end of the file, because the digest has to cover all of it.
    """
    digest = hashlib.sha256()
    decoder = getincrementaldecoder("utf-8")()
    size = 0
    binary = False
    for block in hashing.iter_blocks(path):
        size += len(block)
        digest.update(block)
        if binary:
            continue
        try:
            decoder.decode(block)
        except UnicodeDecodeError:
            binary = True
    if not binary:
        try:
            # A truncated sequence at end of file is only visible here: the
            # incremental decoder holds the partial bytes back until asked.
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            binary = True
    return size, digest.hexdigest(), BINARY if binary else TEXT


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def to_json(inventory: Inventory) -> str:
    """The inventory as JSON text, identical for identical inventories.

    Keys are sorted and entries are already ordered, so the text is a pure
    function of the tree's *names and bytes*: no timestamps, no walk order, no
    machine-specific spelling of the root.

    ``ensure_ascii`` is off because a thesis is full of file names that are not
    ASCII, and a baseline that renders them as ``\\uXXXX`` escapes cannot be
    reviewed by the person who has to decide whether the diff is acceptable.

    The text ends with a newline, so writing it with ``Path.write_text`` produces
    a file no tool complains about.

    There is no version key, unlike a manifest: an inventory is a working
    snapshot that is regenerated, whereas a manifest is committed and must stay
    readable for as long as the baseline matters.
    """
    payload: dict[str, Any] = {
        "root": inventory.root,
        "entries": [
            {
                "path": entry.path,
                "size": entry.size,
                "sha256": entry.sha256,
                "kind": entry.kind,
            }
            for entry in inventory.entries
        ],
    }
    return json.dumps(payload, indent=JSON_INDENT, sort_keys=True, ensure_ascii=False) + "\n"


def from_json(text: str) -> Inventory:
    """Read back what :func:`to_json` wrote, refusing anything else.

    Every field is checked and every unknown key is refused, following the same
    rule as ``thesis.yml``: a key this build ignores is a claim it does not
    honour, and silently dropping a file from the inventory is indistinguishable
    from a file that never existed.
    """
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InventoryError(f"inventory is not valid JSON: {exc}") from exc

    if not isinstance(document, Mapping):
        raise InventoryError(
            f"an inventory must be a JSON object, not {type(document).__name__}"
        )
    unknown = set(document) - _INVENTORY_KEYS
    if unknown:
        raise InventoryError(
            f"unknown key(s) in inventory: {', '.join(sorted(unknown))}; "
            f"known: {', '.join(sorted(_INVENTORY_KEYS))}"
        )

    root = document.get("root")
    if not isinstance(root, str):
        raise InventoryError(f"inventory root must be a string, not {type(root).__name__}")

    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list):
        raise InventoryError(
            f"inventory entries must be a list, not {type(raw_entries).__name__}"
        )

    entries = tuple(_entry_from_json(item, position) for position, item in enumerate(raw_entries))
    try:
        return Inventory(root=root, entries=entries)
    except ValueError as exc:
        # The constructors own the cross-field rules -- one entry per path, and a
        # digest only where one was measured. Restate them in the error type the
        # callers of this module catch.
        raise InventoryError(f"inventory is inconsistent: {exc}") from exc


def _entry_from_json(raw: Any, position: int) -> Entry:
    """One entry, validated field by field. *position* locates it in the file.

    Field types are checked here so that a bad entry can be reported with its
    index; the *consistency* rules (which fields a given kind may carry) belong to
    :class:`Entry` and are enforced by constructing one.
    """
    where = f"entries[{position}]"
    if not isinstance(raw, Mapping):
        raise InventoryError(f"{where} must be an object, not {type(raw).__name__}")
    unknown = set(raw) - _ENTRY_KEYS
    if unknown:
        raise InventoryError(
            f"{where} has unknown key(s) {', '.join(sorted(unknown))}; "
            f"known: {', '.join(sorted(_ENTRY_KEYS))}"
        )

    path = raw.get("path")
    if not isinstance(path, str) or not path:
        raise InventoryError(f"{where}.path must be a non-empty string, got {path!r}")

    kind = raw.get("kind")
    if kind not in KINDS:
        raise InventoryError(f"{where}.kind must be one of {', '.join(KINDS)}, got {kind!r}")

    size = raw.get("size")
    if isinstance(size, bool) or not isinstance(size, int):
        raise InventoryError(f"{where}.size must be an integer, got {size!r}")

    digest = raw.get("sha256")
    if not isinstance(digest, str):
        raise InventoryError(f"{where}.sha256 must be a string, got {digest!r}")

    try:
        return Entry(path=path, size=size, sha256=digest, kind=kind)
    except ValueError as exc:
        raise InventoryError(f"{where} is inconsistent: {exc}") from exc


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def summary(inventory: Inventory) -> dict[str, int]:
    """Counts and byte totals, by kind, plus the totals over all kinds.

    The shape is fixed: every kind appears even when it has nothing, and each
    count comes with its byte total. A summary whose keys depended on what
    happened to be in the tree could not be compared between two runs, and a
    missing key reads exactly like a zero.

    Keys: ``files``, ``bytes``, then ``<kind>`` and ``<kind>_bytes`` for each of
    :data:`KINDS`. The byte total of :data:`MISSING` entries is always zero -- an
    entry with no readable bytes has no size -- and ``bytes`` counts only the
    bytes that were actually read.
    """
    counts = dict.fromkeys(KINDS, 0)
    sizes = dict.fromkeys(KINDS, 0)
    for entry in inventory.entries:
        counts[entry.kind] += 1
        sizes[entry.kind] += entry.size

    report: dict[str, int] = {
        "files": len(inventory.entries),
        "bytes": sum(sizes.values()),
    }
    for kind in KINDS:
        report[kind] = counts[kind]
        report[f"{kind}_bytes"] = sizes[kind]
    return report


def render_markdown(inventory: Inventory) -> str:
    """A human table of *inventory*, grouped by top-level directory.

    Grouped rather than flat because the first path segment is the only structure
    every thesis project agrees on, and "which of my folders is changing" is the
    question a reader actually has. Groups are ordered by name, so a report of a
    given tree always reads the same way.

    Digests are truncated to :data:`DIGEST_CHARS`: a wall of 64-character hashes
    is not reviewable, the JSON form carries the full value, and a truncated
    digest still identifies a file for anyone comparing two reports by eye.

    The text ends with a newline.
    """
    counts = summary(inventory)
    lines = [
        "# File inventory",
        "",
        f"Root: `{inventory.root}`",
        "",
        f"Entries: {counts['files']} ({counts[TEXT]} text, {counts[BINARY]} binary, "
        f"{counts[MISSING]} missing), {_human_size(counts['bytes'])}",
        "",
    ]

    groups = _grouped(inventory)
    if not groups:
        lines.extend(["_No files._", ""])

    for group, entries in groups.items():
        total = sum(entry.size for entry in entries)
        lines.extend(
            [
                f"## {group}",
                "",
                "| Path | Kind | Size | SHA-256 |",
                "| --- | --- | ---: | --- |",
            ]
        )
        for entry in entries:
            size = "-" if entry.kind == MISSING else _human_size(entry.size)
            digest = entry.sha256[:DIGEST_CHARS]
            digest_cell = f"`{digest}`" if digest else "-"
            lines.append(f"| `{_cell(entry.path)}` | {entry.kind} | {size} | {digest_cell} |")
        lines.extend(["", f"_{len(entries)} file(s), {_human_size(total)}._", ""])

    if groups:
        lines.extend(
            [
                f"_Digests are truncated to {DIGEST_CHARS} hex characters; "
                "the JSON form carries them in full._",
                "",
            ]
        )
    return "\n".join(lines).rstrip("\n") + "\n"


def _grouped(inventory: Inventory) -> dict[str, list[Entry]]:
    """Entries keyed by their top-level directory, groups ordered by name."""
    groups: dict[str, list[Entry]] = {}
    for entry in inventory.entries:
        head, separator, _ = entry.path.partition("/")
        groups.setdefault(head if separator else ROOT_GROUP, []).append(entry)
    return dict(sorted(groups.items()))


def _cell(text: str) -> str:
    """*text* as a markdown table cell that cannot break out of its own row."""
    return text.replace("|", "\\|").replace("`", "'")


def _human_size(size: int) -> str:
    """Bytes as a short string, e.g. ``12.3 KiB``."""
    value = float(size)
    unit = _SIZE_UNITS[0]
    for candidate in _SIZE_UNITS[1:]:
        if value < _SIZE_STEP:
            break
        value /= _SIZE_STEP
        unit = candidate
    return f"{value:.0f} {unit}" if unit == _SIZE_UNITS[0] else f"{value:.1f} {unit}"
