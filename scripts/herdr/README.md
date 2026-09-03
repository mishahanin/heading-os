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

Fires on Herdr's `worktree.created` and `worktree.opened` events. Eleven steps;
any failure records the step number, retitles the pane `YARD BROKEN`, sends a
notification, and **does not start the agent**.

> **It is NOT a gate, and this file called it one until 2026-09-03.** The old
> text said "synchronously, before the agent starts". MEASURED against the
> binary and CHANGELOG 0.7.1: `worktree.create` is handled ASYNCHRONOUSLY by the
> app runtime. The pane and its shell exist independently of this hook and
> nothing orders the two. So the hook cannot stop an operator typing in a YARD
> it has not finished checking; it can only report afterwards. Read every
> sentence about "does not start the agent" as "does not start the agent
> ITSELF", never as "no agent can start".
>
> Two further unknowns, named because a plan built on them would be wrong.
> No timeout for an event-hook command is documented, and step 4 is a
> multi-minute `uv sync`. The binary carries a `plugin_command_limit`
> ("maximum concurrent plugin commands reached") whose value and
> over-limit behaviour are undocumented; if it refuses, the hook may not run at
> all and leave nothing in the log.
>
> The blocking surface herdr does offer a plugin is `[[panes]]` plus
> `plugin.pane.open --placement popup`: session-modal, takes all input including
> Escape, closes when its command exits. This plugin does NOT declare one. Given
> the asynchrony above, that is the only thing that would actually hold an
> operator out of a broken YARD, and adopting it is an open decision, not an
> oversight to be quietly fixed.

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

Creating one is a single command, from HELM:

```bash
herdr worktree create --branch fix-router
```

This read `herdr worktree create fix-router` until 2026-09-03. MEASURED that
day on herdr 0.8.2: `herdr worktree create --help` prints
`Usage: herdr worktree create [OPTIONS]` and lists no `Arguments:` section at
all, and `WorktreeCreateParams` in `herdr api schema --json` has no positional
field either. The branch goes in `--branch <NAME>`. What the old line actually
DID (rejected, or accepted and silently created a worktree on the default
branch) is NOT ESTABLISHED: settling it needs a mutating run, and the answer
does not change what the correct line is.

### Removing one is THREE commands, in this order

Every one of them is a human action, never automatic. Doing only the first
leaves a dead entry in the sidebar; doing only the first two leaves the branch.

```bash
herdr workspace list                  # find the ID: result.workspaces[].workspace_id
herdr worktree remove --workspace w47 # 1. the checkout
herdr workspace close w47             # 2. the sidebar entry and its panes
git branch -d fix-router              # 3. the branch
```

**`--workspace` takes the ID, never the branch name.** MEASURED 2026-09-03:
`herdr worktree remove --help` prints `--workspace <ID>`, and the wire schema
(`herdr api schema --json`, `WorktreeRemoveParams`) names the field
`workspace_id` and requires it. `herdr workspace list` returns one line of JSON
in which the ID is `w47`-shaped and the branch appears only inside `label`
(`YARD/fix-router`) and `checkout_path`. `herdr workspace close` takes the same
ID positionally, with no flag.

The order is load-bearing: a branch cannot be deleted while a worktree still
holds it, so step 3 fails until step 1 has run.

This section documented one step out of three until 2026-09-03, and documented
it with a branch name where an ID belongs. What that cost, MEASURED by the
operator the same day: the call with a branch name did nothing and said nothing,
and after removing two probe worktrees with `git worktree remove` plus `rm -rf`
the sidebar still carried `yard-verify (deleted)` and `yard-probe2 (deleted)`,
one of them holding a live shell in a directory that no longer existed.

**Why that call was silent is NOT ESTABLISHED, and do not assume it is the
rule.** This paragraph said, until the audit of 2026-09-03 corrected it, that a
herdr command given a wrong-shaped argument does nothing and reports success.
That generalisation came from one observation and the audit refuted it. MEASURED
the same day on herdr 0.8.2, every read-only command checked failed LOUDLY:

| What was passed | What happened |
|---|---|
| an unknown ID (`workspace get w99999`, `worktree list --workspace YARD/not-an-id`, `agent get`, `pane get`, `pane read`) | `{"error":{"code":"..._not_found"}}` on stderr, empty stdout, exit 1 |
| an extra positional argument | `unknown option: ...`, exit 2 |
| an unknown flag | `unknown option: --branch`, exit 2 |
| a flag with no value | `missing value for --workspace`, exit 2 |
| a missing required positional | usage, exit 2 |

So the silent `worktree remove --workspace <branch-name>` is an OUTLIER whose
cause nobody has found. Pass the ID because the schema requires one, not because
of a mechanism this file invented to explain a single run.

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

### Operator configuration, and what is off by default

None of this is optional decoration. Each line below is a surface that reports
nothing at all until the operator turns it on, which is the same failure shape
this plugin exists to remove, one level up.

```bash
herdr plugin config-dir heading-os.yard   # where this plugin's own .env lives
```

- **`ui.toast.delivery` defaults to `"off"`.** Every `notification show` this
  plugin makes is a no-op on a machine that has not set it (this machine sets
  `"herdr"`, which is why toasts appear here and would not in a fresh clone).
  The bootstrap now reads the `shown` / `reason` fields of the response and logs
  when the operator was not told, instead of discarding the answer.
- **Pane tokens render only if you ask for them.** `hos=<state>` appears only
  where `$hos` is listed in `ui.sidebar.agents.rows`. MEASURED 2026-09-03: this
  machine's config has no `ui.sidebar` section, so every badge written before
  that date was displayed by nothing. The state is now also written to the pane
  TITLE, which needs no configuration.
- **Event hooks inherit the herdr SERVER's environment, not your shell's.** So
  `HEADING_OS_AUTOSTART=0` cannot be set by exporting it before you create a
  worktree. Put it in the plugin's own config dir, printed by the command above.
- **A keybinding for the doctor action**, if you want one:
  `[[keys.command]] type="plugin_action", command="heading-os.yard.doctor"`.

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
