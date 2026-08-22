# Persistence — Files In Any Format, And No Server Database

Last Updated: 2026-08-22
Last Verified: 2026-08-22

Always-active rule. This workspace is built on files, and it runs no server
database. SQLite is the database it uses. Operator directive, 2026-08-21,
corrected twice on 2026-08-22.

The rationale — the WSL2 durability question nobody can answer, and why SQLite
carries the same uncertainty without the daemon — lives in `docs/ARCHITECTURE.md`
§ 4.

## What this requires

1. **Any file format is permitted.** Markdown, JSON, JSONL, YAML, XML, CSV, plain
   text, or whatever else the job needs. Pick the format that fits the data. The
   workspace already runs on a mix, and that mix is correct.
2. **A server database is a rejection, not a trade-off.** When a tool, library or
   design needs one, say so plainly and propose the file or SQLite form. Do not
   weigh it, do not pilot it, do not add it "just for now". Named examples:
   Postgres, Oracle, MySQL, Microsoft SQL Server, Redis, Qdrant, Neo4j,
   Elasticsearch.
3. **Prefer the stores that already exist.** `.memory-index/index.db`,
   `.memory-index-code/index.db`, `.codegraph/codegraph.db` and the Action Queue
   are the precedent, not a new pattern. A new index is a new table or a new
   store file, never a new service.
4. **State the storage choice when you propose anything that persists.** Naming it
   is what lets this rule be applied before the work, rather than after.

## What this does NOT rule out

**File formats.** A JSON file is not a database. Neither is a YAML file, a JSONL
log, or an XML export. This rule says nothing about which format you choose, and
it never asks you to convert an existing file to Markdown.

**In-process stores.** LanceDB, DuckDB, Kuzu and their kin run inside the caller
and add no daemon, no port and no service account, so the reason for the rule
does not reach them. SQLite still comes first, because the workspace already runs
on it — but another embedded engine is a normal proposal to argue on its merits.

Both carve-outs are corrections. The rule first read "Markdown files and SQLite,
nothing else", which refused ordinary data files and every embedded store; the
operator narrowed it to server databases on 2026-08-22.

## Scope

**Data and state, not configuration.** A settings file stays whatever format its
consumer expects — `config/routing-map.yaml`, `config/tool-risk.json`,
`pyproject.toml` and `.pre-commit-config.yaml` are configuration and are untouched
by this rule.

This also governs what runs IN this workspace. It says nothing about what a
customer deployment, ODUN.ONE, or any product may use — those are separate
decisions with separate constraints.

## Validation

Before declaring any capability done, confirm nothing new listens on a port and
no server database was added. State the result in the completion line, e.g.
`Persistence: one SQLite store, no daemon.` A capability that needs a server
database is a finding to raise with the operator, never a dependency to add.
