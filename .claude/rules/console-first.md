# Console-First — No Web-Dashboard Dependency

Last Updated: 2026-09-04
Last Verified: 2026-09-04

Always-active rule. Every capability built in or for this workspace must be fully
operable from the terminal, a CLI, and Claude Code chat. The web dashboard (bridge
daemon and any future web surface) is a convenience layer, never a dependency. CEO
directive, 2026-06-03. Depending on a running headless process is allowed;
depending on a rendered web page is a defect, and a web-only capability is a
finding to fix before done, not a note to defer.

Resident because the decision this governs is made before any file exists. By the
time a path could fire, the design that depends on a browser has already been
chosen.

Ship the non-web path first, then confirm it in the completion line, e.g.
`Console-first: CLI + chat paths verified; browser optional.` The four build
requirements and the three validation questions behind that line moved on
2026-09-04 to `.claude/rules/console-first-build.md`, which is path-scoped to
`scripts/**`, `.claude/skills/**` and `.claude/agents/**` and so loads on the
first write to any capability file. Rationale and scope: `docs/ARCHITECTURE.md`
§ 5.
