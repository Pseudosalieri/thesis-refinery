"""OPC package (``.docx``) surgery that leaves untouched parts untouched.

A ``.docx`` is a zip of parts. Almost every tool that edits one rebuilds the
whole archive, which means a stage that changes a single margin also rewrites
every image, every header, and every content type. That is not just slow: it
destroys the ability to answer *"did this stage change anything it should not
have?"*, because the answer is always "everything".

:class:`Package` is copy-on-write at the part level. You read a part, you replace
a part, and on save every part you did not touch is **streamed through
unmodified** — decompressed and recompressed, never parsed, never re-serialised.
A stage that intends to change one part can be shown to have changed one part.

Two further consequences of that design:

* Parts are never loaded into memory unless asked for, so a 300 MB thesis does
  not need 300 MB of RAM.
* Saving requires an explicit destination. Writing back over the input is
  refused unless you ask for it in so many words, which makes "copy before
  write" the path of least resistance rather than a rule to remember.

python-docx's ``Document.save()`` must **not** be used anywhere in this project.
It re-serialises every part, which is exactly the property above that we need.
Use python-docx for its element classes and its read-only conveniences; write
through :class:`Package`.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from thesis_refinery.docxlib import save as _save
from thesis_refinery.docxlib.ns import NSMAP, RELTYPE, qn
from thesis_refinery.docxlib.parse import DECLARATION, declaration_of, parse
from thesis_refinery.docxlib.xmlq import new, ordered_insert

__all__ = [
    "Package",
    "PackageLimits",
    "UnsafeArchiveError",
    "content_types_part",
    "rels_part_name",
]

_PR_NS = NSMAP["pr"]


class UnsafeArchiveError(ValueError):
    """Raised when an archive contains a member name we refuse to handle."""


@dataclass(frozen=True)
class PackageLimits:
    """Guards against decompression bombs.

    Deliberately generous: a thesis with a hundred megabytes of figure media is
    normal, and a false rejection is worse than a slow build. These exist to stop
    a hostile archive, not a large one.
    """

    max_parts: int = 20_000
    max_part_bytes: int = 512 * 1024 * 1024
    max_total_bytes: int = 2 * 1024 * 1024 * 1024


# An OPC part name is a relative, forward-slashed path. Anything else is either
# non-conformant or an attempt to escape the package.
_UNSAFE_NAME = re.compile(
    r"^$"  # empty
    r"|^/"  # absolute POSIX path
    r"|^[A-Za-z]:"  # drive letter
    r"|\\"  # backslash: OPC part names use forward slashes only
    r"|(^|/)\.\.(/|$)"  # parent traversal
)


def _validate_member_name(name: str) -> None:
    if _UNSAFE_NAME.search(name):
        raise UnsafeArchiveError(
            f"refusing archive member {name!r}: OPC part names must be relative, "
            "forward-slashed paths without '..' segments"
        )


def content_types_part() -> str:
    """The content-types part, which is the one part with a non-path name."""
    return "[Content_Types].xml"


def rels_part_name(part_name: str) -> str:
    """Relationship part for *part_name*.

    ``word/document.xml`` → ``word/_rels/document.xml.rels``
    """
    directory, _, filename = part_name.rpartition("/")
    if directory:
        return f"{directory}/_rels/{filename}.rels"
    return f"_rels/{filename}.rels"


class Package:
    """A ``.docx`` (or any OPC package) opened for copy-on-write part surgery."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        limits: PackageLimits | None = None,
    ) -> None:
        self.path = Path(path)
        self.limits = limits or PackageLimits()
        self._zip = zipfile.ZipFile(self.path, "r")
        self._open()

    def _open(self) -> None:
        self._order: list[str] = []
        self._infos: dict[str, zipfile.ZipInfo] = {}
        self._replaced: dict[str, bytes] = {}
        self._added: dict[str, bytes] = {}
        self._deleted: set[str] = set()
        self._parsed: dict[str, etree._Element] = {}
        self._declarations: dict[str, bytes] = {}

        infos = [i for i in self._zip.infolist() if not i.is_dir()]
        if len(infos) > self.limits.max_parts:
            raise UnsafeArchiveError(
                f"{self.path.name} has {len(infos)} parts, over the limit of "
                f"{self.limits.max_parts}"
            )

        total = 0
        for info in infos:
            _validate_member_name(info.filename)
            if info.file_size > self.limits.max_part_bytes:
                raise UnsafeArchiveError(
                    f"part {info.filename!r} declares {info.file_size} bytes, over the "
                    f"per-part limit of {self.limits.max_part_bytes}"
                )
            total += info.file_size
            if total > self.limits.max_total_bytes:
                raise UnsafeArchiveError(
                    f"{self.path.name} expands to more than {self.limits.max_total_bytes} bytes"
                )
            self._order.append(info.filename)
            self._infos[info.filename] = info

    def reopen(self) -> None:
        """Re-read the file on disk, discarding all pending changes."""
        self._zip.close()
        self._zip = zipfile.ZipFile(self.path, "r")
        self._open()

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._zip.close()

    def __enter__(self) -> Package:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- reading -----------------------------------------------------------

    def names(self) -> list[str]:
        """Part names, in archive order, including pending additions."""
        return [name for name in (*self._order, *self._added) if name not in self._deleted]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self.names()

    def exists(self, name: str) -> bool:
        return name in self

    def read(self, name: str) -> bytes:
        """Raw bytes of a part."""
        if name in self._deleted:
            raise KeyError(f"{name!r} was deleted from {self.path.name}")
        if name in self._replaced:
            return self._replaced[name]
        if name in self._added:
            return self._added[name]
        if name not in self._infos:
            raise KeyError(f"{self.path.name} has no part named {name!r}")
        return self._zip.read(name)

    def xml(self, name: str) -> etree._Element:
        """Parse a part, caching the result and its original declaration.

        Mutating the returned element does **not** persist anything. Follow up
        with :meth:`replace_xml`, or use :meth:`edit_xml`, which cannot be
        forgotten.
        """
        if name in self._parsed:
            return self._parsed[name]
        raw = self.read(name)
        self._declarations[name] = declaration_of(raw)
        root = parse(raw)
        self._parsed[name] = root
        return root

    # -- writing -----------------------------------------------------------

    def write(self, name: str, data: bytes) -> None:
        """Replace, or create, a part from raw bytes."""
        _validate_member_name(name)
        if name in self._added or name not in self._infos:
            self._added[name] = data
        else:
            self._replaced[name] = data
        self._parsed.pop(name, None)
        self._deleted.discard(name)

    def replace_xml(self, name: str, root: etree._Element) -> None:
        """Persist *root* as the new content of *name*.

        The original part's XML declaration is preserved verbatim, so a rewritten
        part still looks like Word wrote it.
        """
        declaration = self._declarations.get(name)
        if declaration is None:
            declaration = self._declaration_of_part(name)
        body = etree.tostring(root, encoding="UTF-8", xml_declaration=False)
        self.write(name, declaration + body)

    def _declaration_of_part(self, name: str) -> bytes:
        try:
            return declaration_of(self.read(name))
        except KeyError:
            return DECLARATION

    @contextmanager
    def edit_xml(self, name: str) -> Iterator[etree._Element]:
        """Parse a part, yield it for mutation, and persist it on the way out.

        The context manager form exists because forgetting ``replace_xml`` is a
        silent no-op, and silent no-ops are the failure mode this project exists
        to eliminate.

        A part that ends up unchanged is **not** written. That matters more than
        it looks: python-docx's parser is configured with
        ``remove_blank_text=True``, so parsing and re-serialising a part is not
        byte-identical to the original even when nothing was edited. Comparing
        the new bytes against the part's stored bytes would therefore report
        every part a stage merely *looked* at as changed, and
        :attr:`changed` -- the module's whole reason to exist -- would stop
        meaning anything. Comparing the serialised tree before and after the
        mutation is the comparison that answers the question actually being
        asked.
        """
        root = self.xml(name)
        before = etree.tostring(root)
        yield root
        if etree.tostring(root) != before:
            self.replace_xml(name, root)

    def delete(self, name: str) -> None:
        """Remove a part. Deleting a missing part is not an error."""
        self._deleted.add(name)
        self._added.pop(name, None)
        self._replaced.pop(name, None)
        self._parsed.pop(name, None)

    @property
    def changed(self) -> set[str]:
        """Names of parts that differ from the input."""
        return set(self._replaced) | set(self._added) | self._deleted

    # -- media -------------------------------------------------------------

    _MEDIA_PATTERN = re.compile(r"^word/media/image(\d+)\.(\w+)$")

    def next_media_name(self, extension: str) -> str:
        """An unused ``word/media/imageN.ext`` name.

        Word's own naming convention is followed, so a document we add media to
        is indistinguishable from one Word built.
        """
        extension = extension.lstrip(".").lower()
        highest = 0
        for name in self.names():
            match = self._MEDIA_PATTERN.match(name)
            if match:
                highest = max(highest, int(match.group(1)))
        return f"word/media/image{highest + 1}.{extension}"

    # -- content types -----------------------------------------------------

    def ensure_content_type_default(self, extension: str, content_type: str) -> None:
        """Declare a default content type for an extension, e.g. ``png``."""
        extension = extension.lstrip(".").lower()
        with self.edit_xml(content_types_part()) as root:
            for child in root:
                if (
                    child.tag == qn("ct:Default")
                    and child.get("Extension", "").lower() == extension
                ):
                    child.set("ContentType", content_type)
                    return
            ordered_insert(
                root,
                new("ct:Default", Extension=extension, ContentType=content_type),
            )

    def ensure_content_type_override(self, part_name: str, content_type: str) -> None:
        """Declare a content type for one specific part."""
        wanted = part_name if part_name.startswith("/") else f"/{part_name}"
        with self.edit_xml(content_types_part()) as root:
            for child in root:
                if child.tag == qn("ct:Override") and child.get("PartName") == wanted:
                    child.set("ContentType", content_type)
                    return
            ordered_insert(
                root,
                new("ct:Override", PartName=wanted, ContentType=content_type),
            )

    # -- relationships -----------------------------------------------------

    def relationships(self, part_name: str) -> list[etree._Element]:
        """Relationship elements declared for *part_name*."""
        name = rels_part_name(part_name)
        if name not in self:
            return []
        return [c for c in self.xml(name) if c.tag == qn("pr:Relationship")]

    def next_relationship_id(self, part_name: str) -> str:
        """An unused ``rIdN`` for *part_name*."""
        highest = 0
        for relationship in self.relationships(part_name):
            identifier = relationship.get("Id", "")
            if identifier.startswith("rId") and identifier[3:].isdigit():
                highest = max(highest, int(identifier[3:]))
        return f"rId{highest + 1}"

    def add_relationship(
        self,
        part_name: str,
        reltype: str,
        target: str,
        *,
        mode: str | None = None,
    ) -> str:
        """Add a relationship to *part_name* and return its new ``rId``.

        *reltype* is a key of :data:`~thesis_refinery.docxlib.ns.RELTYPE` or a
        full URI.
        """
        resolved = RELTYPE.get(reltype, reltype)
        rels_name = rels_part_name(part_name)

        if rels_name not in self:
            # Word writes .rels parts with a default namespace; match that rather
            # than substituting a prefixed form Word would not have produced.
            root = etree.Element(qn("pr:Relationships"), nsmap={None: _PR_NS})
            self.write(
                rels_name,
                DECLARATION + etree.tostring(root, encoding="UTF-8", xml_declaration=False),
            )

        with self.edit_xml(rels_name) as root:
            identifier = self.next_relationship_id(part_name)
            element = new("pr:Relationship", Id=identifier, Type=resolved, Target=target)
            if mode:
                element.set("TargetMode", mode)
            ordered_insert(root, element)
            return identifier

    def find_relationship(
        self,
        part_name: str,
        reltype: str,
        *,
        target: str | None = None,
    ) -> etree._Element | None:
        """First relationship of *reltype* (and *target*, if given)."""
        resolved = RELTYPE.get(reltype, reltype)
        for relationship in self.relationships(part_name):
            if relationship.get("Type") != resolved:
                continue
            if target is not None and relationship.get("Target") != target:
                continue
            return relationship
        return None

    # -- saving ------------------------------------------------------------

    def save(
        self,
        destination: str | os.PathLike[str],
        *,
        allow_in_place: bool = False,
        guard: bool = True,
    ) -> Path:
        """Write the package to *destination*, streaming unchanged parts through.

        Writing back over the input requires ``allow_in_place=True``. Even then it
        goes through a temporary file, so an interrupted save cannot leave a
        half-written document behind.
        """
        destination = Path(destination)
        in_place = destination.exists() and destination.resolve() == self.path.resolve()
        if in_place and not allow_in_place:
            raise ValueError(
                f"refusing to save over the input {self.path}.\n"
                "Stages write a new file; pass allow_in_place=True if you really mean it."
            )

        if guard:
            _save.guard_writable(destination)

        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            dir=str(destination.parent), prefix=f".{destination.name}.", suffix=".tmp"
        )
        os.close(handle)

        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as out:
                for name in self._order:
                    if name in self._deleted:
                        continue
                    info = self._infos[name]
                    replacement = self._replaced.get(name)
                    if replacement is None:
                        self._copy_through(out, name, info)
                    else:
                        out.writestr(self._fresh_info(info), replacement)
                for name, data in self._added.items():
                    out.writestr(self._zip_info_for(name), data)
            if in_place:
                # Windows refuses to replace a file that is still open, and the
                # source zip is open for the whole life of this object.
                self._zip.close()
            Path(temporary).replace(destination)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

        if in_place:
            self.reopen()
        return destination

    def _copy_through(self, out: zipfile.ZipFile, name: str, info: zipfile.ZipInfo) -> None:
        with self._zip.open(name) as source, out.open(self._fresh_info(info), "w") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)

    @staticmethod
    def _fresh_info(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
        """A writable copy of *info*, preserving timestamp and compression."""
        fresh = zipfile.ZipInfo(info.filename, date_time=info.date_time)
        fresh.compress_type = info.compress_type
        fresh.external_attr = info.external_attr
        fresh.internal_attr = info.internal_attr
        fresh.create_system = info.create_system
        return fresh

    @staticmethod
    def _zip_info_for(name: str) -> zipfile.ZipInfo:
        """A deterministic ZipInfo for a part we created."""
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        return info
