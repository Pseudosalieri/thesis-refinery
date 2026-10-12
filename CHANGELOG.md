# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Nothing yet.

## [0.1.0] — 2026-10-12

First cut of the engine, extracted from a production thesis pipeline that carried
a master's thesis through roughly twenty revision rounds.

### Added

- Repository scaffold, AGPL-3.0-only licensing, and the SPDX header convention.
- `thesis-refinery` command-line surface: `init`, `render`, `build`, `verify`,
  `audit`, `ledger`, `diff`, `extract-prefix`. Except for `extract-prefix`, all
  are implemented; a subcommand that is declared but not built reports itself as
  such and exits with code 3.
- Stable exit-code contract: `0` ok, `1` gate failed, `2` bad usage,
  `3` not implemented, `4` environment.

**`docxlib`** — OOXML primitives, none of which know what a thesis is:

- `pkgs`: a `.docx` opened as copy-on-write parts. Parts that are not touched are
  streamed through the archive unmodified, so a stage that changes one part can
  be *shown* to have changed one part. Archive member names are validated, and
  part count and size limits guard against decompression bombs.
- `xmlq`: query and build helpers, plus `SCHEMA_ORDER` — the child order of
  `pPr`, `rPr`, `sectPr`, `tblPr` and friends. OOXML is sequence-typed, so
  appending a child in the wrong place produces a document Word calls corrupt.
  Grouped positions keep a new `w:t` from being inserted ahead of an existing
  `w:tab` inside the same run.
- `parse`: one parser for the whole project, reusing python-docx's
  entity-disabled configuration so the security posture cannot drift per module.
- `save`: copy-before-write, atomic replace, and a guard that refuses to write
  over a git-tracked file.
- `synth`, `drawings`, `tables`, `fields`, `units`.

**`style.py`** — the single specification. Applying code and checking code both
read it; their traversals stay separate, because a shared walk would let a bug
cancel itself out and report a broken document as fine.

**`config.py`** — strict `thesis.yml`. Unknown keys are errors, expected counts
default to `auto`, and there is no default identity.

**`verify/`** — seven checks: geometry, numbering, headers and footers, body
metrics, captions, tables, front matter. Effective formatting is resolved
direct-then-style-then-inherited. Eleven negative tests, one per check, each
corrupting a conforming fixture in exactly the way that check exists to catch.

**`ledger/`** — a digest baseline that fails on any difference in either
direction, a reviewable inventory, and a paragraph-level diff for folding a
Word edit back into markdown.

**`stages/format.py`** — applies the specification to a rendered document: page
setup, page numbering, body metrics, three-line tables, running heads, footers,
and field updating.

**`examples/demo-thesis`** — an entirely synthetic thesis that builds and passes
every check. Its style template and placeholder figure are generated, not copied.

**`docs/`** — design notes, third-party policy, a publish checklist, a
desensitisation report, and a troubleshooting note.

**`tools/check_desensitized.py`** — fails when anything identifying the source
thesis, its author, its institution, or the originating machine reaches a file
git would publish. Wired into CI, with a negative test proving it can fail. It
caught a real leak in this project's own documentation twice.

### Known gaps

- `stages/frontmatter.py`, `stages/references.py` and `stages/abbreviations.py`
  are planned and not present. The pipeline reports an absent stage by name and
  stops, so a checkout without them fails informatively rather than silently
  doing less than its author believes.
- `extract-prefix` is declared and not built.
- `profiles/chemistry` ships two of its three planned modules: the caption run
  shape and the software-term subscripts. The spectra appendix is named in the
  package docstring and deliberately kept out of `__all__`, because a name in
  `__all__` is a promise and a promise that raises `ModuleNotFoundError` is worse
  than an acknowledged absence.
- The bundled example is a single section, so per-section page numbering is
  exercised by the checks and their tests rather than by the example.

[Unreleased]: https://github.com/OWNER/thesis-refinery/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/OWNER/thesis-refinery/releases/tag/v0.1.0
