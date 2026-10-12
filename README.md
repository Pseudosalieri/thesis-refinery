# thesis-refinery

**Turn an already-written Word thesis into a reproducible build artifact — and let
assertions, not eyeballs, prove it didn't drift.**

[简体中文](README.zh-CN.md) · [CHANGELOG](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · License: AGPL-3.0-only

---

> **Status: 0.1.0, pre-release.** The engine is being extracted from a thesis
> pipeline that produced a real, defended master's thesis across ~20 revision
> rounds. Interfaces may still move. See [CHANGELOG.md](CHANGELOG.md) for what
> is landed and what is still pending.

### What works today

```bash
thesis-refinery init my-thesis          # scaffold an empty project
thesis-refinery build examples/demo-thesis   # render, format, and gate the bundled example
thesis-refinery verify -C examples/demo-thesis
```

The bundled example builds and passes all seventeen checks, two of which skip
because the example configures no cover identity. The formatting stage, the
gates, the ledger, `init`, `diff` and the build orchestrator are all implemented
and tested; `extract-prefix` is declared and not yet built.

| Component | State |
|---|---|
| `docxlib` — OOXML primitives, part-level copy-on-write | done |
| `style.py` / `config.py` — one specification, strict configuration | done |
| `verify/` — seven checks, each with a negative test | done |
| `ledger/` — digest baseline, inventory, paragraph diff | done |
| `stages/format.py` — applies the specification | done |
| `examples/demo-thesis` — synthetic, buildable, gate-passing | done |
| `stages/abbreviations.py` — the abbreviation table, rebuilt from its markdown | done |
| `extract-prefix` — cut a cover out of an existing document | done |
| `profiles/chemistry` — caption run shape, software-term subscripts | optional extra |
| `profiles/chemistry/spectra.py` — a spectra appendix | planned, not present |
| `stages/frontmatter.py`, `stages/references.py` | planned, not present |

Nothing in that list is a surprise to the tool itself: a stage that is absent is
reported by name and the build stops there, and a profile module that is absent is
named in prose rather than promised in `__all__`.

## The problem

Almost every thesis is edited in Word, and almost every thesis has to satisfy a
formal format specification that Word cannot enforce. The result is the familiar
failure mode:

- the file `final_v7_REALLY_final.docx` is the only source of truth;
- a formatting regression is discovered by a committee member, not by a build;
- nobody can tell whether the version on the chair's desk matches the version in
  the repository;
- every "fix" is a manual round of clicking, unverifiable and unrepeatable.

Templates do not solve this. They help you *start* a thesis. They do nothing for
the far more painful second half: **you already have a draft, and now it has to
be made conformant without quietly breaking anything else.**

## What this is — and what it is not

| | |
|---|---|
| **Is** | A build + verification toolchain for a thesis that already exists. |
| | A single declarative style specification, applied by scripts and re-checked by independent assertions. |
| | A gate that exits non-zero when the document drifts. |
| **Is not** | A LaTeX or Quarto thesis *template*. |
| | A Word round-trip converter that "just works" on any document. |
| | A citation manager. |

The closest neighbours are Quarto thesis templates
([`quarto-thesis`](https://github.com/nmfs-opensci/quarto-thesis),
[`quarto-monash/thesis`](https://github.com/quarto-monash/thesis),
[`upfthesis`](https://github.com/gongcastro/upfthesis) and friends). Reach for
those when you are starting from an empty page. Reach for this when you are
staring at a finished draft and a 40-page format specification.

## The pipeline

Sources are markdown. The `.docx` is a **build product**, never the source of
truth.

```
chapters/*.qmd ──► quarto render ──► index.docx
                                        │
                                        ├─ front matter import   (cover/declaration, byte-preserved)
                                        ├─ optional profiles     (spectra appendices, energy-term subscripts)
                                        ├─ reference styling     (bibliography record normalisation)
                                        ├─ style application     (margins, sections, headers/footers, three-line tables)
                                        └─ typography finish     (rule lines, footer size, abstract headings)
                                        │
                                        ▼
                                   index-final.docx ──► verify ──► PASS / exit 1
```

Every stage reads one file and writes a **new** file. Nothing is ever modified
in place, so a failure always has exactly one candidate input.

## Quickstart

```bash
pip install -e ".[dev]"                  # or: . ./tools/dev-env.sh

thesis-refinery init my-thesis           # scaffold an empty project
cd my-thesis
thesis-refinery build                    # render, apply the style, run the gates
thesis-refinery audit                    # the same checks as a readable report
thesis-refinery verify                   # the same checks as an exit code
```

Quarto is required for the render step. Everything else — style application,
verification, ledger — works on a `.docx` you already have, so you can adopt
this tool on a finished document without re-rendering anything.

## Three commitments

### 1. Markdown is the source of truth

Content changes go into `chapters/*.qmd`. Format changes go into the style
specification. Hand-editing the `.docx` is supported as a **separate channel**
for a human author, but it creates an explicit obligation: diff the Word version
against the repository baseline and fold the changes back into markdown. An
unfolded Word edit is a silent fork, and this toolchain treats it as one.

### 2. One style specification, two independent readers

Formatting facts — page geometry, margins, header text, table borders, caption
styles, TOC tab stops — live in exactly one place. Both the code that *applies*
them and the code that *checks* them read that one specification.

What is deliberately **not** shared is the traversal code. The applier and the
checker walk the document separately. If they shared a code path, a bug in the
walk would cancel itself out and the gate would pass on a broken document. Share
the data; never share the `main()`.

### 3. Every assertion must be able to fail

A checker that cannot fail is decoration. Every check in `verify/` ships with a
negative test that deliberately corrupts the document and asserts that the gate
goes red. A format report that always exits 0 is not a gate, and this project
does not ship one.

## Configuration

A single `thesis.yml` per project describes the layout, the identity strings the
cover must contain, the style values, and the expected counts.

```yaml
project:
  thesis_dir: manuscript
  chapters_dir: manuscript/chapters
  output_dir: manuscript/_outputs

identity:
  required: []      # strings that must appear in the front matter
  forbidden: []     # strings that must not

style:
  page:   { width_mm: 210, height_mm: 297 }
  margins: { top_mm: 25, bottom_mm: 25, body_side_mm: 20 }

checks:
  expect_captions: auto        # derive from the source instead of hard-coding
  expect_body_paragraphs: auto
```

Expected counts accept `auto`, which derives the expectation from the input
document. Hard-coded counts are tripwires that silently rot; where a number has
to be pinned, it is pinned in one place and reported in the build log.

## Profiles

Domain-specific steps are opt-in:

```bash
pip install -e ".[chemistry]"
```

```yaml
profiles: [chemistry]
```

The `chemistry` profile adds numeric spectra appendices (compound × spectrum-type
matrix, caption templating, page rotation for sideways scans) and subscript
formatting for software-reported terms such as `ΔG_Bind`. It is driven entirely
by a manifest you describe — sheet names, column names, filename templates,
caption templates — with no compound naming scheme baked into the code.

## Repository layout

| Path | Contents |
|---|---|
| `src/thesis_refinery/docxlib/` | Shared OOXML primitives: namespaces, XML queries, package surgery, drawings, tables, fields, safe save |
| `src/thesis_refinery/style.py` | The single style specification |
| `src/thesis_refinery/stages/` | Pipeline stages, one file each |
| `src/thesis_refinery/verify/` | Gates (`gate.py`), human report (`report.py`), individual checks |
| `src/thesis_refinery/ledger/` | Inventory, hash manifest, figure map, version diff |
| `src/thesis_refinery/profiles/` | Optional domain profiles |
| `examples/demo-thesis/` | A small, entirely synthetic thesis that exercises every stage |
| `tools/` | Generators for demo assets, and the desensitisation gate |
| `docs/` | Design notes, rules, third-party policy, publish checklist |

## Requirements

- Python ≥ 3.10
- Quarto (render step only); pandoc comes with it
- No Git LFS, no Word automation, no COM. Windows is supported but not required.

## Documentation

- [`docs/design-notes.md`](docs/design-notes.md) — why the pipeline is shaped this way
- [`docs/rules/`](docs/rules/) — the working rules (markdown truth, copy-before-write, notation)
- [`docs/third-party.md`](docs/third-party.md) — what is deliberately *not* bundled, and why
- [`docs/PUBLISH-CHECKLIST.md`](docs/PUBLISH-CHECKLIST.md) — what a maintainer must do before publishing

## License

AGPL-3.0-only. See [LICENSE](LICENSE).

Note the network clause: if you run a modified version of this program as a
network service, you must offer the corresponding source to its users. Using it
locally to build your own thesis triggers nothing.

No third-party assets are bundled. The reference styles a real university
requires are copyrighted documents that you supply yourself — see
[`docs/third-party.md`](docs/third-party.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: any new check needs a
negative test, and any new formatting fact goes in `style.py` rather than inline.
