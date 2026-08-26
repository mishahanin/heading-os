---
id: EXAMPLE-thread
title: Example Business Thread
status: active
type: business
classification: ceo-only
opened: 2026-01-15
last_touched: 2026-01-20
counterparties:
  - Example Contact (Example Co)
links:
  crm: []
  pipeline: []
  outputs: []
  knowledge: []
tags:
  - example
---

Read-only demo thread shipped with the engine. Real threads live in your private
data folder. Run `python scripts/init-data.py` to create yours.

The frontmatter above is not decoration. Every thread tool in this repository
parses it, and this file had none until 2026-08-27: on a clone with no private
data folder, `python scripts/thread.py list` answered with a warning about the
one file the engine itself ships, and the census benchmark refused to grade at
all. A demo file that the engine's own parser rejects is worse than no demo file.

## 2026-01-20 - Log

Example log entry. A real thread carries one dated section per update.
