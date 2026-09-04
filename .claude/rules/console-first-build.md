---
paths:
  - "scripts/**"
  - ".claude/skills/**"
  - ".claude/agents/**"
---
# Console-First — What The Check Consists Of

Last Updated: 2026-09-04
Last Verified: 2026-09-04

Path-scoped since 2026-09-04. The obligation itself stays always-on in
`.claude/rules/console-first.md`, because deciding to build something web-only is
a design decision and no path fires at the moment it is made. What moved here is
everything that only applies once you are WRITING the capability, which is a real
signal: a tool call naming `scripts/**`, `.claude/skills/**` or
`.claude/agents/**`. That is where a CLI, a skill or an agent is actually built,
so this file loads before the first line of one exists.

The rationale, the in-scope / out-of-scope boundary, and how this composes with
`.claude/rules/visual-design-discipline.md` live in `docs/ARCHITECTURE.md` § 5.

## What this requires

When building or extending any capability:

1. **Ship a non-web path first.** A CLI (`scripts/<name>.py` with argparse subcommands) and/or a chat-invocable path (skill, or a documented command Claude can run) is the primary interface. The web view, if any, comes after and is optional.
2. **The backing store is the source of truth.** State lives in files (JSON/JSONL/SQLite/markdown), not only in a daemon's memory or a browser's view. Anything the dashboard can show, a CLI can read; anything the dashboard can do, a CLI or chat can do.
3. **Single-writer is fine; web-only-writer is not.** When a capability has one writer, that writer must be driveable without a browser. The canonical example is the Action Queue: `scripts/action-queue.py list|show|approve|edit|dismiss|retry|deposit` operates on the queue file IN-PROCESS (since 2026-06-27) - no bridge daemon, no loopback HTTP. The CEO-in-terminal is the single writer; `approve` SENDS synchronously and is watched. The bridge daemon is optional (the web action-queue page is read-only FYI), so the queue works with the daemon down. A web view is never the only mutator.
4. **Degrade clearly, never silently.** If the required daemon is down, the CLI exits non-zero with a plain message ("bridge daemon not running"), not a hang and not a browser redirect.

## Validation

Before declaring any capability done, confirm the non-web path exists and works:

- Can it be driven end to end from the terminal with the browser closed? (Run it.)
- Can it be driven from Claude chat?
- Is the state readable from a CLI, not only rendered in the browser?

State the result in the completion line, e.g. `Console-first: CLI + chat paths verified; browser optional.` If a capability is web-only, that is a finding to fix before done, not a note to defer.
