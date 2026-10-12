# Security policy

## Reporting a vulnerability

Report privately through GitHub's **Security → Report a vulnerability** form on
this repository, rather than opening a public issue.

Please include the version, the platform, and a minimal document or command that
reproduces the problem. Expect an initial response within a week.

## What counts as a vulnerability here

This tool parses files you did not author. That is its job, and it is also its
attack surface.

**In scope**

- XML external entity (XXE) expansion, or any network/file access triggered by
  parsing a `.docx`.
- Billion-laughs / quadratic blowup, i.e. a document that makes the tool consume
  memory or CPU out of proportion to its size.
- Path traversal via a zip entry name inside a `.docx`, `.xlsx`, or an archive
  this tool unpacks.
- Zip-slip or symlink tricks during package rewriting, where a crafted archive
  causes a write outside the output directory.
- A crafted document causing the tool to overwrite a file it should refuse to
  touch. The pipeline is built on a copy-before-write rule; a bypass of that rule
  is a security bug, not a convenience bug.
- Command injection through a configured path, filename, or caption template.

**Out of scope**

- Malicious content inside a document that is merely displayed in the generated
  output, such as a macro or an embedded object pasted into the thesis. This tool
  does not execute documents; it rewrites them.
- Denial of service from a legitimately enormous document, such as a thesis with
  hundreds of megabytes of figure media. That is expected to be slow.
- Anything requiring the attacker to already have write access to the project
  directory.

## Hardening requirements

These are enforced centrally in `docxlib`, so that no individual stage can drift
away from them. They are the invariants this project is expected to hold; a stage
that bypasses one is a bug.

- All XML parsing goes through a single parser configuration with entity
  resolution and network access disabled. No stage constructs its own parser.
- Archive member names are validated before extraction; entries containing `..`
  or an absolute path are rejected rather than sanitised.
- Stages never modify an input in place. A stage reads one file and writes a new
  one, and refuses to write over a file that git tracks.
- No path taken from a document, a manifest, or a configuration value is used
  for a write without being resolved and checked against the output directory.
