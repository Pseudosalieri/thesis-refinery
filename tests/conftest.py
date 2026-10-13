"""Shared fixtures.

The sample documents are synthesised rather than committed as binaries: a
fixture you can read is a fixture you can reason about, and it keeps the
repository free of opaque blobs.
"""

from __future__ import annotations

import hashlib
import subprocess
import zipfile
from pathlib import Path

import pytest

from thesis_refinery.docxlib.synth import build_docx, paragraph_xml, style_xml, styles_xml

# A one-pixel PNG: the smallest thing that is honestly an image.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db4"
    "0000000049454e44ae426082"
)

SAMPLE_STYLES = styles_xml(
    [
        style_xml("Normal", "Normal", default=True),
        style_xml("Heading1", "Heading 1", based_on="Normal", outline_level=0),
        style_xml(
            "FirstParagraph",
            "First Paragraph",
            based_on="Normal",
            next_style="BodyText",
            paragraph_properties='<w:ind w:firstLine="480"/>',
        ),
        style_xml(
            "BodyText",
            "Body Text",
            based_on="Normal",
            paragraph_properties='<w:ind w:firstLine="480"/><w:spacing w:line="360" w:lineRule="auto"/>',
        ),
    ]
)


def _sample_body() -> str:
    return "\n".join(
        [
            paragraph_xml("A sample first paragraph.", style="FirstParagraph"),
            paragraph_xml("A second paragraph.", style="BodyText"),
            paragraph_xml("A heading", style="Heading1"),
        ]
    )


@pytest.fixture
def sample_docx(tmp_path: Path) -> Path:
    """A small, valid, self-consistent ``.docx``."""
    return build_docx(
        tmp_path / "sample.docx",
        body=_sample_body(),
        styles=SAMPLE_STYLES,
    )


@pytest.fixture
def sample_docx_with_media(tmp_path: Path) -> Path:
    """The same document plus a media part, so streaming can be exercised."""
    return build_docx(
        tmp_path / "sample-media.docx",
        body=_sample_body(),
        styles=SAMPLE_STYLES,
        media={"word/media/image1.png": TINY_PNG},
        extra_defaults={"png": "image/png"},
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A throwaway git repository, for tests about what git would publish."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    return repo


@pytest.fixture
def evil_zip(tmp_path: Path):
    """Build a zip with an arbitrary member name, for archive-safety tests."""

    def build(name: str) -> Path:
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        path = tmp_path / f"evil-{digest}.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(name, b"payload")
        return path

    return build
