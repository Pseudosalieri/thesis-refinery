# Design notes

Why this pipeline is shaped the way it is. Each section names the failure it
prevents, because the shape is not self-evident and a future contributor will
otherwise simplify it back into the bug.

## The problem, stated precisely

A thesis is written in Word and has to satisfy a format specification Word cannot
enforce. The usual workflow produces one artifact — `final_v7_REALLY_final.docx` —
which is simultaneously the source of truth, the build product and the archive.
Three consequences follow, and everything below is a reaction to one of them:

1. **A regression is invisible until a human notices.** There is no build to
   fail, so the first detector is a committee member.
2. **"Is this the version I sent?" has no answer.** Two files, one truth, no
   diff.
3. **Every fix is unverifiable.** Formatting is applied by hand, so nothing can
   be re-applied, and nothing can be checked independently of the person who
   applied it.

## Markdown is the source of truth; the `.docx` is a build product

Corollary one: content changes go into `chapters/*.qmd`, never into the `.docx`.

Corollary two, which is less obvious and more important: **formatting changes go
into a specification, never into either.** A pipeline that edits the document to
fix a margin has produced a document that cannot be re-derived, and the next
content edit loses the fix.

The human is still allowed to edit the `.docx` by hand. That is a legitimate
channel — people write in Word. But it creates an explicit obligation: diff the
Word version against the repository baseline, fold the changes back into
markdown, and re-run the coverage check. An unfolded Word edit is a *silent fork*,
and the toolchain treats it as one rather than pretending it cannot happen.

## One specification, two independent readers

Every formatting fact lives in `thesis_refinery/style.py`, exactly once. The code
that applies a fact and the code that checks it both read it from there.

What they must never share is the **traversal**. The applier walks the document
its own way; the checker walks it again, independently. If they shared a walk, a
bug in the walk would cancel itself out: the applier would misplace an element,
the checker would look in the mis-placed spot, find it, and report success. The
gate would then pass on a document Word cannot open.

So: share the data, never share the `main()`. This is written down because
"there's some duplication between the applier and the checker" is exactly the kind
of observation that gets acted on.

This project learned it the expensive way. Its predecessor implemented 26
formatting facts in one place and then re-typed 21 of them as literals across
roughly 60 sites in its audit scripts. The two copies drifted. The audit began
faithfully checking for a document nobody had asked for, and the symptom looked
like a content bug for two months.

## Copy-on-write at the part level

A `.docx` is a zip of parts. Almost every tool that edits one rebuilds the whole
archive, which means a stage that changes a single margin also rewrites every
image, every header and every content type.

That is not merely slow. It destroys the ability to answer *"did this stage
change anything it should not have?"*, because the answer is always "everything".
`docxlib.pkgs.Package` therefore streams parts it did not touch straight through:
decompressed and recompressed, never parsed, never re-serialised. A stage that
intends to change one part can be **shown** to have changed one part, and two
tests assert exactly that.

One consequence is worth stating on its own: `python-docx`'s `Document.save()`
must never be used here. It re-serialises every part, which is precisely the
property above. python-docx is used for its element classes and its read-only
conveniences; writes go through `Package`.

## Copy before write

A stage reads one file and writes a new one. Three things follow:

- A failed stage cannot have half-written its input, so the input is still there
  to diagnose.
- "Which stage broke this?" always has exactly one candidate answer.
- A file that git tracks cannot be quietly rewritten by a build.
  `docxlib.save.guard_writable` refuses.

The guard is deliberately permissive outside a git work tree. A user's thesis
folder is very often not a repository at all, and refusing to write there would
make the tool useless for the people it is for.

## Order is part of the schema

OOXML is sequence-typed. `w:pPr`'s children must appear in a fixed order, and Word
rejects a document where they do not — usually with the unhelpful message that
the file is corrupt. Hand-written `parent.append(child)` is therefore a latent
bug, not a style preference.

`docxlib.xmlq.SCHEMA_ORDER` records the child order of the elements this project
touches, and `ordered_insert` places a child accordingly. Two details matter:

- **Grouped positions.** Inside `w:r`, `w:t`, `w:tab` and `w:drawing` may appear
  any number of times in any order. Giving them distinct indices would make
  inserting a new `w:t` place it *before* an existing `w:tab`, silently
  reordering the inside of a run. They share one position instead.
- **Refusal over guessing.** When the parent or the child is not in the table,
  `ordered_insert` raises. It does not append and hope. `on_unknown="append"`
  exists for the cases where appending really is what is meant, and having to
  write it down is the point.

## Effective formatting, not literal formatting

A paragraph's formatting comes from its own `w:pPr`, from its style, or from a
style it inherits through `w:basedOn`. pandoc puts almost nothing in the
paragraph and relies on styles; a human editing in Word overrides one paragraph
by hand.

A checker that reads only direct formatting reports every paragraph as
unformatted. A checker that reads only the style cannot see the override. Both
produce confident, wrong answers. `verify.styles` resolves the chain, and every
finding names where the value came from — because "wrong indent, inherited from
Body Text" is actionable and "wrong indent" is not.

Deliberately not implemented: the full cascade with `w:docDefaults` merging and
toggle-property XOR semantics. That machinery has enough subtleties that getting
it subtly wrong is worse than the honest approximation. Direct-then-style-then-
parent-style covers every document Word and pandoc actually produce.

## A check that cannot fail is decoration

Every check returns findings; only one module, `verify/gate.py`, turns them into
an exit code. A report never fails. The predecessor had a single script that
printed a format report and always exited 0, and because it looked like a check,
nobody noticed for two months that nothing was gating anything.

Two behaviours exist because their absence is invisible:

- A body check that matches **zero** paragraphs fails. Style names get localised —
  Word in a Chinese locale calls it something other than `Body Text` — and "all
  zero of them are correct" is how a checker spends two months inspecting a
  document it is not looking at.
- The caption count must be **at least** the number of caption-looking
  paragraphs. Without that guard, a stage which rewrites caption styles passes a
  count assertion by lowering the count it is compared against.

And one that is a policy rather than a mechanism: **no default identity.** A
checker with a built-in fallback verifies whatever the fallback describes. If no
identity strings are configured, the check skips and says so.

## `auto` beats a pinned number

Expected counts default to `auto`, meaning "derive it from the document under
test". Hard-coded counts are tripwires that rot silently: the predecessor pinned
seven of them, in a config file and in code defaults, and every content change
made them quietly wrong.

Where a number must be pinned, it is pinned in one place and reported in the build
log — so the log says what the document *contains*, not just that it agreed with a
number somebody typed months ago.

## Configuration refuses what it does not understand

Unknown keys in `thesis.yml` are errors. A misspelled setting that is silently
ignored is a setting the author believes is in force, and the build then
"succeeds" while ignoring an instruction — the worst available outcome.

## Fields are written with a cached result

A field without a cached result shows nothing until Word refreshes it, which means
the document as committed does not show what the author will see, and a diff of
two versions shows a field as empty. Every generated field therefore carries a
result, and can carry its own run properties so a running head has its font size
before Word has ever run.

Instruction text keeps its surrounding spaces and `xml:space="preserve"`, because
Word delimits field arguments by spaces and a stripped instruction is one Word may
refuse to parse — and it will then display the instruction instead of the result.

## Deliberate omissions

- **No Git LFS.** The repository tracks small hand-authored inputs only. A
  contributor should not need a special remote to clone and run the demo.
- **No Word automation.** Everything is XML. Requiring Word would make the
  pipeline unrunnable in CI, which is where it earns its keep.
- **No PDF in CI.** docx rendering needs no fonts installed because Word
  documents reference fonts by name; PDF would need them, and PDF is not what
  the gates check.
- **No third-party assets.** University format specifications, published figures
  and instrument output are copyrighted and/or personal. See
  [`third-party.md`](third-party.md).
