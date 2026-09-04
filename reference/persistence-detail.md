# Persistence rule — the carve-outs, the scope line, and why they were corrected

Companion to the always-on `.claude/rules/persistence.md`, which carries the rule
itself. This file carries the argument: what the rule does NOT forbid, where its
edge is, and the two corrections that put it there. None of it is an obligation,
which is why none of it is resident. Read it when a persistence choice is being
challenged or when someone proposes a store the rule does not obviously cover.

Rationale for the rule itself, the WSL2 durability question nobody can answer,
and why SQLite carries the same uncertainty without the daemon:
`docs/ARCHITECTURE.md` § 4.

## What this does NOT rule out

Both carve-outs are corrections: the rule first read "Markdown files and SQLite,
nothing else", which refused ordinary data files and every embedded store, and
the operator narrowed it to SERVER databases on 2026-08-22.

- **File formats.** A JSON file is not a database. Neither is a YAML file, a
  JSONL log or an XML export. This rule says nothing about which format you
  choose and never asks you to convert an existing file to Markdown.
- **In-process stores.** LanceDB, DuckDB, Kuzu and their kin run inside the
  caller and add no daemon, no port and no service account, so the reason for the
  rule does not reach them. SQLite still comes first because the workspace
  already runs on it, but another embedded engine is a normal proposal to argue
  on its merits.

## Scope

**Data and state, not configuration.** A settings file stays whatever format its
consumer expects — `config/routing-map.yaml`, `config/tool-risk.json`,
`pyproject.toml`, `.pre-commit-config.yaml` are configuration and untouched by
this rule. This also governs what runs IN this workspace; it says nothing about a
customer deployment, ODUN.ONE, or any product.

## Prefer the stores that already exist

`.memory-index/index.db`, `.memory-index-code/index.db`, `.codegraph/codegraph.db`
and the Action Queue are the precedent. A new index is a new table or a new store
file, never a new service.

## Validation

Before declaring any capability done, confirm nothing new listens on a port and
no server database was added. State it in the completion line, e.g.
`Persistence: one SQLite store, no daemon.` A capability that needs a server
database is a finding to raise with the operator, never a dependency to add.
