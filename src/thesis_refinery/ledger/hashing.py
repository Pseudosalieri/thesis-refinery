"""Content hashing, and a file enumeration two machines can agree on.

Two runs of the ledger must agree byte for byte, or a manifest cannot answer the
one question it exists to answer: *did anything change?* Three things that look
harmless until they bite are therefore ruled out here.

**Filesystem order.** The order a directory happens to return depends on the
filesystem, on the machine, and on whether a directory has been rewritten since
it was created. Everything this module yields is ordered by the root-relative
posix path, which is a property of the names alone.

**Reading a whole file into memory to hash it.** A thesis checkout contains its
own rendered PDF and its figure media. :func:`sha256_file` streams in fixed-size
blocks, so the memory a ledger run needs does not scale with the size of the
thesis it is describing.

**Hashing a build product.** ``_outputs/`` and the caches are rewritten by every
build, so including them would make verification fail for a reason nobody cares
about, and its failure would be ignored along with it. :data:`DEFAULT_SKIP_DIRS`
names them.

Which paths count (:func:`iter_files`, :func:`is_ignored`) and what their bytes
are (:func:`iter_blocks`, :func:`sha256_file`) are two halves of one idea, which
is why they share a module rather than a caller.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from pathlib import Path

__all__ = [
    "CHUNK_BYTES",
    "DEFAULT_SKIP_DIRS",
    "is_ignored",
    "iter_blocks",
    "iter_files",
    "sha256_file",
]

CHUNK_BYTES = 1024 * 1024
"""Read size for streaming hashes.

Deliberately large enough that a hashing pass is I/O-bound rather than
call-bound, and small enough that a run never needs more than a megabyte of
buffer per open file. It is a module constant and read at call time so that a
test can shrink it and exercise block boundaries without generating a gigabyte of
input.
"""

DEFAULT_SKIP_DIRS: tuple[str, ...] = (
    ".git",
    "_outputs",
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    ".ruff_cache",
    ".quarto",
    "node_modules",
)
"""Directory names the ledger never walks into.

Matched by name anywhere in the tree, so a nested ``manuscript/_outputs`` is
skipped as readily as a top-level one. These are exactly the directories whose
contents a build rewrites: version-control internals, build products, interpreter
and tool caches, and Quarto's intermediates. A ledger that included them would
report drift after every successful build, and a gate that fails when nothing is
wrong is a gate everyone learns to ignore.
"""


def sha256_file(path: str | Path) -> str:
    """Hex SHA-256 of the bytes of *path*, read in fixed-size blocks.

    Streaming rather than ``read_bytes()`` is not a micro-optimisation: the
    largest files in a thesis checkout are the rendered PDF and the ``.docx``
    itself, and hashing them must not need as much memory as they are large, or
    the tool stops working on the documents it was written for.

    A missing file, a directory, or a file this process may not read raises
    ``OSError``. Callers decide what an unreadable file *means* -- see
    :func:`thesis_refinery.ledger.inventory.entry_for`, which records it rather
    than giving up on the whole run.
    """
    digest = hashlib.sha256()
    for block in iter_blocks(path):
        digest.update(block)
    return digest.hexdigest()


def iter_blocks(path: str | Path) -> Iterator[bytes]:
    """The bytes of *path*, in ``CHUNK_BYTES`` blocks, as a stream.

    The single definition of *how a file is read* in this package, so that a
    digest and a text/binary verdict about the same file are always answers about
    the same bytes. A caller that opened the file twice could catch a file being
    rewritten underneath it and record an entry that never existed on disk:
    a digest from one revision and a verdict from another.

    ``CHUNK_BYTES`` is looked up at call time, so shrinking it in a test really
    does change the block boundaries of the read.
    """
    with Path(path).open("rb") as stream:
        while True:
            block = stream.read(CHUNK_BYTES)
            if not block:
                return
            yield block


def iter_files(
    root: str | Path,
    *,
    skip_dirs: Sequence[str] = (),
    skip_names: Sequence[str] = (),
) -> Iterator[Path]:
    """Every file under *root*, as root-relative paths, in a fixed order.

    Paths are relative because that is the name every consumer wants: the
    inventory, the manifest and the diff report all key on the repository-relative
    posix path. It is also the only spelling of a file that means the same thing
    on the machine that wrote a manifest and the machine that verifies it.

    ``skip_dirs`` is matched against directory *names* anywhere in the tree, so
    passing :data:`DEFAULT_SKIP_DIRS` prunes every ``_outputs`` at any depth.
    ``skip_names`` does the same for files (``Thumbs.db``, ``desktop.ini``, the
    ``~$`` lock files Word leaves behind).

    Directory symlinks are not followed: a link that points at an ancestor would
    make the walk infinite, and one that points outside the tree would make the
    inventory describe files that are not in it. File symlinks *are* read through,
    because their bytes are the bytes a build would use; a target that has gone
    missing shows up as an entry that cannot be read, not as silence.

    A *root* that does not exist raises ``OSError``. That is deliberate: an empty
    inventory for a mistyped path would let a verification pass on nothing at all.
    """
    base = Path(root)
    found: list[Path] = []
    _collect(base, base, frozenset(skip_dirs), frozenset(skip_names), found)
    # Sorted by the relative posix string, not by Path: Path comparison is
    # case-insensitive on Windows, which would make the order of a ledger depend
    # on the operating system that produced it.
    yield from sorted(found, key=Path.as_posix)


def _collect(
    directory: Path,
    base: Path,
    skip_dirs: frozenset[str],
    skip_names: frozenset[str],
    found: list[Path],
) -> None:
    """Append every wanted file under *directory* to *found*."""
    for entry in directory.iterdir():
        if entry.is_dir():
            if entry.is_symlink() or entry.name in skip_dirs:
                continue
            _collect(entry, base, skip_dirs, skip_names, found)
        elif entry.name not in skip_names:
            found.append(entry.relative_to(base))


def is_ignored(relpath: str, ignore: Sequence[str]) -> bool:
    """True when *relpath* is one of *ignore*, or lies underneath one of them.

    *ignore* entries are directory prefixes measured from the tree root, in the
    spirit of ``.gitignore``: ``manuscript/_outputs`` excludes that directory and
    everything in it, and does **not** exclude ``manuscript/_outputs_kept``. The
    match is on path segments, never on a bare string prefix, which is the whole
    difference between those two outcomes.

    The predicate is a pure function of the relative path, and that is the point.
    A caller that pruned its walk instead could not decide whether a file that is
    *gone* was excluded on purpose, and in a tamper report "excluded" and
    "deleted" must never be the same answer.

    Entries may be written with backslashes or a leading ``./``; both are
    normalised, because they are what a Windows shell hands you.
    """
    wanted = [prefix for prefix in map(_normalise, ignore) if prefix]
    if not wanted:
        return False
    candidate = _normalise(relpath)
    return any(candidate == prefix or candidate.startswith(f"{prefix}/") for prefix in wanted)


def _normalise(text: str) -> str:
    """A caller-supplied path as a root-relative, forward-slashed, bare path."""
    stripped = text.strip().replace("\\", "/")
    while stripped.startswith("./"):
        stripped = stripped[2:]
    return stripped.strip("/")
