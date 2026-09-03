# Herdr plugin: HEADING OS YARD bootstrap

Last Updated: 2026-09-03
Last Verified: 2026-09-03

Provisions a **YARD** — a git worktree of this engine, on its own branch, checked
out outside the engine clone — and refuses to start an agent in it until the
engine's guards have been *proved* to fire there.

**HELM** is the main clone on `main`, where everything live runs. Full design:
`docs/ARCHITECTURE.md` § HELM and YARD, and the `## HELM and YARD` section of
`CLAUDE.md`.

## Install

Once per machine. Nothing is downloaded and nothing is copied: `link` registers
the directory in place, so the plugin IS these tracked files and cannot drift
from the repo. Link the path inside HELM, which is always on `main`.

```bash
herdr plugin link "$PWD/scripts/herdr/heading-os-yard"   # from HELM
herdr plugin list
```

Do not copy the directory somewhere else first and link the copy. Installed that
way on 2026-09-03, the copy was byte-identical on the day and would have gone on
answering `worktree.created` with whatever it held the day it was made, while
every edit to the tracked files did nothing.

Then, in `~/.config/herdr/config.toml` (`herdr --help` prints its path):

```toml
[worktrees]
directory = "/home/administrator/ai/claude-workspaces/.yard"
```

Herdr's own default is `~/.herdr/worktrees`, and it lays checkouts out as
`<directory>/<repo>/<branch-slug>` — so a YARD for branch `fix-router` lands at
`<directory>/.heading-os/fix-router`. The directory must be **outside** the
engine clone: a file created by accident inside a task then physically cannot
land in the engine's working tree.

`herdr server reload-config` applies it without restarting the panes. It prints
`"status":"applied"` with an empty `diagnostics` array when the key was accepted;
a typo in the section or key name shows up there rather than at worktree-create
time.

Separately, once, from HELM:

```bash
python scripts/install-data-overlay-guard.py
```

That installs the data overlay's `pre-commit` guard. It refuses to overwrite an
existing hook and prints the fragment to merge by hand instead.

## What the bootstrap does

Fires on Herdr's `worktree.created` event, synchronously, before the agent
starts. Eleven steps; any failure records the step number, marks the pane
`BROKEN`, sends a notification, and **does not start the agent**.

| # | Step |
|---|---|
| 1 | Resolve HELM through `git rev-parse --git-common-dir` |
| 2 | Copy `.env` from HELM, **strip `WORKSPACE_ROOT`**, write an absolute `HEADING_OS_DATA` |
| 3 | Disable this worktree's push URL |
| 4 | `uv sync` |
| 5 | `./scripts/setup-platform.sh` from **this** copy |
| 6 | Assert the PreToolUse walls are registered here |
| 7 | `assert_data_root_external()` |
| 8 | Assert `get_workspace_root()` is this checkout |
| 9 | Tree-clean must PASS on a clean tree |
| 10 | **Canary**: a decoy in a private-routed path, and tree-clean must FAIL |
| 11 | Status `ok`, start the agent with `HEADING_OS_YARD=1` |

Step 10 is the one that matters. A guard pointed at the wrong tree reports
clean, and a clean report is what a healthy guard produces, so the two states
are indistinguishable by their result. The decoy makes the guard prove it can
fail here.

Three measurements shape the script, all taken 2026-09-03 on this machine, and
each one broke an earlier draft:

- `.venv` does not exist in a fresh worktree, so the status file is written with
  `printf` and nothing calls `.venv/bin/python` before step 4.
- `jq` is not installed, so the event JSON is parsed with the system `python3`.
- Six of the eight private directories the original specification listed as
  canary candidates are gitignored, and the wall reads
  `git ls-files --others --exclude-standard`, so a decoy in any of them is
  invisible. Only `docs/security/` and `auto-memory/` work.

## Operating

```bash
herdr worktree create fix-router            # from HELM
herdr worktree remove --workspace fix-router # a human action, never automatic
```

Diagnosis:

```bash
cat <yard>/.claude/.yard-bootstrap-status          # status and the step it stopped at
herdr plugin log list --plugin heading-os.yard     # the bootstrap's own log
git worktree list                                  # stale registrations
python scripts/install-data-overlay-guard.py --check
```

Re-run the bootstrap on an existing YARD:

```bash
cd <yard>
FORCE_BOOTSTRAP=1 HERDR_PLUGIN_EVENT_JSON='{"worktree":{"path":"'"$PWD"'"}}' \
  bash scripts/herdr/heading-os-yard/yard-bootstrap.sh
```

Without `FORCE_BOOTSTRAP=1` a healthy YARD (`status: ok`) is left alone.

## Files

| File | What it is |
|---|---|
| `heading-os-yard/herdr-plugin.toml` | the manifest: the `worktree.created` event and the `doctor` action |
| `heading-os-yard/yard-bootstrap.sh` | the eleven steps above |
| `heading-os-yard/data-overlay-pre-commit` | the hook body `install-data-overlay-guard.py` installs |

## Tests

`tests/test_yard_bootstrap_lint.py`,
`tests/test_a_data_overlay_guard_that_overwrote_what_was_there.py`,
`tests/test_a_yard_that_could_still_write_into_the_helm.py`,
`tests/test_guarded_shell_installers_refuse_from_a_worktree.py`,
`tests/test_guarded_entry_points_refuse_from_a_worktree.py`,
`tests/test_clone_guard.py`,
`tests/test_a_guard_that_scanned_the_wrong_tree.py`.
