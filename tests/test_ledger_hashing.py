"""Tests for ``ledger.hashing``.

Two of these are the reason the module exists: the digest must cover every byte
of a streamed file, and the enumeration order must be a property of the names
rather than of the machine. The rest pins the exclusion rules, because a ledger
that quietly includes a build product fails on every run and then gets ignored.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest

from thesis_refinery.ledger import hashing


def write_files(root: Path, names: Sequence[str]) -> None:
    """Create every name under *root*, with content that depends on the name."""
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"content of {name}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Digests
# ---------------------------------------------------------------------------


def test_sha256_file_matches_hashlib_over_the_whole_file(tmp_path: Path) -> None:
    payload = b"a chapter, in bytes\n"
    target = tmp_path / "chapter.qmd"
    target.write_bytes(payload)

    assert hashing.sha256_file(target) == hashlib.sha256(payload).hexdigest()


def test_sha256_file_covers_every_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A block loop that drops or repeats bytes still returns *a* digest.

    Shrinking the block size to a prime number makes the last, partial block
    land on a different offset than the full ones.
    """
    payload = bytes(range(256)) * 5 + b"tail"
    target = tmp_path / "figure.bin"
    target.write_bytes(payload)
    monkeypatch.setattr(hashing, "CHUNK_BYTES", 7)

    assert hashing.sha256_file(target) == hashlib.sha256(payload).hexdigest()


def test_iter_blocks_streams_in_pieces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reader must not need the whole file in memory to hand it on."""
    target = tmp_path / "text.txt"
    target.write_bytes(b"0123456789")
    monkeypatch.setattr(hashing, "CHUNK_BYTES", 4)

    assert list(hashing.iter_blocks(target)) == [b"0123", b"4567", b"89"]


def test_iter_blocks_of_an_empty_file_yields_nothing(tmp_path: Path) -> None:
    target = tmp_path / "empty.txt"
    target.write_bytes(b"")

    assert list(hashing.iter_blocks(target)) == []


def test_sha256_file_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        hashing.sha256_file(tmp_path / "nowhere.bin")


def test_sha256_file_refuses_a_directory(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        hashing.sha256_file(tmp_path)


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------


def test_iter_files_yields_relative_posix_paths_in_a_fixed_order(tmp_path: Path) -> None:
    write_files(tmp_path, ["b.txt", "a/2.txt", "a.txt", "a/1.txt", "z/y/x.txt"])

    assert [path.as_posix() for path in hashing.iter_files(tmp_path)] == [
        "a.txt",
        "a/1.txt",
        "a/2.txt",
        "b.txt",
        "z/y/x.txt",
    ]


def test_order_does_not_depend_on_the_platforms_case_rules(tmp_path: Path) -> None:
    """``sorted`` over ``Path`` is case-insensitive on Windows; this must not be.

    Two machines hashing the same tree have to produce the same manifest, and a
    manifest is a text file. A case-insensitive order makes it depend on the
    operating system that wrote it.
    """
    write_files(tmp_path, ["a.txt", "B.txt"])

    assert [path.name for path in hashing.iter_files(tmp_path)] == ["B.txt", "a.txt"]


def test_order_does_not_depend_on_creation_order(tmp_path: Path) -> None:
    names = ["m.txt", "a/deep.txt", "z.txt", "a/b.txt"]
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_files(first, names)
    write_files(second, list(reversed(names)))

    listed = [path.as_posix() for path in hashing.iter_files(first)]

    assert listed == ["a/b.txt", "a/deep.txt", "m.txt", "z.txt"]
    assert [path.as_posix() for path in hashing.iter_files(second)] == listed


def test_skip_dirs_prunes_a_name_at_any_depth(tmp_path: Path) -> None:
    write_files(
        tmp_path, ["kept.txt", "deep/kept.txt", "_outputs/index.docx", "deep/_outputs/x.docx"]
    )

    assert [path.as_posix() for path in hashing.iter_files(tmp_path, skip_dirs=("_outputs",))] == [
        "deep/kept.txt",
        "kept.txt",
    ]


def test_default_skip_dirs_prunes_every_build_product_and_cache(tmp_path: Path) -> None:
    """The default set is the contract; assert it by behaviour, not by listing it."""
    for name in hashing.DEFAULT_SKIP_DIRS:
        write_files(tmp_path, [f"{name}/artifact.bin"])
    write_files(tmp_path, ["chapter.qmd"])

    pruned = [
        path.as_posix()
        for path in hashing.iter_files(tmp_path, skip_dirs=hashing.DEFAULT_SKIP_DIRS)
    ]

    assert pruned == ["chapter.qmd"]
    assert len(list(hashing.iter_files(tmp_path))) == len(hashing.DEFAULT_SKIP_DIRS) + 1


def test_skip_names_prunes_files_at_any_depth(tmp_path: Path) -> None:
    write_files(tmp_path, ["Thumbs.db", "chapter.qmd", "media/Thumbs.db"])

    assert [
        path.as_posix() for path in hashing.iter_files(tmp_path, skip_names=("Thumbs.db",))
    ] == ["chapter.qmd"]


def test_a_file_named_like_a_skipped_directory_is_still_listed(tmp_path: Path) -> None:
    """The rule is about directories; a same-named file is not a build product."""
    write_files(tmp_path, ["_outputs"])

    assert [path.as_posix() for path in hashing.iter_files(tmp_path, skip_dirs=("_outputs",))] == [
        "_outputs"
    ]


def test_a_missing_root_is_an_error_not_an_empty_listing(tmp_path: Path) -> None:
    """An empty listing for a mistyped root would let a verification pass on nothing."""
    with pytest.raises(FileNotFoundError):
        list(hashing.iter_files(tmp_path / "nowhere"))


# ---------------------------------------------------------------------------
# Ignore prefixes
# ---------------------------------------------------------------------------


def test_is_ignored_matches_whole_path_segments_from_the_root() -> None:
    assert hashing.is_ignored("drafts", ["drafts"])
    assert hashing.is_ignored("drafts/2024/old.docx", ["drafts"])
    assert not hashing.is_ignored("drafts_kept/old.docx", ["drafts"])
    assert not hashing.is_ignored("manuscript/drafts/old.docx", ["drafts"])


def test_is_ignored_normalises_shell_spellings_and_ignores_empty_entries() -> None:
    assert hashing.is_ignored("manuscript/_outputs/a.docx", ["./manuscript/_outputs/"])
    assert hashing.is_ignored("manuscript/_outputs/a.docx", ["manuscript\\_outputs"])
    assert not hashing.is_ignored("a.txt", [""])
    assert not hashing.is_ignored("a.txt", [])
