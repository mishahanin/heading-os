# Persistence — Markdown Files and SQLite, Nothing Else

Last Updated: 2026-08-21
Last Verified: 2026-08-21

Always-active rule. State in this workspace lives in Markdown files or in SQLite.
No other store is permitted. Postgres, MySQL, Redis, Qdrant, Neo4j, Elasticsearch
and every other server-process database are ruled out. Operator directive,
2026-08-21.

The rationale — the WSL2 durability question nobody can answer, and why SQLite
carries the same uncertainty without the daemon — lives in `docs/ARCHITECTURE.md`
§ 4.

## What this requires

1. **A server database is a rejection, not a trade-off.** When a tool, library or
   design needs one, say so plainly and propose the Markdown or SQLite form.
   Do not weigh it, do not pilot it, do not add it "just for now".
2. **Prefer the stores that already exist.** `.memory-index/index.db`,
   `.memory-index-code/index.db`, `.codegraph/codegraph.db` and the Action Queue
   are the precedent, not a new pattern. A new index is a new table or a new
   store file, never a new service.
3. **"Embedded" is not the test. SQLite is the test.** LanceDB, Kuzu, DuckDB,
   usearch, LMDB and every other embedded store run in the caller and add no
   process, and they are still out: the permitted set is Markdown and SQLite,
   named literally, because a rule that asks "does this add a process?" needs a
   judgement every time while this one is answered by reading a file header.
   A tool whose default store is embedded-but-not-SQLite is refused on the same
   terms as a server database.
4. **State the storage choice when you propose anything that persists.** Naming it
   is what lets this rule be applied before the work, rather than after.

## Scope

**Data and state, not configuration.** A settings file stays whatever format its
consumer expects — `config/routing-map.yaml`, `config/tool-risk.json`,
`pyproject.toml` and `.pre-commit-config.yaml` are configuration and are untouched
by this rule. The constraint is on where DATA and STATE live: records, indexes,
queues, caches, embeddings, logs of what happened. The test is what the file is
for, not what it is written in.

This also governs what runs IN this workspace. It says nothing about what a
customer deployment, ODUN.ONE, or any product may use — those are separate
decisions with separate constraints.

## Validation

Before declaring any capability done, confirm every new persistent artifact is a
Markdown file or a SQLite database, and that nothing new listens on a port. State
the result in the completion line, e.g. `Persistence: one SQLite store, no daemon.`
A capability that needs a server database is a finding to raise with the operator,
never a dependency to add.
