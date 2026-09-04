# Persistence — Files In Any Format, And No Server Database

Last Updated: 2026-09-04
Last Verified: 2026-09-04

Always-active rule. This workspace is built on files and runs no server database.
SQLite is the database it uses. Operator directive, 2026-08-21, corrected twice on
2026-08-22. Resident because it fires when you PROPOSE something that persists,
which happens in prose before any file is touched.

1. **Any file format is permitted.** Markdown, JSON, JSONL, YAML, XML, CSV, plain
   text, whatever fits the data. A JSON file is not a database, and neither is a
   YAML file or a JSONL log.
2. **In-process stores are permitted.** SQLite first, because the workspace
   already runs on it; LanceDB, DuckDB, Kuzu and their kin add no daemon and no
   port, so they are a normal proposal to argue on its merits.
3. **A server database is a rejection, not a trade-off.** Named: Postgres,
   Oracle, MySQL, Microsoft SQL Server, Redis, Qdrant, Neo4j, Elasticsearch. When
   a tool, library or design needs one, say so plainly and propose the file or
   SQLite form. Do not weigh it, pilot it, or add it "just for now".
4. **Prefer the stores that already exist.** `.memory-index/index.db`,
   `.memory-index-code/index.db`, `.codegraph/codegraph.db` and the Action Queue
   are the precedent. A new index is a new table or a new store file, never a new
   service.
5. **State the storage choice when you propose anything that persists**, and
   confirm in the completion line that nothing new listens on a port, e.g.
   `Persistence: one SQLite store, no daemon.` Naming it is what lets this rule
   be applied before the work rather than after.

Configuration is out of scope: a settings file stays whatever format its consumer
expects. The two carve-outs written out, why each was a correction, the existing
stores a new index should join, and the full validation step:
`reference/persistence-detail.md`. Rationale: `docs/ARCHITECTURE.md` § 4.
