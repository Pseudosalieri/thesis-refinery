"""Copy-before-write, atomic replace, and the guard against clobbering tracked files.

The rule this module exists to enforce: **a stage may never modify a file in
place.** Stages read an input and write a *new* output. Three consequences,
all of them deliberate:

* A failed stage cannot have half-written its input, so the input is still
  available for diagnosis.
* "Which stage broke this?" always has one candidate answer.
* A file that git tracks — a hand-authored input, a template, an example — cannot
  be silently rewritten by a build. :func:`guard_writable` refuses.

The guard is deliberately permissive outside a git work tree. A user's thesis
folder is very often not a repository at all, and refusing to write there would
make the tool useless for exactly the people it is for.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

__all__ = [
    "TrackedFileError",
    "atomic_write_bytes",
    "copy_before_write",
    "guard_writable",
    "is_git_tracked",
]


class TrackedFileError(RuntimeError):
    """Raised when a write would overwrite a file that git tracks."""


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes] | None:
    """Run git, returning ``None`` when git is unavailable or not usable."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None


def is_git_tracked(path: Path) -> bool:
    """True when *path* is tracked by the git repository containing it.

    Returns ``False`` when the path is outside a work tree, when git is not
    installed, or when the repository cannot be read. Those are all cases where
    the guard cannot help, and blocking the write would be worse than allowing
    it.
    """
    path = path.resolve()
    if not path.exists():
        return False

    directory = path.parent
    probe = _git(["rev-parse", "--is-inside-work-tree"], directory)
    if probe is None or probe.returncode != 0:
        return False

    # --error-unmatch makes a non-zero exit mean "not tracked". The pathspec form
    # keeps the answer to this one file instead of the whole index.
    tracked = _git(["ls-files", "--error-unmatch", "--", path.name], directory)
    return tracked is not None and tracked.returncode == 0


def guard_writable(path: Path) -> None:
    """Raise :class:`TrackedFileError` if writing *path* would clobber tracked content.

    Call this immediately before any write to a path that may already exist.
    """
    if is_git_tracked(path):
        raise TrackedFileError(
            f"refusing to write over {path}: it is tracked by git.\n"
            "Stages write new files; overwriting a tracked file is how a build "
            "destroys the input it was supposed to protect."
        )


def atomic_write_bytes(path: Path, data: bytes, *, guard: bool = True) -> None:
    """Write *data* to *path* atomically, refusing to clobber a tracked file.

    The temp file is created in the destination directory so that the final
    :func:`os.replace` stays on one filesystem and is therefore atomic.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if guard:
        guard_writable(path)

    handle, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        # Path.replace is os.replace: same filesystem, therefore atomic.
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def copy_before_write(source: Path, destination: Path, *, guard: bool = True) -> Path:
    """Copy *source* to *destination*, creating parents, and return *destination*.

    This is the sanctioned way to start a modification: make the copy first, edit
    the copy, leave the original alone.
    """
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if guard:
        guard_writable(destination)
    shutil.copy2(source, destination)
    return destination
