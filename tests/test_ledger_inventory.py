"""Tests for ``ledger.inventory``.

The interesting assertions here are about *decisions*: what counts as text, which
directories are excluded, and what an unreadable file becomes. Each of them is the
kind of choice that is invisible until it is wrong, at which point a ledger
reports a change nobody made.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from thesis_refinery.ledger import hashing, inventory
from thesis_refinery.ledger.inventory import BINARY, MISSING, TEXT, Entry, Inventory

DIGEST = "0" * 64
"""A syntactically valid digest, for entries that are not built from real files."""

SAMPLE_ENTRY: dict[str, object] = {
    "path": "a.txt",
    "size": 1,
    "sha256": DIGEST,
    "kind": "text",
}
"""A well-formed entry, to be broken one field at a time."""


def entry_json(**fields: object) -> str:
    """An inventory document holding one entry, with *fields* overriding it."""
    return json.dumps({"root": "r", "entries": [{**SAMPLE_ENTRY, **fields}]})


def duplicate_entry_json() -> str:
    """An inventory document that lists the same path twice."""
    return json.dumps({"root": "r", "entries": [SAMPLE_ENTRY, SAMPLE_ENTRY]})


def write_tree(
    root: Path,
    names: Sequence[str],
    contents: Mapping[str, str] | None = None,
) -> None:
    """Create every name under *root*; content defaults to something name-specific."""
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        default = f"content of {name}\n"
        path.write_text((contents or {}).get(name, default), encoding="utf-8")


def paths_of(snapshot: Inventory) -> list[str]:
    return [entry.path for entry in snapshot.entries]


# ---------------------------------------------------------------------------
# What the snapshot records
# ---------------------------------------------------------------------------


def test_build_records_path_size_digest_and_kind(tmp_path: Path) -> None:
    # write_bytes, not write_text: text mode rewrites "\n" to "\r\n" on Windows,
    # and these assertions are about the exact bytes on disk.
    (tmp_path / "chapter.qmd").write_bytes(b"A chapter.\n")
    (tmp_path / "scan.bin").write_bytes(b"\xff\xd8\xff")

    snapshot = inventory.build(tmp_path)

    assert snapshot.root == tmp_path.as_posix()
    assert snapshot.entries == (
        Entry(
            path="chapter.qmd",
            size=11,
            sha256=hashlib.sha256(b"A chapter.\n").hexdigest(),
            kind=TEXT,
        ),
        Entry(
            path="scan.bin",
            size=3,
            sha256=hashlib.sha256(b"\xff\xd8\xff").hexdigest(),
            kind=BINARY,
        ),
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"", TEXT),
        (b"plain ascii\n", TEXT),
        ("caf\u00e9 \u2014 na\u00efve\n".encode(), TEXT),
        (b"caf\xe9\n", BINARY),
        # A NUL byte is unusual in a thesis but it is still valid UTF-8. The
        # verdict follows decodability, not the usual NUL-in-the-first-block
        # heuristic, so this stays text -- deliberately.
        (b"a\x00b", TEXT),
        # A truncated multi-byte sequence at end of file is only visible when the
        # incremental decoder is asked to finish.
        (b"truncated \xc3", BINARY),
    ],
)
def test_kind_is_decided_by_utf8_decodability(
    tmp_path: Path, payload: bytes, expected: str
) -> None:
    (tmp_path / "sample.dat").write_bytes(payload)

    snapshot = inventory.build(tmp_path)

    assert [entry.kind for entry in snapshot.entries] == [expected]


def test_a_multi_byte_character_split_across_blocks_is_still_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decoding per block instead of incrementally would call this binary."""
    (tmp_path / "sample.txt").write_bytes("caf\u00e9\n".encode())
    monkeypatch.setattr(hashing, "CHUNK_BYTES", 1)

    entry = inventory.build(tmp_path).entries[0]

    assert entry.kind == TEXT
    assert entry.size == 6


def test_build_records_a_file_it_cannot_read_as_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that vanishes mid-walk is reported, not fatal, and not "empty"."""
    (tmp_path / "kept.txt").write_text("kept\n", encoding="utf-8")
    (tmp_path / "vanishing.txt").write_text("gone\n", encoding="utf-8")

    original = hashing.iter_blocks

    def flaky(path: str | Path):
        if Path(path).name == "vanishing.txt":
            raise FileNotFoundError(path)
        yield from original(path)

    monkeypatch.setattr(hashing, "iter_blocks", flaky)

    snapshot = inventory.build(tmp_path)

    assert [(entry.path, entry.kind) for entry in snapshot.entries] == [
        ("kept.txt", TEXT),
        ("vanishing.txt", MISSING),
    ]
    assert inventory.summary(snapshot)["missing"] == 1


def test_entry_for_describes_a_path_that_is_not_there(tmp_path: Path) -> None:
    entry = inventory.entry_for(tmp_path, Path("gone.docx"))

    assert entry == Entry(path="gone.docx", size=0, sha256="", kind=MISSING)


def test_the_root_is_kept_as_spelled_and_never_resolved(tmp_path: Path) -> None:
    """Resolving would stamp this machine's absolute path into a committed file."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "kept.txt").write_text("kept\n", encoding="utf-8")

    unresolved = tmp_path / "sub" / ".."

    assert inventory.build(unresolved).root == unresolved.as_posix()
    assert inventory.build(unresolved).root != str(unresolved.resolve())


# ---------------------------------------------------------------------------
# Exclusions
# ---------------------------------------------------------------------------


def test_ignore_excludes_a_directory_prefix_but_not_a_similar_name(tmp_path: Path) -> None:
    write_tree(tmp_path, ["drafts/old.docx", "drafts_kept/new.docx", "chapter.qmd"])

    assert paths_of(inventory.build(tmp_path, ignore=("drafts",))) == [
        "chapter.qmd",
        "drafts_kept/new.docx",
    ]


def test_build_products_are_skipped_by_default_and_included_on_request(tmp_path: Path) -> None:
    write_tree(tmp_path, ["chapter.qmd", "_outputs/index.docx", "__pycache__/x.pyc"])

    assert paths_of(inventory.build(tmp_path)) == ["chapter.qmd"]
    assert paths_of(inventory.build(tmp_path, skip_dirs=())) == [
        "__pycache__/x.pyc",
        "_outputs/index.docx",
        "chapter.qmd",
    ]


# ---------------------------------------------------------------------------
# JSON form
# ---------------------------------------------------------------------------


def test_json_is_identical_for_two_runs_over_the_same_tree(tmp_path: Path) -> None:
    write_tree(tmp_path, ["b.txt", "a/c.txt"])

    first = inventory.to_json(inventory.build(tmp_path))
    second = inventory.to_json(inventory.build(tmp_path))

    assert first == second
    assert first.endswith("\n")


def test_json_does_not_depend_on_creation_order_and_round_trips(tmp_path: Path) -> None:
    names = ["m.txt", "a/deep.txt", "z.txt"]
    contents = {name: f"{name} body\n" for name in names}
    write_tree(tmp_path, names, contents)
    before = inventory.build(tmp_path)

    for name in names:
        (tmp_path / name).unlink()
    write_tree(tmp_path, list(reversed(names)), contents)
    after = inventory.build(tmp_path)

    assert inventory.to_json(before) == inventory.to_json(after)
    assert inventory.from_json(inventory.to_json(before)) == before


def test_json_keeps_non_ascii_paths_readable(tmp_path: Path) -> None:
    """Escaped file names make a baseline nobody can review."""
    write_tree(tmp_path, ["\u7ae0\u8282/\u521d\u7a3f.qmd"])

    text = inventory.to_json(inventory.build(tmp_path))

    assert "\u521d\u7a3f.qmd" in text
    assert "\\u" not in text


def test_from_json_accepts_a_minimal_inventory() -> None:
    text = (
        '{"root": "manuscript", "entries": ['
        '{"path": "a.txt", "size": 1, "sha256": "' + DIGEST + '", "kind": "text"}]}'
    )

    snapshot = inventory.from_json(text)

    assert snapshot.root == "manuscript"
    assert snapshot.entries == (Entry(path="a.txt", size=1, sha256=DIGEST, kind=TEXT),)


@pytest.mark.parametrize(
    "text",
    [
        "not json at all",
        "[1, 2]",
        '{"entries": []}',
        '{"root": 7, "entries": []}',
        '{"root": "r", "entries": {}}',
        '{"root": "r", "entries": [], "extra": 1}',
        '{"root": "r", "entries": [1]}',
        entry_json(mode=420),
        entry_json(path=""),
        entry_json(kind="nonsense"),
        entry_json(size="1"),
        entry_json(size=True),
        entry_json(size=-1),
        entry_json(sha256="abc"),
        entry_json(sha256=5),
        entry_json(kind="missing"),
        duplicate_entry_json(),
    ],
    ids=[
        "not-json",
        "not-an-object",
        "no-root",
        "root-not-a-string",
        "entries-not-a-list",
        "unknown-top-level-key",
        "entry-not-an-object",
        "unknown-entry-key",
        "empty-path",
        "unknown-kind",
        "size-not-an-integer",
        "size-a-boolean",
        "negative-size",
        "digest-too-short",
        "digest-not-a-string",
        "missing-entry-with-a-digest",
        "duplicate-path",
    ],
)
def test_from_json_refuses_anything_malformed(text: str) -> None:
    with pytest.raises(inventory.InventoryError):
        inventory.from_json(text)


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


def test_entry_refuses_an_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unknown entry kind"):
        Entry(path="a.txt", size=1, sha256=DIGEST, kind="executable")


def test_entry_refuses_a_digest_it_never_measured() -> None:
    with pytest.raises(ValueError, match="64"):
        Entry(path="a.txt", size=1, sha256="", kind=TEXT)


def test_entry_refuses_a_missing_entry_that_records_bytes() -> None:
    with pytest.raises(ValueError, match="nothing to record"):
        Entry(path="a.txt", size=4, sha256="", kind=MISSING)


def test_inventory_sorts_its_entries() -> None:
    later = Entry(path="z.txt", size=1, sha256=DIGEST, kind=TEXT)
    earlier = Entry(path="a.txt", size=1, sha256=DIGEST, kind=TEXT)

    assert paths_of(Inventory(root="r", entries=(later, earlier))) == ["a.txt", "z.txt"]


def test_inventory_refuses_two_entries_for_one_path() -> None:
    entry = Entry(path="a.txt", size=1, sha256=DIGEST, kind=TEXT)

    with pytest.raises(ValueError, match="more than once"):
        Inventory(root="r", entries=(entry, entry))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def test_summary_counts_and_totals_bytes_by_kind(tmp_path: Path) -> None:
    (tmp_path / "a.qmd").write_bytes(b"12345")
    (tmp_path / "b.bin").write_bytes(b"\xff\xfe")

    snapshot = Inventory(
        root="r",
        entries=(*inventory.build(tmp_path).entries, Entry("gone.docx", 0, "", MISSING)),
    )

    assert inventory.summary(snapshot) == {
        "files": 3,
        "bytes": 7,
        "text": 1,
        "text_bytes": 5,
        "binary": 1,
        "binary_bytes": 2,
        "missing": 1,
        "missing_bytes": 0,
    }


def test_render_markdown_groups_by_top_level_directory(tmp_path: Path) -> None:
    write_tree(tmp_path, ["thesis.yml", "chapters/a.qmd", "chapters/b.qmd", "media/f1.png"])

    text = inventory.render_markdown(inventory.build(tmp_path))

    assert [line for line in text.splitlines() if line.startswith("## ")] == [
        "## (root)",
        "## chapters",
        "## media",
    ]
    chapters = text.split("## chapters", 1)[1].split("## media", 1)[0]
    assert "`chapters/a.qmd`" in chapters
    assert "`chapters/b.qmd`" in chapters
    assert "`thesis.yml`" not in chapters
    assert "2 file(s)" in chapters


def test_render_markdown_marks_an_entry_with_no_readable_bytes(tmp_path: Path) -> None:
    snapshot = Inventory(root="r", entries=(Entry("gone.docx", 0, "", MISSING),))

    text = inventory.render_markdown(snapshot)

    assert "| `gone.docx` | missing | - | - |" in text
    assert "0 text, 0 binary, 1 missing" in text
    assert "_No files._" not in text


def test_render_markdown_survives_an_empty_inventory() -> None:
    text = inventory.render_markdown(Inventory(root="r", entries=()))

    assert "_No files._" in text
    assert "Entries: 0" in text


def test_render_markdown_escapes_a_path_that_would_break_the_table() -> None:
    snapshot = Inventory(root="r", entries=(Entry("odd|name.txt", 1, DIGEST, TEXT),))

    assert "`odd\\|name.txt`" in inventory.render_markdown(snapshot)
