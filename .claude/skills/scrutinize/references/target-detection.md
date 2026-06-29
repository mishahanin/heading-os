# Target Detection Protocol - /scrutinize

**Consumed by:** `.claude/skills/scrutinize/SKILL.md`
**Last Updated:** 2026-05-27

Hybrid auto-detect with user confirmation. The skill reads recent conversation and git state, picks the most likely target, announces it in one line, and waits for confirmation or redirect.

## Detection Priority (first match wins)

| # | Condition | Target |
|---|---|---|
| 1 | Explicit argument (`plan` / `execution` / `workspace` / `file:<path>` / `dir:<path>` / `trajectory:<run_id>`) | Use argument; skip confirmation |
| 1b | A `/implement` run completed in the current session AND `outputs/operations/implement/_trajectory_*.jsonl` exists from this session | `trajectory:<latest run_id>` (single-turn detection only — does not persist across sessions) |
| 2 | A plan was presented in the last 10 conversation turns but not yet approved | `plan` |
| 3 | `git status` shows uncommitted changes AND edits occurred this session | `execution` (scope from git) |
| 4 | None of the above | Ask (see Menu) |


## Confirmation Line Format

After auto-detection at priorities 2-3, print exactly one line:

```
Target detected: <label>. Proceeding with VIIA pass. Redirect with: plan | execution | file:<path> | dir:<path> | workspace.
```

Wait for user response. Any redirect supersedes auto-detection. A silence followed by a correction ("wait, I meant workspace") also supersedes.

## Menu (priority 4)

If nothing obvious:

```
No obvious target in context. What should I scrutinize?

1. plan
2. execution
3. file:<path>
4. dir:<path>
5. workspace
6. trajectory:<run_id>

Reply with the number, the name, or an explicit argument like `file:path/to/thing.md`.
```

Wait for response.

## Scope Resolution per Target

### plan
Source: the most recent plan presented in the conversation (search the last 10 turns for a block that looks like a plan - numbered steps, success criteria section, or a plan file created by `/create-plan`).
If no plan is in the last 10 turns, fall through to priority 5 menu.

### execution (hybrid)

Resolve scope in this order:

1. If `git status --porcelain` shows changes OR `git log --since="2 hours ago" --oneline` returns commits, use the union of both sets of files.
2. Else prompt:

```
Scope for execution review:
1. Uncommitted changes only (git status)
2. Last commit
3. Last N commits (specify N)
4. Specific SHA range (specify)

Default: 1
```

### file:<path>
Source: read the file at `<path>`. If path does not exist, stop and report the missing path.

### dir:<path>
Source: glob `<path>/**/*` with these exclusions: `.git/`, `node_modules/`, `outputs/`, `_secure/`, `.sessions/`, `*.pen`, binary files. Review each file per its type.

### workspace
Source: the entire main workspace. Dispatch 5 parallel specialist agents per `workspace-areas.md`.

### trajectory:<run_id>

Source: read `outputs/operations/implement/_trajectory_{run_id}.jsonl`, parse all events, group by phase and step number, build a normalised view (per-step record: start, end, files, validation, deviation).

**Resolution with explicit error paths:**

1. **Exact match first**: try to read `outputs/operations/implement/_trajectory_{run_id}.jsonl` literally.
2. **If exact match fails**, glob `outputs/operations/implement/_trajectory_*{run_id}*.jsonl` (substring match):
   - **Zero matches** → error: `"No trajectory matches '{run_id}'. Available (5 most recent): <list>."` and halt.
   - **One match** → use it; print `"Resolved '{input}' -> '{full_run_id}' (substring match)."` and proceed.
   - **Multiple matches** → menu showing 5 most-recent matches, ask CEO to disambiguate.
3. **Malformed run_id**: if input does not match regex `^\d{4}-\d{2}-\d{2}_\d{6}_[a-z0-9-]+$` AND no substring matches found, error: `"Malformed run_id '{input}'. Expected format: YYYY-MM-DD_HHMMSS_<plan-slug> (e.g. 2026-05-27_134522_r12-trajectory-evaluation). Run /implement first if no trajectories exist yet."` and halt.

Run-id slug equals the part after the final `_` in the run_id (matches `scripts/implement-trajectory-log.py` minting rule).

## Worked Examples

**Example 1 - plan detected:**

```
User: /scrutinize
[Recent turn includes a plan in numbered steps. No /implement manifest. Clean git status.]
Skill output: "Target detected: plan. Proceeding with VIIA pass. Redirect with: plan | execution | file:<path> | dir:<path> | workspace."
User: ok
Skill: proceeds with plan-target VIIA pass.
```

**Example 2 - explicit argument bypasses confirmation:**

```
User: /scrutinize workspace
Skill: no confirmation needed; immediately dispatches the 5 specialist agents.
```

**Example 3 - nothing obvious:**

```
User: /scrutinize
[No plan, clean git.]
Skill: prints the 6-option menu and waits.
```

**Example 4 - trajectory auto-detection right after /implement:**

```
User: /implement plans/2026-05-27-r12-trajectory-evaluation.md
[/implement runs to completion, writes outputs/operations/implement/_trajectory_2026-05-27_134522_r12-trajectory-evaluation.jsonl]
User: /scrutinize
Skill: "Target detected: trajectory:2026-05-27_134522_r12-trajectory-evaluation. Proceeding with VIIA pass. Redirect with: plan | execution | file:<path> | dir:<path> | workspace | trajectory:<run_id>."
User: ok
Skill: proceeds with trajectory-target VIIA pass per references/trajectory-evaluation.md.
```

**Example 5 - partial-match resolution:**

```
User: /scrutinize trajectory:r12-trajectory-evaluation
Skill: exact match fails (missing date prefix). Globs _trajectory_*r12-trajectory-evaluation*.jsonl.
       One match found: 2026-05-27_134522_r12-trajectory-evaluation.
       Prints: "Resolved 'r12-trajectory-evaluation' -> '2026-05-27_134522_r12-trajectory-evaluation' (substring match)."
       Proceeds.
```
