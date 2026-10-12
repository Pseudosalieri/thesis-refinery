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
  `audit`, `ledger`, `diff`, `extract-prefix`. Subcommands that are declared but
  not yet built report themselves as such and exit with code 3.
- Stable exit-code contract: `0` ok, `1` gate failed, `2` bad usage,
  `3` not implemented, `4` environment.
- `tools/check_desensitized.py`: a gate that fails when anything identifying the
  source thesis, its author, its institution, or the originating machine appears
  in a file that git would publish. Wired into CI.
- `tools/dev-env.ps1` / `tools/dev-env.sh`: development shells that work on
  checkouts living under non-ASCII paths. See `docs/troubleshooting.md`.

### Known gaps

- `docxlib`, `style`, `config`, and every pipeline stage are still to come; the
  subcommands above are declared but unimplemented.
- The bundled example thesis does not exist yet.

[Unreleased]: https://github.com/OWNER/thesis-refinery/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/OWNER/thesis-refinery/releases/tag/v0.1.0
