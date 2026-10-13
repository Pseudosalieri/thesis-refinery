# Troubleshooting

## `pip install -e .` succeeds but `import thesis_refinery` fails

**Symptom.** The editable install reports success, `pip show thesis-refinery`
lists the package, and yet:

```
ModuleNotFoundError: No module named 'thesis_refinery'
```

`sys.path` does not contain your `src/` directory, even though the `.pth` file in
`site-packages` does contain the right path — and the path visibly exists.

**Cause.** Python's `site` module reads `.pth` files with `encoding="locale"`,
i.e. `locale.getencoding()`. On a machine whose locale encoding is not UTF-8
(cp936, cp1251, latin-1, …), a `.pth` file that holds a **non-ASCII path** is
decoded into mojibake, `os.path.exists()` returns false, and `site` **skips the
entry silently**. No warning is printed at any verbosity.

Minimal reproduction:

```python
import io, locale, os
p = r"...\site-packages\_editable_impl_thesis_refinery.pth"
print(locale.getencoding())            # cp936
with io.TextIOWrapper(io.open_code(p), encoding="locale") as f:
    line = next(f)
print(repr(line))                      # the path is now mojibake
print(os.path.exists(line.rstrip()))   # False
```

The last two lines are the tell: the path is valid on disk, but what `site` read
back is not the path that was written.

**What does *not* fix it.** `PYTHONUTF8=1`. UTF-8 mode changes
`locale.getpreferredencoding()`, but `encoding="locale"` resolves through
`locale.getencoding()`, which still reports `cp936`. Verified on CPython 3.12.

**What does fix it.** Any of these:

1. **Use `PYTHONPATH` instead of an editable install.** This is what
   `tools/dev-env.ps1` and `tools/dev-env.sh` do. Environment variables are
   decoded with the filesystem encoding, which *is* UTF-8 on Windows, so
   non-ASCII paths are fine:

   ```powershell
   . .\tools\dev-env.ps1
   python -m thesis_refinery --help
   ```

2. **Install non-editable** (`pip install .`). The package is copied into
   `site-packages`, and `site` never has to resolve your checkout's path. You
   lose live reloading of your edits.

3. **Keep the checkout under a pure-ASCII path.** The whole path has to be
   ASCII, not just the last directory name: a checkout at
   `D:\<non-ASCII-root>\thesis-refinery` still fails, because it is the parent
   directory that gets mangled. If your projects live under a non-Latin-script
   root, option 1 is the practical choice.

The test suite is immune either way: `pyproject.toml` sets
`pythonpath = ["src"]` for pytest, so `python -m pytest` works with no install at
all.

## Quarto is not found

`thesis-refinery render` and `thesis-refinery build` shell out to `quarto`. The
executable is resolved in this order:

1. `--quarto` on the command line,
2. the `QUARTO_EXE` environment variable,
3. `tools.quarto` in `thesis.yml`,
4. `quarto` on `PATH`.

There is deliberately no hard-coded installation path. A pinned absolute path goes
stale the moment the tool it names is uninstalled or upgraded; the build then keeps
working only by accident of a `PATH` fallback, and nobody notices for months.

`thesis-refinery verify` and `thesis-refinery audit` do **not** need Quarto. They
operate on a `.docx` you already have, which is the point: you can adopt the
gates on a finished document without re-rendering anything.

## A gate fails and I disagree with it

First check whether the disagreement is about a *fact* or about a *tolerance*:

- If a formatting fact is wrong, fix it in `thesis.yml` / `style.py`. Both the
  applier and the checker read the same specification, so a genuine
  specification change cannot make them disagree.
- If the check is too strict about something the specification does not cover,
  that is a bug in the check. Open an issue with the document that triggers it.

Do not silence a gate by deleting the assertion. A check that cannot fail is
decoration, and this project's whole reason to exist is that decoration was not
enough.

## Console output is garbled (mojibake) on Windows

Set `PYTHONIOENCODING=utf-8` (both `dev-env` scripts do this for you) or run
`chcp 65001` first. Reports contain typographic characters such as `Δ` and `×`,
which a cp936 console cannot encode.
