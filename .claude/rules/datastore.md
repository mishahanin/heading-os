---
paths:
  - "outputs/content/**"
  - "outputs/deliverables/**"
  - "datastore/**"
---

<!-- version: 2.0.0 | last-updated: 2026-09-02 -->
# DataStore: source of truth

Last Verified: 2026-09-02

`datastore/` holds the authoritative original documents: contracts, decks,
datasheets, spreadsheets, research. When you state a fact about 31C, whether it
is a number, a price, a partner detail, a market figure or a product capability,
**check it against the DataStore before you write it down.**

## Everything in `datastore/` is private

Operator directive, 2026-09-02. Nothing under `datastore/` may reach a public
surface. That covers file CONTENTS and file NAMES alike, and it covers this
repository, which is public.

Three consequences, and none of them is optional:

- **Never quote a datastore filename in engine code, prose, a comment, a
  docstring, a test fixture or an example.** Invent one of the same shape.
- **Engine code that loads a brand asset resolves it through the private
  manifest**, so the filename lives in the data overlay and never in a public
  file.
- `corporate` routing is not an exception. It means shared down to executives,
  through private repositories. It never means publishable.

`tests/test_a_public_engine_that_named_a_private_competitor.py` enforces this
mechanically. It exists because the rule alone did not hold: on 2026-09-02 a
public test docstring was found quoting, verbatim, the title of a competitor's
commercial proposal that lives at a `private` path.

## Where the structure is written down

**Not here.** The inventory is generated into the private data overlay at
`reference/datastore-map.md` by `python scripts/datastore-map.py`. It carries
the real directory names, per-subtree file counts, the routing destination of
each subtree, and how much of each is reachable by search.

This file carried a hand-written copy of that map from 2026-04-20 until
2026-09-02. It drifted: by the end it named fourteen subtrees and omitted three
whole top-level directories, roughly 150 files. It also sat in the public
repository while describing a private tree, so regenerating it in place would
have published real counterparty names on the first run. Splitting policy from
inventory fixes both problems at once, and the policy is what belongs in an
always-loaded rule.

Read the generated map when you need the shape of the tree. Regenerate it when
it looks stale; `python scripts/datastore-map.py --check` exits 1 when it is.

## Validation workflow

1. Find the source document. `python scripts/datastore-log.py summary` says what
   appeared or changed recently; the generated map says what is there in total.
2. Read the document, or its `-extract.md` companion when the original is a
   binary.
3. Cross-check your claim against it.
4. **The DataStore wins.** If it contradicts anything in `context/` or
   `reference/`, the DataStore is right and the other file is stale.

### Binary documents are invisible until extracted

A PDF, spreadsheet, deck or Word file cannot be read by search. It becomes
reachable only when `python scripts/datastore-extract.py` writes its
`-extract.md` companion beside it. MEASURED 2026-09-02: 983 files in the tree,
593 of them binary, and only 11 of those 593 had a companion. So a document you
cannot find may well be there and simply unreadable. Check the map's Opaque
column before concluding something is absent.

## When to validate

Any external-facing content that carries a specific fact, number or claim:
LinkedIn posts, proposals, investor materials, partner communications, keynote
decks.

## Brand assets

Templates, fonts and logos live under `datastore/brand/`. Engine code never
spells their filenames; it resolves them through the private manifest, for the
reason at the top of this file. The typography, palette and letterhead rules
themselves are policy and live in `reference/corporate-style-guide.md`.

`datastore/brand/examples/` holds real production documents and is refreshed
periodically. Read it before creating new corporate material, so the output
matches current practice rather than a remembered version of it.

## Competitive intelligence

Competitor product documents are filed by vendor. When positioning ODUN.ONE
against a competitor, preparing a proposal, or answering a customer question
about differentiation, read the actual documents. Ground the claim in what the
competitor's own material says, never in an assumption about it.

Those documents are among the most sensitive in the tree. Their filenames name
the vendor and often the end customer, so the rule at the top of this file
applies to them with no exception whatsoever.
