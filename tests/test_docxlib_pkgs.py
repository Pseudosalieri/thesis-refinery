"""Tests for docxlib.pkgs.

The first two tests are the reason the module exists. Everything else is
plumbing that has to keep working for those two to mean anything.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from thesis_refinery.docxlib.ns import CONTENT_TYPE, qn
from thesis_refinery.docxlib.parse import parse
from thesis_refinery.docxlib.pkgs import (
    Package,
    PackageLimits,
    UnsafeArchiveError,
    content_types_part,
    rels_part_name,
)
from thesis_refinery.docxlib.xmlq import find, local_name, ordered_insert
from thesis_refinery.docxlib.xmlq import new as new_element

DOCUMENT = "word/document.xml"


def parts_of(path: Path) -> dict[str, bytes]:
    with Package(path) as package:
        return {name: package.read(name) for name in package.names()}


# ---------------------------------------------------------------------------
# The property this module exists for
# ---------------------------------------------------------------------------


def test_saving_without_changes_reproduces_every_part_byte_for_byte(
    sample_docx_with_media: Path, tmp_path: Path
) -> None:
    before = parts_of(sample_docx_with_media)

    with Package(sample_docx_with_media) as package:
        package.save(tmp_path / "copy.docx")

    assert parts_of(tmp_path / "copy.docx") == before


def test_editing_one_part_changes_exactly_that_part(
    sample_docx_with_media: Path, tmp_path: Path
) -> None:
    """A stage that changes one part must be shown to have changed one part."""
    before = parts_of(sample_docx_with_media)

    with Package(sample_docx_with_media) as package:
        assert package.changed == set()
        with package.edit_xml(DOCUMENT) as root:
            body = find(root, "w:body")
            assert body is not None
            # CT_Body's content model is not in SCHEMA_ORDER, so the escape hatch
            # is the honest choice here.
            ordered_insert(body, new_element("w:sectPr"), on_unknown="append")
        assert package.changed == {DOCUMENT}
        package.save(tmp_path / "edited.docx")

    after = parts_of(tmp_path / "edited.docx")
    assert set(after) == set(before)
    assert [name for name in before if before[name] != after[name]] == [DOCUMENT]


def test_a_no_op_edit_does_not_mark_the_part_changed(sample_docx: Path) -> None:
    """A part a stage merely *looked* at must not appear in ``changed``.

    python-docx's parser is configured with ``remove_blank_text=True``, so a
    parsed-and-re-serialised part is not byte-identical to the original even when
    nothing was edited. Comparing the new bytes against the part's stored bytes
    would therefore report every inspected part as changed, and ``changed`` --
    this module's whole reason to exist -- would stop meaning anything. The
    comparison that answers the question actually being asked is the serialised
    tree before versus after the mutation.
    """
    with Package(sample_docx) as package:
        with package.edit_xml(DOCUMENT):
            pass

        assert package.changed == set()


def test_a_real_edit_still_marks_the_part_changed(sample_docx: Path) -> None:
    """The control for the test above: silence must mean "no change", not "broken"."""
    with Package(sample_docx) as package:
        with package.edit_xml(DOCUMENT) as root:
            body = find(root, "w:body")
            assert body is not None
            ordered_insert(body, new_element("w:sectPr"), on_unknown="append")

        assert package.changed == {DOCUMENT}


def test_a_rewritten_part_keeps_the_original_xml_declaration(
    sample_docx: Path, tmp_path: Path
) -> None:
    with Package(sample_docx) as package:
        with package.edit_xml(DOCUMENT) as root:
            body = find(root, "w:body")
            assert body is not None
            ordered_insert(body, new_element("w:sectPr"), on_unknown="append")
        package.save(tmp_path / "out.docx")

    raw = parts_of(tmp_path / "out.docx")[DOCUMENT]
    assert raw.startswith(b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n')


def test_mutating_the_result_of_xml_does_not_persist(sample_docx: Path, tmp_path: Path) -> None:
    """Documents the contract: `xml()` is read-only until you call `replace_xml`."""
    with Package(sample_docx) as package:
        root = package.xml(DOCUMENT)
        body = find(root, "w:body")
        assert body is not None
        ordered_insert(body, new_element("w:sectPr"), on_unknown="append")
        package.save(tmp_path / "out.docx")

    saved_body = find(parse(parts_of(tmp_path / "out.docx")[DOCUMENT]), "w:body")
    assert saved_body is not None
    assert find(saved_body, "w:sectPr") is None


# ---------------------------------------------------------------------------
# Reading and writing
# ---------------------------------------------------------------------------


def test_names_preserves_archive_order(sample_docx: Path) -> None:
    with zipfile.ZipFile(sample_docx) as archive:
        expected = [i.filename for i in archive.infolist()]
    with Package(sample_docx) as package:
        assert package.names() == expected


def test_content_types_is_first(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        assert package.names()[0] == content_types_part()


def test_missing_part_raises_key_error(sample_docx: Path) -> None:
    with Package(sample_docx) as package, pytest.raises(KeyError):
        package.read("word/nope.xml")


def test_added_part_appears_and_reads_back(sample_docx: Path, tmp_path: Path) -> None:
    with Package(sample_docx) as package:
        package.write("word/extra.xml", b"<x/>")
        assert "word/extra.xml" in package
        assert package.read("word/extra.xml") == b"<x/>"
        package.save(tmp_path / "out.docx")

    assert parts_of(tmp_path / "out.docx")["word/extra.xml"] == b"<x/>"


def test_deleted_part_is_gone(sample_docx: Path, tmp_path: Path) -> None:
    with Package(sample_docx) as package:
        package.delete("word/styles.xml")
        assert "word/styles.xml" not in package
        package.save(tmp_path / "out.docx")

    assert "word/styles.xml" not in parts_of(tmp_path / "out.docx")


def test_deleting_a_missing_part_is_not_an_error(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        package.delete("word/nope.xml")


def test_replacing_a_deleted_part_undeletes_it(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        package.delete("word/styles.xml")
        package.write("word/styles.xml", b"<w:styles/>")
        assert "word/styles.xml" in package


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


def test_save_refuses_to_overwrite_the_input(sample_docx: Path) -> None:
    with Package(sample_docx) as package, pytest.raises(ValueError, match="refusing to save over"):
        package.save(sample_docx)

def test_save_in_place_requires_the_opt_in(sample_docx: Path) -> None:
    original = sample_docx.read_bytes()

    with Package(sample_docx) as package:
        package.write("word/extra.xml", b"<x/>")
        package.save(sample_docx, allow_in_place=True)

    assert sample_docx.read_bytes() != original
    assert "word/extra.xml" in parts_of(sample_docx)


def test_save_in_place_leaves_the_package_usable(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        package.write("word/extra.xml", b"<x/>")
        package.save(sample_docx, allow_in_place=True)
        # The object must describe what is now on disk, not what was there before.
        assert package.changed == set()
        assert "word/extra.xml" in package


def test_save_creates_missing_directories(sample_docx: Path, tmp_path: Path) -> None:
    destination = tmp_path / "deep" / "nested" / "out.docx"
    with Package(sample_docx) as package:
        package.save(destination)
    assert destination.is_file()


def test_save_refuses_a_git_tracked_destination(sample_docx: Path, git_repo: Path) -> None:
    from thesis_refinery.docxlib.save import TrackedFileError

    tracked = git_repo / "tracked.docx"
    tracked.write_bytes(sample_docx.read_bytes())
    import subprocess

    subprocess.run(["git", "-C", str(git_repo), "add", "tracked.docx"], check=True)

    with Package(sample_docx) as package, pytest.raises(TrackedFileError):
        package.save(tracked)


def test_save_can_skip_the_guard_when_asked(sample_docx: Path, git_repo: Path) -> None:
    import subprocess

    tracked = git_repo / "tracked.docx"
    tracked.write_bytes(sample_docx.read_bytes())
    subprocess.run(["git", "-C", str(git_repo), "add", "tracked.docx"], check=True)

    with Package(sample_docx) as package:
        package.save(tracked, guard=False)

    assert tracked.is_file()


# ---------------------------------------------------------------------------
# Archive safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../escape.xml",
        "word/../../escape.xml",
        "/absolute.xml",
        "C:/drive.xml",
        "",
    ],
)
def test_unsafe_member_names_are_rejected(evil_zip, name: str) -> None:
    with pytest.raises(UnsafeArchiveError):
        Package(evil_zip(name))


def test_backslash_member_names_are_rejected(sample_docx: Path) -> None:
    """OPC uses forward slashes. A foreign writer could still emit a backslash.

    This is asserted through ``write`` rather than through a crafted archive
    because Python's ``zipfile`` rewrites ``os.sep`` to ``/`` on Windows, which
    would make an archive-based test pass on Linux and fail here.
    """
    with Package(sample_docx) as package, pytest.raises(UnsafeArchiveError):
        package.write("word\\backslash.xml", b"payload")


def test_unsafe_names_are_rejected_on_write_too(sample_docx: Path) -> None:
    with Package(sample_docx) as package, pytest.raises(UnsafeArchiveError):
        package.write("../escape.xml", b"x")


def test_a_normal_part_name_is_accepted(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        assert DOCUMENT in package


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def test_part_count_limit_is_enforced(sample_docx: Path) -> None:
    with pytest.raises(UnsafeArchiveError, match="over the limit"):
        Package(sample_docx, limits=PackageLimits(max_parts=2))


def test_per_part_size_limit_is_enforced(sample_docx: Path) -> None:
    with pytest.raises(UnsafeArchiveError, match="per-part limit"):
        Package(sample_docx, limits=PackageLimits(max_part_bytes=16))


def test_total_size_limit_is_enforced(sample_docx: Path) -> None:
    with pytest.raises(UnsafeArchiveError, match="expands to more"):
        Package(sample_docx, limits=PackageLimits(max_total_bytes=64))


def test_generous_limits_accept_a_normal_document(sample_docx: Path) -> None:
    with Package(sample_docx, limits=PackageLimits()) as package:
        assert package.names()


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------


def test_next_media_name_continues_words_numbering(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        assert package.next_media_name("png") == "word/media/image1.png"


def test_next_media_name_skips_existing_media(sample_docx_with_media: Path) -> None:
    with Package(sample_docx_with_media) as package:
        assert package.next_media_name(".PNG") == "word/media/image2.png"


# ---------------------------------------------------------------------------
# Content types
# ---------------------------------------------------------------------------


def test_content_type_override_is_added_once(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        package.ensure_content_type_override("word/extra.xml", "application/xml")
        package.ensure_content_type_override("word/extra.xml", "application/xml")

        root = package.xml(content_types_part())
        overrides = [
            child for child in root if child.tag == qn("ct:Override") and child.get("PartName") == "/word/extra.xml"
        ]
        assert len(overrides) == 1


def test_every_default_precedes_every_override(sample_docx: Path) -> None:
    """The schema is sequence-typed; Word rejects a Types part that violates it."""
    with Package(sample_docx) as package:
        package.ensure_content_type_default("png", "image/png")
        package.ensure_content_type_override("word/extra.xml", "application/xml")

        names = [local_name(child.tag) for child in package.xml(content_types_part())]

    assert "Override" in names
    assert names.index("Override") > max(
        index for index, name in enumerate(names) if name == "Default"
    )


def test_content_type_default_is_updated_not_duplicated(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        package.ensure_content_type_default("xml", "application/xml")
        package.ensure_content_type_default("xml", "application/other")

        root = package.xml(content_types_part())
        xml_defaults = [
            child for child in root if child.tag == qn("ct:Default") and child.get("Extension") == "xml"
        ]

    assert len(xml_defaults) == 1
    assert xml_defaults[0].get("ContentType") == "application/other"


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------


def test_rels_part_name_follows_the_convention() -> None:
    assert rels_part_name("word/document.xml") == "word/_rels/document.xml.rels"
    assert rels_part_name("document.xml") == "_rels/document.xml.rels"
    assert rels_part_name("[Content_Types].xml") == "_rels/[Content_Types].xml.rels"


def test_add_relationship_creates_the_rels_part_and_numbers_from_one(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        package.delete("word/_rels/document.xml.rels")
        identifier = package.add_relationship(DOCUMENT, "image", "media/image1.png")

        assert identifier == "rId1"
        found = package.find_relationship(DOCUMENT, "image")
        assert found is not None
        assert found.get("Target") == "media/image1.png"


def test_relationship_ids_do_not_collide(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        existing = len(package.relationships(DOCUMENT))

        first = package.add_relationship(DOCUMENT, "image", "media/a.png")
        second = package.add_relationship(DOCUMENT, "image", "media/b.png")

        assert first != second
        assert len(package.relationships(DOCUMENT)) == existing + 2
        identifiers = [r.get("Id") for r in package.relationships(DOCUMENT)]
        assert len(identifiers) == len(set(identifiers))


def test_relationship_type_may_be_a_raw_uri(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        package.add_relationship(DOCUMENT, "http://example.invalid/rel", "x.xml")
        found = package.find_relationship(DOCUMENT, "http://example.invalid/rel")
        assert found is not None


def test_find_relationship_returns_none_when_absent(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        assert package.find_relationship(DOCUMENT, "image") is None


def test_relationships_of_a_part_without_rels_is_empty(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        assert package.relationships("word/styles.xml") == []


# ---------------------------------------------------------------------------
# synth
# ---------------------------------------------------------------------------


def test_generated_document_has_the_expected_skeleton(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        assert content_types_part() in package.names()
        assert "_rels/.rels" in package.names()
        assert DOCUMENT in package.names()
        assert "word/styles.xml" in package.names()


def test_generated_styles_are_parsable(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        root = package.xml("word/styles.xml")
    assert find(root, "w:style", **{"w:styleId": "BodyText"}) is not None


def test_generated_content_types_use_the_ct_namespace(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        root = package.xml(content_types_part())
    assert local_name(root.tag) == "Types"
    assert root.tag == qn("ct:Types")


def test_content_type_constants_are_used_by_the_generator(sample_docx: Path) -> None:
    with Package(sample_docx) as package:
        root = package.xml(content_types_part())
        document = find(root, "ct:Override", PartName="/word/document.xml")
    assert document is not None
    assert document.get("ContentType") == CONTENT_TYPE["document"]


def test_document_body_contains_the_paragraphs() -> None:
    from thesis_refinery.docxlib.parse import parse
    from thesis_refinery.docxlib.synth import document_xml, paragraph_xml
    from thesis_refinery.docxlib.xmlq import iter_descendants, paragraph_text

    root = parse(document_xml(paragraph_xml("Hello <world> & friends", style="BodyText")))
    paragraph = next(iter_descendants(root, "w:p"))

    assert paragraph_text(paragraph) == "Hello <world> & friends"
    assert find(paragraph, "w:pPr") is not None
