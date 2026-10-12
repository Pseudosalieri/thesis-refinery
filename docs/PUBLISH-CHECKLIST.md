# Publish checklist

Everything a maintainer must do **before** this repository is pushed anywhere.
The repository is publish-*ready*; it is not published. Nothing here has been
done for you, and several items cannot be automated.

Work through it in order. The first three sections are the ones that are actual
decisions; the rest is housekeeping.

---

## 1. Identify yourself, or decide not to

| Item | Where | Why it cannot be automated |
|---|---|---|
| `https://github.com/OWNER/thesis-refinery` | `pyproject.toml` (`[project.urls]`), `CITATION.cff`, `CHANGELOG.md` | The account is your choice. |
| Commit author | `git config user.name` / `user.email` in this repository | Every commit currently uses a placeholder identity. Decide whether that is what you want in a public history, and if not, rewrite it **before** the first push. |
| `CITATION.cff` authors | `CITATION.cff` | A citation needs a real name or a deliberate collective. |
| Code of Conduct contact | `CODE_OF_CONDUCT.md` (`CONDUCT_CONTACT_PLACEHOLDER`) | An enforcement address that nobody reads is worse than none. |

```powershell
# What the history currently says about you:
git log --format='%an <%ae>' | Sort-Object -Unique

# Rewriting the author of every commit, before publishing:
git filter-repo --mail-map mailmap.txt   # or: git rebase --root --exec 'git commit --amend --no-edit --reset-author'
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
thesis-refinery verify --docx examples/demo-thesis/manuscript/_outputs/index-final.docx
```

Both must succeed. If they do not, the README's quickstart is a lie, and the
first thing every visitor does is the quickstart.

## 4. Repository settings, once it exists

- [ ] Description: *Turn an already-written Word thesis into a reproducible build
      artifact, guarded by assertions instead of eyeballs.*
- [ ] Topics: `thesis`, `dissertation`, `docx`, `python-docx`, `quarto`,
      `reproducible-build`, `formatting`, `verification`
- [ ] Licence detected as **AGPL-3.0** (GitHub reads `LICENSE`; the PEP 639
      `license` field in `pyproject.toml` agrees with it)
- [ ] Issues enabled; Discussions optional
- [ ] Branch protection on `main`: require the CI checks to pass
- [ ] **Actions enabled** — `.github/workflows/ci.yml` has never run. Its first
      run is a real event: the `end-to-end` job installs Quarto and builds the
      example, and that path has only been exercised locally.
- [ ] Secret scanning and Dependabot alerts on
- [ ] Confirm the desensitisation gate is the **first** job in CI, as written. A
      leak must not be maskable by an unrelated test failure.

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
