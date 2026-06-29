# .claude/settings.json — CORPORATE-CLASSIFIED

This file is **corporate-classified** (see `config/routing-map.yaml`).

It is published to every executive workspace via the corporate content repo (`heading-os-corporate`); execs pick it up on their next `git pull`. (The hourly `workspace-sync.py` push was retired -- see `plans/2026-06-26-retire-workspace-sync-disk-import.md`.)

(As of 2026-04-25, AIOS-for-the-CEO is an independent OSS repo and is no longer fed from this file.)

## Implications

- Anything added to `.claude/settings.json` in ceo-main propagates to all executives on the next `/push-updates`.
- The `enabledPlugins` field controls which Claude Code plugins load for every executive. Adding a plugin here enables it for everyone. Removing one disables it everywhere.
- Machine-specific settings (model overrides, personal shortcuts, experimental flags) belong in `.claude/settings.local.json`, which is gitignored and never synced.

## Plugin policy

The shipped plugin set is intentional. Do not add plugins here for one-off experiments — do that in `settings.local.json` first, validate the plugin is stable and useful, then promote it here if it should ship to the fleet.

Current shipped plugins:

- `superpowers@claude-plugins-official` — brainstorming, planning, TDD, scrutinize workflows
- `skill-creator@claude-plugins-official` — authoring new skills
- `claude-md-management@claude-plugins-official` — maintaining CLAUDE.md files
- `frontend-design@claude-plugins-official` — UI/design workflows
- `playwright@claude-plugins-official` — browser automation (screenshots, scraping, PDF generation, YouTube transcripts)

## Host prerequisite: `python3` on PATH

Every hook in `settings.local.json` invokes `python3 -c "..."` directly. The walk-up bootstrap is cross-platform (`Path.cwd().parents` resolves on Windows, Linux, macOS), but the binary itself must be on PATH under the name `python3`.

- **Linux / macOS / WSL:** `python3` is the standard executable name. No action needed.
- **Windows:** ensure `python3` resolves. Two options:
  1. Microsoft Store Python (the default Windows install) ships a `python3` alias alongside `python`. Verify with `where python3` in PowerShell.
  2. If only `python.exe` is on PATH (custom installs), add a doskey alias `doskey python3=python $*` to a startup script, or symlink `python.exe` to `python3.exe`.
- **Failure mode:** if `python3` is not resolvable, hooks silently no-op (Claude Code skips a hook line whose command returns nonzero). The session still runs, but PreToolUse guardrails (`_dispatch.py`), checkpoint state, and bridge-daemon heartbeat updates are missing. Verify by checking `outputs/operations/bridge/state.json` updates after a session start, or running `python3 --version` in the workspace shell.

## Adding or removing a plugin

1. Edit `enabledPlugins` in `.claude/settings.json` in ceo-main.
2. Update this README if the shipped set changed (plus `templates/GETTING-STARTED.md` and `templates/CEO-ADMIN-GUIDE.md`).
3. Run `/push-updates` to publish to executives.

Executives receive the change on their next sync and Claude Code auto-fetches the plugin from the marketplace the next time they start Claude in the workspace.
