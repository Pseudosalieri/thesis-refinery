"""Project configuration: one ``thesis.yml`` per thesis.

Three rules, all of them reactions to real failures in the pipeline this project
was extracted from.

**Unknown keys are errors.** A misspelled key that is silently ignored is a
setting the author believes is in force and is not. The build then "succeeds"
while ignoring an instruction, which is the worst possible outcome.

**Expected counts default to ``auto``.** The predecessor hard-coded seven counts
(captions, body paragraphs, reference entries, and so on) as literals in a config
file and in code defaults. Every one of them was a tripwire that had to be
re-derived by hand after any content change, and every one of them silently
rotted between changes. ``auto`` derives the expectation from the input document;
pin a number only when you actually need to pin it.

**Missing configuration fails loudly.** A checker that falls back to a default
identity will happily verify a completely different thesis and report success.
Both the fallback and the silence are refused here.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from thesis_refinery.style import (
    CaptionSpec,
    HeaderFooterSpec,
    MarginSpec,
    PageSpec,
    ParagraphSpec,
    StyleSpec,
    TableSpec,
)

__all__ = [
    "AUTO",
    "CONFIG_FILENAME",
    "Checks",
    "Config",
    "ConfigError",
    "Identity",
    "ProjectPaths",
    "find_project_root",
    "load",
]

CONFIG_FILENAME = "thesis.yml"

AUTO = "auto"
"""Sentinel for "derive this expectation from the input document"."""


class ConfigError(RuntimeError):
    """Raised when ``thesis.yml`` is missing, malformed, or inconsistent."""


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectPaths:
    """Where the sources and the build products live, all absolute."""

    root: Path
    thesis_dir: Path
    chapters_dir: Path
    output_dir: Path

    @property
    def quarto_config(self) -> Path:
        return self.thesis_dir / "_quarto.yml"

    @property
    def index_source(self) -> Path:
        return self.thesis_dir / "index.qmd"


@dataclass(frozen=True)
class Identity:
    """Strings that must, and must not, appear in the front matter.

    Empty by default and deliberately so: an empty required-list means the front
    matter is not checked for identity at all, which is honest, whereas a
    built-in default would silently check for someone else's thesis.
    """

    required: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()


@dataclass(frozen=True)
class Checks:
    """Expected counts. ``AUTO`` derives them from the document under test."""

    expect_captions: int | str = AUTO
    expect_body_paragraphs: int | str = AUTO
    expect_reference_entries: int | str = AUTO
    expect_sections: int | str = AUTO
    expect_drawings: int | str = AUTO


@dataclass(frozen=True)
class Config:
    """Everything a build needs to know about a project."""

    root: Path
    paths: ProjectPaths
    identity: Identity = field(default_factory=Identity)
    checks: Checks = field(default_factory=Checks)
    style: StyleSpec = field(default_factory=StyleSpec)
    profiles: tuple[str, ...] = ()
    reference_docx: Path | None = None
    frontmatter_docx: Path | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Discovery and loading
# ---------------------------------------------------------------------------


def find_project_root(start: Path | str | None = None) -> Path:
    """Nearest ancestor of *start* containing a ``thesis.yml``.

    Raises :class:`ConfigError` when there is none, rather than guessing at the
    current directory: operating on the wrong project is worse than stopping.
    """
    current = Path(start).resolve() if start is not None else Path.cwd().resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (candidate / CONFIG_FILENAME).is_file():
            return candidate

    raise ConfigError(
        f"no {CONFIG_FILENAME} found in {current} or any parent directory.\n"
        f"Run `thesis-refinery init` to create a project, or pass --project-dir."
    )


def load(project: Path | str | None = None) -> Config:
    """Load the configuration for *project*.

    *project* may be a directory, a path to a ``thesis.yml``, or ``None`` to
    search upwards from the current directory.
    """
    path = _resolve_config_path(project)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML:\n{exc}") from exc

    if document is None:
        document = {}
    if not isinstance(document, Mapping):
        raise ConfigError(f"{path} must contain a mapping at the top level, not {type(document).__name__}")

    return _build(Path(path).parent, document)


def _resolve_config_path(project: Path | str | None) -> Path:
    if project is None:
        return find_project_root() / CONFIG_FILENAME

    candidate = Path(project)
    if candidate.is_dir():
        config = candidate / CONFIG_FILENAME
        if not config.is_file():
            raise ConfigError(f"{candidate} does not contain a {CONFIG_FILENAME}")
        return config
    if candidate.is_file():
        return candidate
    raise ConfigError(f"{candidate} is neither a directory nor a file")


# ---------------------------------------------------------------------------
# Building the config from the parsed YAML
# ---------------------------------------------------------------------------

_KNOWN_TOP_LEVEL = frozenset(
    {
        "project",
        "identity",
        "checks",
        "style",
        "profiles",
        "reference_docx",
        "frontmatter_docx",
    }
)

_STYLE_SECTIONS: dict[str, type] = {
    "page": PageSpec,
    "margins": MarginSpec,
    "body": ParagraphSpec,
    "table": TableSpec,
    "caption": CaptionSpec,
    "header_footer": HeaderFooterSpec,
}


def _build(root: Path, document: Mapping[str, Any]) -> Config:
    unknown = set(document) - _KNOWN_TOP_LEVEL
    if unknown:
        raise ConfigError(
            f"unknown key(s) in {CONFIG_FILENAME}: {', '.join(sorted(unknown))}.\n"
            f"Known keys: {', '.join(sorted(_KNOWN_TOP_LEVEL))}.\n"
            "Unknown keys are refused rather than ignored, because a silently "
            "ignored setting is a setting the author believes is in force."
        )

    project = _mapping(document.get("project", {}), "project")
    paths = _build_paths(root, project)

    return Config(
        root=root,
        paths=paths,
        identity=_build_identity(document.get("identity", {})),
        checks=_build_checks(document.get("checks", {})),
        style=_build_style(document.get("style", {})),
        profiles=_string_tuple(document.get("profiles", []), "profiles"),
        reference_docx=_optional_path(root, document.get("reference_docx")),
        frontmatter_docx=_optional_path(root, document.get("frontmatter_docx")),
        raw=document,
    )


def _build_paths(root: Path, project: Mapping[str, Any]) -> ProjectPaths:
    known = {"thesis_dir", "chapters_dir", "output_dir"}
    _reject_unknown(project, known, "project")

    thesis_dir = root / str(project.get("thesis_dir", "manuscript"))
    return ProjectPaths(
        root=root,
        thesis_dir=thesis_dir,
        chapters_dir=root / str(project.get("chapters_dir", "manuscript/chapters")),
        output_dir=root / str(project.get("output_dir", "manuscript/_outputs")),
    )


def _build_identity(section: Any) -> Identity:
    mapping = _mapping(section, "identity")
    _reject_unknown(mapping, {"required", "forbidden"}, "identity")
    return Identity(
        required=_string_tuple(mapping.get("required", []), "identity.required"),
        forbidden=_string_tuple(mapping.get("forbidden", []), "identity.forbidden"),
    )


def _build_checks(section: Any) -> Checks:
    mapping = _mapping(section, "checks")
    _reject_unknown(mapping, {f.name for f in dataclasses.fields(Checks)}, "checks")

    values: dict[str, int | str] = {}
    for field_info in dataclasses.fields(Checks):
        if field_info.name not in mapping:
            continue
        values[field_info.name] = _count(mapping[field_info.name], f"checks.{field_info.name}")
    return Checks(**values)


def _build_style(section: Any) -> StyleSpec:
    mapping = _mapping(section, "style")

    sections: dict[str, Any] = {}
    top_level: dict[str, Any] = {}
    for key, value in mapping.items():
        if key in _STYLE_SECTIONS:
            sections[key] = _dataclass_from_mapping(_STYLE_SECTIONS[key], value, f"style.{key}")
        else:
            top_level[key] = value

    try:
        return StyleSpec(**sections, **top_level)
    except TypeError as exc:
        known = {f.name for f in dataclasses.fields(StyleSpec)}
        offending = sorted(set(top_level) - known)
        if offending:
            raise ConfigError(
                f"unknown key(s) under style: {', '.join(offending)}.\n"
                f"Known: {', '.join(sorted(known | set(_STYLE_SECTIONS)))}"
            ) from exc
        raise ConfigError(f"invalid style section: {exc}") from exc


def _dataclass_from_mapping(cls: type, value: Any, path: str) -> Any:
    mapping = _mapping(value, path)
    known = {f.name for f in dataclasses.fields(cls)}
    _reject_unknown(mapping, known, path)
    coerced = {name: _coerce(cls, name, raw) for name, raw in mapping.items()}
    return cls(**coerced)


def _coerce(cls: type, name: str, value: Any) -> Any:
    """Coerce a YAML scalar to the declared field type where it is unambiguous."""
    for field_info in dataclasses.fields(cls):
        if field_info.name != name:
            continue
        annotation = field_info.type
        if annotation in ("tuple[str, ...]", "Tuple[str, ...]"):
            return _string_tuple(value, name)
        if annotation in ("int | str",):
            return _count(value, name)
        if annotation in ("float", "int") and isinstance(value, (int, float)):
            return float(value) if annotation == "float" else int(value)
        return value
    return value


# ---------------------------------------------------------------------------
# Small validators
# ---------------------------------------------------------------------------


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path} must be a mapping, not {type(value).__name__}")
    return value


def _reject_unknown(mapping: Mapping[str, Any], known: set[str], path: str) -> None:
    unknown = set(mapping) - known
    if unknown:
        raise ConfigError(
            f"unknown key(s) under {path}: {', '.join(sorted(unknown))}.\n"
            f"Known: {', '.join(sorted(known))}"
        )


def _string_tuple(value: Any, path: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raise ConfigError(f"{path} must be a list of strings, not a bare string")
    if not isinstance(value, Sequence):
        raise ConfigError(f"{path} must be a list of strings, not {type(value).__name__}")
    return tuple(str(item) for item in value)


def _count(value: Any, path: str) -> int | str:
    if isinstance(value, str):
        if value.strip().lower() == AUTO:
            return AUTO
        if value.strip().isdigit():
            return int(value)
        raise ConfigError(f"{path} must be an integer or '{AUTO}', got {value!r}")
    if isinstance(value, bool):
        raise ConfigError(f"{path} must be an integer or '{AUTO}', got a boolean")
    if isinstance(value, int):
        return value
    raise ConfigError(f"{path} must be an integer or '{AUTO}', got {type(value).__name__}")


def _optional_path(root: Path, value: Any) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    candidate = Path(text)
    return candidate if candidate.is_absolute() else root / candidate
