# HEADING OS devcontainer

A zero-setup container that opens the engine in read-only demo mode.

Last Updated: 2026-07-09

## What this is

Opening the repository in this devcontainer (VS Code "Reopen in Container" or a
GitHub Codespace) gives you a ready environment with no local toolchain to install.
The container installs `uv`, runs a core `uv sync`, and prints demo output from
`scripts/crm-health.py` on first build. No `.env`, no API key, and no private data
repository are required.

## Why it defaults to demo data

`devcontainer.json` sets `HEADING_OS_DATA` to the in-repo `examples/` tree, so the
engine's data-root resolver lands on the bundled, read-only demo data. Demo mode
refuses writes by design (`require_writable_data_root()` raises), so nothing you run
in the demo can mutate state. The container ships no secrets and never touches a
private data repository, because a fresh clone has none.

## Switching to a real workspace

The demo is a look, not a full install. To run a real workspace:

1. Create your own private data repository: `uv run python scripts/create-data-repo.py`
   (or point `HEADING_OS_DATA` at an existing one).
2. Rebuild the container so the new environment is picked up.
3. Arm the integrations you need. Core `uv sync` installs only the light always-on
   set; run `uv sync --all-extras` for the full integration surface.

The full zero-to-running walk-through is in `docs/DEPLOYMENT.md`.
