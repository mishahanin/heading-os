---
name: backup
disable-model-invocation: true
description: Backup the entire workspace to GitHub. EXPLICIT INVOCATION ONLY - never auto-trigger from natural language.
argument-hint: "(no arguments)"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.3"
allowed-tools: "Bash(git:*), Bash(python3:*)"
model: haiku
x-heading-orchestration:
  parallel_safe: false
  shared_state: []
  triggers:
    - backup
    - push to github
    - save workspace
x-heading-capability:
  what: >
    Commits all workspace changes and pushes them to GitHub origin/main, so the
    entire workspace is backed up off-machine.
  how: >
    Explicit invocation only - type /backup (no arguments); never auto-triggers.
    It runs git add/commit/push, excluding corporate and CRM-clone trees on exec
    workspaces, and reports the file count pushed.
  when: >
    Use to save the whole workspace to GitHub. To publish content to executives
    use /publish-corporate; for a full versioned push with CRM aggregate use
    /push-updates.
x-heading-routing:
  category: Operations
  triggers:
    - NEVER auto-trigger. Explicit `/backup` only.
  exclusions:
    - Corporate publish -> /publish-corporate
  compound: 'No'
  router: manual
---
Backup the entire workspace to GitHub.

## CEO two-part topology (engine + data): use `push-all.py`

The CEO workspace is split into two repos — the ENGINE clone (`.heading-os`,
code only) and the DATA overlay (`.heading-os-data`, all data + every
artifact), each with its own private `origin/main`. The single command that
commits and pushes BOTH, with a pre-push secret scan and an ahead/behind
`[0 0]` verification (a bare push can silently leave a ref behind), is:

```
python scripts/push-all.py            # commit working-tree changes + push both
python scripts/push-all.py -m "msg"   # custom commit message
python scripts/push-all.py --no-commit  # push existing commits only
python scripts/push-all.py --dry-run    # show what would happen, change nothing
```

It reads `GH_TOKEN` from the engine `.env`, refuses to push any tracked
secret-like file (`.env`, `.session`, `cookies.json`, `.sessions/`), and never
pushes `.memory-index/` (gitignored, rebuildable). The DATA overlay is pushed
FIRST, because the engine's pre-push hook runs the full suite inside the push and
data is the only half that cannot be reconstructed. Prefer this over the manual
git steps below whenever the data overlay exists (`get_data_root()` differs
from the engine root). The manual steps remain the path for exec workspaces and
the pre-cutover single-repo case.

**Exit `3` means at least one repository was skipped. Read the HEADLINE, not the
code, because it has two shapes and they mean opposite things to the operator.**

- **`Partial: N of M repo(s) not pushed.`** — the backup was partial, not failed.
  Report it that way. The repositories named below the headline were not pushed, for
  the reason printed next to each; everything else was pushed and verified.
- **`NOTHING PUSHED: all M repo(s) skipped.`** — nothing reached a remote and there
  is NO new off-machine copy. Never report this as a partial success. The exec and
  pre-cutover single-repo modes push one repository, so exit `3` there is always this
  shape.

In both shapes every skipped repository is still committed locally, so nothing is
lost. Tell the operator which repository was skipped, quote the reason, and give the
remedy: usually merge the branch into `main` and run the command again, or
`python scripts/install-git-hooks.py` for an unarmed engine test gate. Do NOT re-run
the command hoping for a different answer.

Exit `1` and `2` are real failures that stopped the run (a security refusal, an
absent `GH_TOKEN`, a misconfigured data root, a push that ran and did not verify).
Because the DATA overlay is pushed first, a failure at the ENGINE can still leave
DATA pushed and verified — read the per-repository lines above the failure before
telling the operator nothing was backed up. Exit `0` means everything went.

## Exec workspaces: also `push-all.py`

An executive workspace is the same two-repo topology from the exec's side: a
READ-ONLY engine clone (`.heading-os`, consumed via `git pull`; its origin is the
CEO's engine repo, so the exec cannot push it) and a WRITABLE data overlay
(`heading-os-data-{slug}`). `push-all.py` is exec-aware — it reads
`.workspace-identity.json`, detects the `exec-workspace` type, and pushes ONLY the
data overlay, skipping the engine entirely:

```
python scripts/push-all.py            # commit + push the data overlay
python scripts/push-all.py --dry-run  # show what would happen, change nothing
```

Do NOT `git add -A` / commit in the engine clone on an exec — it is read-only and
all real artifacts already resolve into the data overlay via the `get_*_dir()`
helpers. Machine-local config (`.zed/`, `.claude/settings.local.json`) stays local
and uncommitted by design (gitignored). Corporate content lives in the gitignored
`.corporate-repo/` clone and is refreshed by `/sync`, never by `/backup`.

## Manual steps (only if `push-all.py` is unavailable)

Fallback for the rare case the script cannot run. The repo target differs by
workspace:
- **Exec:** push the DATA overlay (`heading-os-data-{slug}`), NEVER the engine clone.
- **CEO single-repo (pre-cutover, data root == engine root):** push the one repo.

Steps:
1. `cd` into the correct repo (the data overlay for an exec; the workspace root for
   the single-repo CEO case).
2. Run `git status --short`. If there are no changes, say "Nothing to commit" and stop.
3. `git add -A`, review with `git diff --cached --stat`, commit, push origin main,
   and confirm what was pushed.

Important:
- Always end the commit message with: `Co-Authored-By: Claude <noreply@anthropic.com>`
- If any file exceeds GitHub's 100MB limit, remove it from staging, add it to
  `.gitignore`, and proceed with the rest.
- Never push secrets (`.env` files) — these are already in `.gitignore`.
- Never pass `--no-verify`.
