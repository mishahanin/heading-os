# Design docs and ADRs

How significant decisions in HEADING OS are recorded before the code lands, and an index of the records so far.

Last Updated: 2026-07-08

## What a design doc is here

A design doc (an architecture decision record, ADR) is a short markdown file that states the context behind a change, the decision taken, and the alternatives rejected. It is written before the implementation, so the direction can be reviewed on its own rather than reverse-engineered from a diff. This is the design-doc-first discipline the Phase 10 work follows: decide in prose, review the prose, then build.

## When to write one

Write a design doc for a structural or cross-cutting change: a new subsystem, a new seam between engine and data, a change to how a whole class of things works, or anything a future reader would struggle to understand from the code alone. A small, local fix does not need one. If in doubt, a short record costs little and pays back the first time someone asks "why was it done this way".

## Naming

Two conventions, chosen by origin:

- **Playbook-tracked items** keep their playbook id: `F-XX-slug.md` (for example `F-10.4-memory-lifecycle.md`). This preserves traceability to the remediation playbook that requested the work.
- **Standalone decisions** use a sequential id: `ADR-NNNN-slug.md`, numbered in order of acceptance.

Each record carries an H1 title on line 1, a one-line description, a `Last Updated` line, and a `Status` of `proposed`, `accepted`, or `superseded`.

## How to start one

Copy [`adr-template.md`](adr-template.md) to the right filename, fill in the sections, and open it in the same change (or the issue) that proposes the work.

## Markdown-only, by design

These records are for contributors reading the repository, not visitors of the docs site. They are deliberately NOT generated into HTML and NOT added to the site navigation (`SITE_NAV_GROUPS` in `scripts/regenerate-docs-html.py`). Because `docs/design/*.md` have no `.html` sibling, the F-8.1 docs-drift guard (`regenerate-docs-html.py --all` then `git diff docs/`) never regenerates them, so a new record here does not touch the docs build. Keep it that way: do not add an `.html` sibling for a design doc.

## Index

| Record | Status | Summary |
| --- | --- | --- |
| [`F-10.4-memory-lifecycle.md`](F-10.4-memory-lifecycle.md) | accepted | One published map of the six memory stores plus the unified `scripts/memory.py` console facade. |
| [`adr-template.md`](adr-template.md) | template | The fill-in skeleton for a new record. |
