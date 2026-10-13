# Publish checklist

Everything a maintainer must do **before** this repository is pushed anywhere.
The repository is publish-*ready*; it is not published. Nothing here has been
done for you, and several items cannot be automated.

Work through it in order. The first three sections are the ones that are actual
decisions; the rest is housekeeping.

---

## 1. Identify yourself, or decide not to

Settled for this publication. The decisions are recorded rather than implied, so a
future maintainer can see what was chosen:

| Item | Decision |
|---|---|
| Repository URLs | Point at the published repository in `pyproject.toml` (`[project.urls]`), `CITATION.cff`, and `CHANGELOG.md`. |
| Commit author / committer | The name is the neutral collective `thesis-refinery contributors`. The address is the repository owner's GitHub noreply address, so commits attribute to the account without publishing a personal name. |
| `CITATION.cff` authors | The same collective name: a deliberate collective, not a real name. |
| Code of Conduct contact | A real, monitored address. The placeholder is gone. |

The author was rewritten across the *whole* history before the first push, with
`git filter-branch --env-filter` over `--all`, which changes the identity fields and
leaves the original author and committer dates untouched. The equivalent recipes:

```powershell
# With git-filter-repo installed:
git filter-repo --mail-map mailmap.txt

# Without it, dates are reset, which is why filter-branch was used instead:
git rebase --root --exec 'git commit --amend --no-edit --reset-author'
```

## 2. Run the gates one last time

```powershell
cd <repo>
$env:PYTHONPATH = (Join-Path $PWD 'src')
$env:PYTHONIOENCODING = 'utf-8'

python tools/check_desensitized.py     # must print PASS, 0 hits
python -m pytest -q                    # must be green
python -m ruff check .                 # must be clean
```

All three were run and passed for this publication, and the bundled example was
built and verified as well (section 3).

There is deliberately no `ruff format --check`: the formatter would explode the
schema-order tables in `docxlib/xmlq.py` onto one element per line. See the note
in [`CONTRIBUTING.md`](../CONTRIBUTING.md).

The desensitisation gate is the one that matters most, and it is the one that is
easy to skip because it always passes *after* you have been careful. Run it
anyway: it caught a real leak in this project's own documentation twice while the
documentation was being written.

## 3. Confirm the example actually runs

```powershell
thesis-refinery build examples/demo-thesis
thesis-refinery verify -C examples/demo-thesis
```

Both must succeed. If they do not, the README's quickstart is a lie, and the
first thing every visitor does is the quickstart.

`verify` needs `--project-dir` (or `-C`): it looks for `thesis.yml` in the working
directory and its parents, and from the repository root there is none. Passing only
`--docx` is not enough, and that is what this section used to say. Walking this
checklist before the first push is what caught it; leaving it to CI would have made
the first run red for a reason that has nothing to do with the tool.

## 4. Repository settings, once it exists

- [x] Description: *Turn an already-written Word thesis into a reproducible build
      artifact, guarded by assertions instead of eyeballs.*
- [x] Topics: `thesis`, `dissertation`, `docx`, `python-docx`, `quarto`,
      `reproducible-build`, `formatting`, `verification`
- [x] Licence detected as **AGPL-3.0** (GitHub reads `LICENSE`; the PEP 639
      `license` field in `pyproject.toml` agrees with it)
- [x] Issues enabled; Discussions optional
- [ ] Branch protection on `main`: require the CI checks to pass
- [ ] **Actions enabled** — `.github/workflows/ci.yml` has never run. Its first
      run is a real event: the `end-to-end` job installs Quarto and builds the
      example, and that path has only been exercised locally.
- [x] Secret scanning and Dependabot alerts on
- [x] Confirm the desensitisation gate is the **first** job in CI, as written. A
      leak must not be maskable by an unrelated test failure.
- [ ] Branch protection is deliberately deferred until the first CI run is green;
      adding it before that blocks the maintainer's own pushes.

## 5. Workflow files that will run for the first time

`.github/workflows/ci.yml` is written but has never executed. Expect to fix:

- the Quarto action's version pin, and
- the end-to-end job's path assumptions, if the demo's layout moved.

Do not disable a failing job to make the first run green. The end-to-end job is
the only thing that proves the tool works on a machine that is not yours.

## 6. Decide about packaging

Publishing to PyPI is **not** required and is not prepared:

- [ ] The version is `0.1.0` and the `Development Status` classifier says Alpha.
      Do not publish a `1.0.0` for a tool whose interfaces still move.
- [ ] `CHANGELOG.md` has an `[Unreleased]` section; the release process is
      documented there and nowhere else. Consider adding `docs/releasing.md` once
      you have done it twice.
- [ ] If you do publish, `pip install -e .` must work on a machine with an ASCII
      checkout path — the non-ASCII failure in `docs/troubleshooting.md` is a
      `site`/locale interaction, not something this project can fix.

## 7. Afterwards

- [ ] Tag the release: `git tag -a v0.1.0 -m "..."` and push the tag.
- [ ] Add `docs/PUBLISH-CHECKLIST.md`'s completed state to `CHANGELOG.md`'s
      `[Unreleased]` section and empty the checklist down to the parts that apply
      to the *next* release.

## 8. What the gate cannot see, checked by hand before the first push

The gate reads the working tree as text, skips anything it cannot decode as UTF-8,
and never looks at git history. Both gaps were closed by hand for this publication.
The results are recorded because a green gate says nothing about either:

- **The three tracked binaries were opened, not assumed clean.** `placeholder.png`
  is a plain grey rectangle; `front-cover.docx` holds only placeholder cover lines;
  `reference.docx` is a bare style container with no `docProps/core.xml`, so it
  carries no author or company metadata. No figure, cover photograph, or instrument
  image is tracked.
- **The history holds no private material.** `git fsck --unreachable --dangling` is
  empty, and there are no stashes, extra refs, or old remotes — so nothing deleted is
  recoverable from what is published.
- **Commit metadata was audited as well as file contents.** The gate reads files,
  not messages. The messages carried a reference to a private repository; it was
  rewritten out of the commits that contained it, together with narrative that only
  made sense inside the originating pipeline. Per-commit test counts were removed
  too, since they are stale the moment the next commit lands.
- **`predecessor` in module docstrings is retained** where it is doing explanatory
  work. It names no person, institution, or repository, and the provenance
  statements in `README.md` and `CONTRIBUTING.md` are what define it.

---

## What must never be added, by anyone

Repeated here because it is the one thing that cannot be undone once it is in a
public git history:

- A real thesis, in whole or in part, including a "de-identified" one. Text is
  not the only identifier: a figure is, and so is a reference list.
- A university's format specification, sample thesis, or `reference.docx`.
- Instrument output — spectra, chromatograms, plate reads.
- A figure reproduced from a publication.
- A person's name, student number, supervisor, or institution, anywhere,
  including in a comment, a test fixture, a commit message, or this file.

If you are unsure whether something is identifying, run the gate. If the gate
does not catch it, add it to the deny-list in `tools/check_desensitized.py` — the
deny-list is the shared, reviewable record of what must not appear, and adding an
entry is a pull request like any other.
