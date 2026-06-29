# Console-First — No Web-Dashboard Dependency

Last Updated: 2026-06-03
Last Verified: 2026-06-03

Always-active rule. Every capability built in or for this workspace must be fully operable from the terminal, a CLI, and Claude Code chat. The web dashboard (bridge daemon and any future web surface) is a convenience layer, never a dependency. CEO directive, 2026-06-03.

## The principle

Misha operates from the terminal and from Claude Code chat. A capability that can only be exercised through a browser is, in practice, invisible to how he works and couples core function to a presentation layer. So the browser is additive — it visualises and accelerates — but it is never the only path to any action.

Daemons are fine. A headless always-on service (the bridge daemon, sync-exchange, sentinel, etc.) is acceptable as a dependency because it has no UI requirement — you drive it from a CLI or chat. A *web dashboard* dependency is not acceptable. The distinction: depending on a running process is allowed; depending on a rendered web page is a defect.

## What this requires

When building or extending any capability:

1. **Ship a non-web path first.** A CLI (`scripts/<name>.py` with argparse subcommands) and/or a chat-invocable path (skill, or a documented command Claude can run) is the primary interface. The web view, if any, comes after and is optional.
2. **The backing store is the source of truth.** State lives in files (JSON/JSONL/SQLite/markdown), not only in a daemon's memory or a browser's view. Anything the dashboard can show, a CLI can read; anything the dashboard can do, a CLI or chat can do.
3. **Single-writer is fine; web-only-writer is not.** When a capability has one writer, that writer must be driveable without a browser. The canonical example is the Action Queue: `scripts/action-queue.py list|show|approve|edit|dismiss|retry|deposit` operates on the queue file IN-PROCESS (since 2026-06-27) - no bridge daemon, no loopback HTTP. The CEO-in-terminal is the single writer; `approve` SENDS synchronously and is watched. The bridge daemon is optional (the web action-queue page is read-only FYI), so the queue works with the daemon down. A web view is never the only mutator.
4. **Degrade clearly, never silently.** If the required daemon is down, the CLI exits non-zero with a plain message ("bridge daemon not running"), not a hang and not a browser redirect.

## Scope

**In scope (rule applies):** every script, skill, daemon, workflow, and feature that exposes an action or surfaces state — Action Queue, Cold-Sweep, recall, intel, CRM, comms, content, operations. New capability of any kind.

**Out of scope:** purely presentational polish of the dashboard itself (a chart, a colour, a layout) that adds no capability the CLI/chat lacks; third-party web tools the workspace merely calls (LinkedIn, Google); and the dashboard's role as a *viewer* of state that is already fully CLI/chat-operable.

## How this composes with the visual-design rule

`.claude/rules/visual-design-discipline.md` governs how the dashboard *looks* when it exists. This rule governs whether the dashboard is *required*. They do not conflict: build the CLI/chat path first (this rule), and when a web view is also built, design it well (that rule). A beautiful dashboard that is the only way to do something still violates this rule.

## Validation

Before declaring any capability done, confirm the non-web path exists and works:

- Can it be driven end to end from the terminal with the browser closed? (Run it.)
- Can it be driven from Claude chat?
- Is the state readable from a CLI, not only rendered in the browser?

State the result in the completion line, e.g. `Console-first: CLI + chat paths verified; browser optional.` If a capability is web-only, that is a finding to fix before done, not a note to defer.

## Classification

Corporate (shared with all execs via the `.claude/rules/` directory default) — the constraint applies to every workspace in the fleet, not just the CEO's.

## Change control

Changes to this rule require Misha's explicit approval.
