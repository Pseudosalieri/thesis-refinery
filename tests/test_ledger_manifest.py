"""Tests for ``ledger.manifest``.

The gate is only worth having if it can fail, so every failure mode it claims to
catch is provoked here: a changed file, a missing file, an added file, a lying
baseline, and a walk that quietly stops looking. The loader gets the same
treatment, one malformed manifest at a time.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from thesis_refinery.docxlib.save import TrackedFileError, is_git_tracked
from thesis_refinery.ledger import hashing, inventory, manifest
from thesis_refinery.ledger.manifest import ManifestError, Verification

DIGEST = "a" * 64
"""A syntactically valid digest, for manifests that describe nothing real."""


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


def baseline(tmp_path: Path) -> tuple[dict, Path]:
    """A manifest of a small project tree, and the tree it describes."""
    tree = tmp_path / "project"
    write_tree(tree, ["thesis.yml", "chapters/01.qmd", "chapters/02.qmd", "media/figure.png"])
    return manifest.build(tree), tree


def envelope(files: object, **overrides: object) -> str:
    """A manifest document as JSON text, with any field overridden."""
    document: dict[str, object] = {"version": 1, "root": "r", "files": files}
    document.update(overrides)
    return json.dumps(document)


def record(sha256: object = DIGEST, size: object = 1) -> dict[str, object]:
    return {"sha256": sha256, "size": size}


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def test_build_records_version_root_and_digests(tmp_path: Path) -> None:
    tree = tmp_path / "project"
    write_tree(tree, ["chapters/01.qmd"])
    (tree / "chapters" / "01.qmd").write_bytes(b"# One\n")

    document = manifest.build(tree)

    assert set(document) == {"version", "root", "files"}
    assert document["version"] == manifest.MANIFEST_VERSION
    assert document["root"] == tree.as_posix()
    assert document["files"] == {
        "chapters/01.qmd": {"sha256": hashlib.sha256(b"# One\n").hexdigest(), "size": 6}
    }


def test_the_manifest_is_a_projection_of_the_inventory(tmp_path: Path) -> None:
    """Two descriptions of one tree must not be able to disagree."""
    tree = tmp_path / "project"
    write_tree(tree, ["a.txt", "b/c.txt"])

    document = manifest.build(tree)
    snapshot = inventory.build(tree)

    assert document["root"] == snapshot.root
    assert document["files"] == {
        entry.path: {"sha256": entry.sha256, "size": entry.size} for entry in snapshot.entries
    }


def test_build_refuses_a_file_it_could_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A baseline that skips an unreadable file claims a coverage it lacks."""
    tree = tmp_path / "project"
    write_tree(tree, ["a.txt"])

    def unreadable(path: str | Path) -> object:
        raise PermissionError(str(path))

    monkeypatch.setattr(hashing, "iter_blocks", unreadable)

    with pytest.raises(ManifestError, match="cannot record a digest"):
        manifest.build(tree)


# ---------------------------------------------------------------------------
# Writing and loading
# ---------------------------------------------------------------------------


def test_write_then_load_round_trips_exactly(tmp_path: Path) -> None:
    document, _ = baseline(tmp_path)

    path = manifest.write(document, tmp_path / "baseline" / "manifest.json")

    assert path.is_file()
    assert manifest.load(path) == document


def test_write_is_byte_stable_across_two_builds(tmp_path: Path) -> None:
    _, tree = baseline(tmp_path)

    first = manifest.write(manifest.build(tree), tmp_path / "a.json").read_bytes()
    second = manifest.write(manifest.build(tree), tmp_path / "b.json").read_bytes()

    assert first == second
    assert first.endswith(b"\n")


def test_write_stores_what_load_would_return(tmp_path: Path) -> None:
    """An upper-case digest is accepted, then written in its one canonical spelling."""
    target = tmp_path / "manifest.json"
    document = {"version": 1, "root": "r", "files": {"a.txt": record(sha256="A" * 64)}}

    manifest.write(document, target)

    stored = json.loads(target.read_text(encoding="utf-8"))
    assert stored["files"]["a.txt"]["sha256"] == "a" * 64


def test_write_refuses_a_manifest_it_would_not_be_able_to_load(tmp_path: Path) -> None:
    target = tmp_path / "manifest.json"

    with pytest.raises(ManifestError):
        manifest.write({"version": 99, "root": "r", "files": {}}, target)

    assert not target.exists()


def test_load_reports_a_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="cannot read"):
        manifest.load(tmp_path / "nowhere.json")


def test_load_accepts_a_minimal_manifest(tmp_path: Path) -> None:
    """The positive control for the refusals below: an empty manifest is legal."""
    path = tmp_path / "manifest.json"
    path.write_text(envelope({}), encoding="utf-8")

    assert manifest.load(path) == {"version": 1, "root": "r", "files": {}}


def test_load_normalises_the_digest_case(tmp_path: Path) -> None:
    """Hex is case-insensitive; a case difference is not a change to the file."""
    path = tmp_path / "manifest.json"
    path.write_text(envelope({"a.txt": record(sha256="A" * 64)}), encoding="utf-8")

    assert manifest.load(path)["files"]["a.txt"]["sha256"] == "a" * 64


@pytest.mark.parametrize(
    "text",
    [
        "not json at all",
        "[]",
        json.dumps({"version": 1, "root": "r"}),
        envelope({}, version=2),
        envelope({}, version=True),
        envelope({}, version="1"),
        envelope({}, root=5),
        envelope({}, extra="unknown"),
        envelope([]),
        envelope({"a.txt": 5}),
        envelope({"a.txt": {"sha256": DIGEST}}),
        envelope({"a.txt": {"sha256": DIGEST, "size": 1, "mode": 420}}),
        envelope({"a.txt": record(sha256="zz")}),
        envelope({"a.txt": record(sha256=5)}),
        envelope({"a.txt": record(sha256="A" * 63 + "!")}),
        envelope({"a.txt": record(size=-1)}),
        envelope({"a.txt": record(size=True)}),
        envelope({"a.txt": record(size="1")}),
        envelope({"": record()}),
        envelope({"/etc/passwd": record()}),
        envelope({"../escape.txt": record()}),
        envelope({"a\\b.txt": record()}),
        envelope({"C:/elsewhere.txt": record()}),
    ],
    ids=[
        "not-json",
        "not-an-object",
        "no-files",
        "wrong-version",
        "boolean-version",
        "string-version",
        "root-not-a-string",
        "unknown-top-level-key",
        "files-not-an-object",
        "record-not-an-object",
        "record-without-size",
        "unknown-record-key",
        "digest-not-hex",
        "digest-not-a-string",
        "digest-wrong-length",
        "negative-size",
        "boolean-size",
        "size-not-an-integer",
        "empty-path",
        "absolute-path",
        "parent-traversal",
        "backslash-path",
        "windows-drive-path",
    ],
)
def test_load_refuses_anything_malformed(tmp_path: Path, text: str) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ManifestError):
        manifest.load(path)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def test_verify_reports_nothing_for_an_untouched_tree(tmp_path: Path) -> None:
    document, tree = baseline(tmp_path)

    verification = manifest.verify(document, tree)

    assert verification.ok
    assert verification == Verification(changed=(), missing=(), added=())


def test_verify_reports_a_changed_file_of_the_same_length(tmp_path: Path) -> None:
    """The size is not what detects this: the digest is."""
    document, tree = baseline(tmp_path)
    target = tree / "thesis.yml"
    target.write_bytes(b"x" * len(target.read_bytes()))

    verification = manifest.verify(document, tree)

    assert verification.changed == ("thesis.yml",)
    assert verification.missing == ()
    assert verification.added == ()
    assert verification.ok is False


def test_verify_reports_a_file_that_grew(tmp_path: Path) -> None:
    document, tree = baseline(tmp_path)
    with (tree / "chapters" / "01.qmd").open("ab") as stream:
        stream.write(b"\nand more\n")

    assert manifest.verify(document, tree).changed == ("chapters/01.qmd",)


def test_verify_reports_a_missing_file(tmp_path: Path) -> None:
    document, tree = baseline(tmp_path)
    (tree / "media" / "figure.png").unlink()

    verification = manifest.verify(document, tree)

    assert verification.missing == ("media/figure.png",)
    assert verification.changed == ()
    assert verification.added == ()


def test_verify_reports_a_file_replaced_by_a_directory(tmp_path: Path) -> None:
    document, tree = baseline(tmp_path)
    target = tree / "thesis.yml"
    target.unlink()
    target.mkdir()

    assert manifest.verify(document, tree).missing == ("thesis.yml",)


def test_verify_reports_an_added_file(tmp_path: Path) -> None:
    document, tree = baseline(tmp_path)
    write_tree(tree, ["chapters/03.qmd"])

    assert manifest.verify(document, tree).added == ("chapters/03.qmd",)


def test_verify_sorts_its_findings(tmp_path: Path) -> None:
    document, tree = baseline(tmp_path)
    write_tree(tree, ["z.txt", "a.txt", "m/n.txt"])

    assert manifest.verify(document, tree).added == ("a.txt", "m/n.txt", "z.txt")


def test_an_empty_tree_reports_every_recorded_file_missing(tmp_path: Path) -> None:
    document, _ = baseline(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()

    verification = manifest.verify(document, empty)

    assert verification.missing == tuple(sorted(document["files"]))
    assert verification.changed == ()
    assert verification.added == ()


def test_verifying_an_absent_tree_is_an_error_not_a_report(tmp_path: Path) -> None:
    """Drift and a mistyped path are different problems."""
    document, _ = baseline(tmp_path)

    with pytest.raises(ManifestError, match="not a directory"):
        manifest.verify(document, tmp_path / "nowhere")


def test_verify_uses_the_root_it_is_given_not_the_recorded_one(tmp_path: Path) -> None:
    """A baseline has to be verifiable on a machine where the checkout moved."""
    document, tree = baseline(tmp_path)
    moved = tmp_path / "elsewhere"
    shutil.copytree(tree, moved)
    document["root"] = "/a/different/machine/entirely"

    assert manifest.verify(document, moved).ok


def test_a_file_moved_into_a_build_directory_is_reported_missing(tmp_path: Path) -> None:
    """Exclusions apply to both sides, so a covered file cannot hide in one."""
    document, tree = baseline(tmp_path)
    (tree / "_outputs").mkdir()
    (tree / "thesis.yml").replace(tree / "_outputs" / "thesis.yml")

    verification = manifest.verify(document, tree)

    assert verification.missing == ("thesis.yml",)
    assert verification.added == ()


def test_an_ignore_entry_excludes_a_file_from_both_sides(tmp_path: Path) -> None:
    tree = tmp_path / "project"
    write_tree(tree, ["thesis.yml", "drafts/old.docx"])
    document = manifest.build(tree)

    assert manifest.verify(document, tree, ignore=("drafts",)).ok

    write_tree(tree, ["drafts/newer.docx"])
    assert manifest.verify(document, tree, ignore=("drafts",)).ok


def test_verify_walks_the_tree_itself_rather_than_reusing_the_builders_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shared walk lets one bug cancel itself out on both sides of the gate.

    ``CONTRIBUTING.md`` forbids a checker from sharing traversal code with what it
    checks. Reusing ``inventory.build`` here -- or the enumeration it is built on --
    would make a walk that silently stopped looking at a directory report "no
    drift" for the files in it.
    """
    document, tree = baseline(tmp_path)
    (tree / "thesis.yml").unlink()

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("verify must not reuse the builder's walk")

    monkeypatch.setattr(inventory, "build", refuse)
    monkeypatch.setattr(hashing, "iter_files", refuse)

    assert manifest.verify(document, tree).missing == ("thesis.yml",)


def test_verification_sorts_and_deduplicates_what_it_is_given() -> None:
    verification = Verification(changed=["b.txt", "a.txt", "b.txt"], missing=(), added=[])

    assert verification.changed == ("a.txt", "b.txt")
    assert verification.ok is False


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_render_says_ok_when_nothing_drifted(tmp_path: Path) -> None:
    document, tree = baseline(tmp_path)

    text = manifest.render(manifest.verify(document, tree))

    assert text.splitlines()[0] == "manifest verify: OK - 0 changed, 0 missing, 0 added"


def test_render_lists_every_non_empty_group(tmp_path: Path) -> None:
    document, tree = baseline(tmp_path)
    (tree / "thesis.yml").unlink()
    (tree / "chapters" / "01.qmd").write_text("edited\n", encoding="utf-8")
    write_tree(tree, ["extra.txt"])

    text = manifest.render(manifest.verify(document, tree))

    assert text.splitlines()[0] == "manifest verify: FAILED - 1 changed, 1 missing, 1 added"
    assert "changed:\n  chapters/01.qmd" in text
    assert "missing:\n  thesis.yml" in text
    assert "added:\n  extra.txt" in text


def test_render_caps_a_long_list(tmp_path: Path) -> None:
    document, tree = baseline(tmp_path)
    write_tree(tree, [f"file{index:02d}.txt" for index in range(manifest.MAX_LISTED + 5)])

    text = manifest.render(manifest.verify(document, tree))

    assert text.splitlines()[0].startswith(
        f"manifest verify: FAILED - 0 changed, 0 missing, {manifest.MAX_LISTED + 5} added"
    )
    assert "  file00.txt" in text
    assert "  file19.txt" in text
    assert "file20.txt" not in text
    assert "... and 5 more" in text


# ---------------------------------------------------------------------------
# Writing over a tracked baseline
# ---------------------------------------------------------------------------


def test_a_tracked_manifest_can_still_be_rewritten(tmp_path: Path, git_repo: Path) -> None:
    """The baseline is committed from its second revision on, and must stay updatable.

    With the tracked-file guard left on, this write would be refused -- and the
    manifest would then be edited by hand instead, which is how a baseline starts
    lying.
    """
    tree = git_repo / "project"
    write_tree(tree, ["thesis.yml"])
    target = git_repo / "manifest.json"

    document = manifest.build(tree)
    manifest.write(document, target)
    subprocess.run(["git", "-C", str(git_repo), "add", "manifest.json"], check=True)
    assert is_git_tracked(target)

    document["root"] = "regenerated"
    manifest.write(document, target)

    assert manifest.load(target)["root"] == "regenerated"


def test_write_can_be_asked_for_the_strict_tracked_file_refusal(
    tmp_path: Path, git_repo: Path
) -> None:
    tree = git_repo / "project"
    write_tree(tree, ["thesis.yml"])
    target = git_repo / "manifest.json"
    document = manifest.build(tree)

    manifest.write(document, target)
    subprocess.run(["git", "-C", str(git_repo), "add", "manifest.json"], check=True)

    with pytest.raises(TrackedFileError):
        manifest.write(document, target, guard=True)
