# Contributing

Thanks for considering a contribution. This project exists because manual thesis
formatting is unreproducible, so the contribution rules lean hard on
reproducibility.

## Getting set up

```bash
# POSIX
. ./tools/dev-env.sh

# Windows / PowerShell
. .\tools\dev-env.ps1

python -m pytest
python -m ruff check .
python tools/check_desensitized.py
```

`pip install -e ".[dev]"` also works, and is the normal route on a machine whose
checkout path is pure ASCII. If you develop under a non-ASCII path on Windows,
read [`docs/troubleshooting.md`](docs/troubleshooting.md) first — an editable
install will appear to succeed and then not be importable.

### Why `ruff format` is not run

`ruff check` is enforced. `ruff format` is not, and CI does not check it.

The formatter explodes any collection carrying a magic trailing comma onto one
element per line. The sequence tables in `docxlib/xmlq.py` are written that way
on purpose — `SCHEMA_ORDER` for `w:pPr` is eight scannable lines, and the
formatter makes it thirty-eight — and those tables are the most-read reference in
the codebase. Machine-enforced formatting is worth having right up to the point
where it makes the reference material worse, and this is that point.

So: keep lines within 100 columns, keep imports sorted, and let `ruff check`
decide the rest. Do not reformat the schema tables by hand to match a formatter
that is not being run.

## The two rules that are not negotiable

### 1. Every check needs a test that makes it fail

A verification check that has never been observed failing is not a check. If you
add or change an assertion in `verify/`, add a test under `tests/` that corrupts
the document in exactly the way the check is supposed to catch, and assert that
the gate reports a failure and returns a non-zero exit code.

Pull requests that add a check without a negative test will be asked to add one.

### 2. Every formatting fact lives in `style.py`

Page geometry, margins, header text, table borders, caption styles, font sizes,
TOC tab stops — all of it goes in the style specification, and both the code that
applies it and the code that checks it read it from there.

If you find yourself typing a millimetre value, a point size, or a style name
into a stage or a check, stop and put it in the specification instead. The
project's predecessor duplicated 21 of its 26 formatting facts across roughly 60
literal sites, and that is exactly the failure mode this project was created to
end.

## Sharing traversal code is a bug, not a refactor

The applier and the checker must walk the document **separately**. They share the
specification (data). They must not share the walk. If they did, a bug in the
walk would cancel itself out and the gate would pass on a broken document.

You will occasionally be tempted to "remove duplication" by having a check call
into a stage. Don't.

## No test may need the network, Word, or a licence

Tests must run on a clean CI machine. Quarto is allowed only in tests marked
`@pytest.mark.slow`, and those must skip cleanly when Quarto is absent.

## Commits

- One logical change per commit; a commit that adds a check also adds its test.
- Explain *why* in the message body when the change is not obvious from the diff.
- Run the three commands from "Getting set up" before pushing.

## A note on what must never be committed

This repository is a generalisation of a real, private thesis. Real theses,
university format specifications, instrument output, and publisher figures are
copyrighted and/or personal. They do not go in this repository — not in `tests/`,
not in `examples/`, not as a "small sample".

`tools/check_desensitized.py` enforces part of this mechanically, and CI runs it.
Do not weaken the deny-list to make your change pass. If a deny-list entry is
genuinely wrong, say so in the pull request and explain why.

The bundled example is entirely synthetic. Keep it that way: use obviously fake
DOIs, obviously fake author names, and generated placeholder images rather than
anything resembling a real document.
