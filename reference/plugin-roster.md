# Installed plugin roster

What is installed, what is enabled, what is deliberately off, and what each one
costs per turn. Moved out of the always-on `.claude/rules/skill-router.md` on
2026-08-20, which keeps only the routing rule.

Consumed by: `.claude/rules/skill-router.md`.

Last Updated: 2026-08-20

**Do not trust this file over the machine.** Enablement lives in
`.claude/settings.json` (tracked), `.claude/settings.local.json` (gitignored,
machine-local) and `~/.claude/settings.json` (user level, EMPTY here). Verify
with `python scripts/harness-audit.py`, which enumerates the installed surface
and the third-party hooks actually running.

This list was reconciled against all three sources on 2026-08-20. Before that
date it named four plugins that were enabled nowhere and omitted three that were
running.

Plugins shipped via the Claude Code plugin system expose skills under a `plugin:skill` namespace. Enablement lives in two tiers: workspace-level `.claude/settings.json` `enabledPlugins`, and user-level `~/.claude/settings.json` `enabledPlugins`. The user tier is EMPTY on this machine, so `.claude/settings.json` is the whole picture.

The list below was reconciled against `.claude/settings.json`, `~/.claude/plugins/installed_plugins.json`, and the plugin cache on 2026-08-20. Before that date it named four plugins that were enabled nowhere (`code-review`, `code-simplifier`, `andrej-karpathy-skills`, `context7`) and omitted three that were running (`security-guidance`, `mattpocock-skills`, `claude-security`). Re-verify with `python scripts/harness-audit.py`, not by reading this list.

**Enabled, ships skills only (no hooks, no per-turn cost):**

- `superpowers:*` v6.2.0 - 14 skills: brainstorming, writing-plans, executing-plans, subagent-driven-development, using-git-worktrees, test-driven-development, systematic-debugging, verification-before-completion, receiving-code-review, requesting-code-review, finishing-a-development-branch, writing-skills, using-superpowers, dispatching-parallel-agents. The v6 major (upgraded from v5.1.0 on 2026-07-14, then 6.1.1 to 6.2.0 on 2026-08-06) kept the same 14-skill set throughout, so all namespaced-name bindings remain valid. The 6.1.1 directory is still in the plugin cache and is NOT loaded; `installed_plugins.json` names 6.2.0, and `scripts/harness-audit.py` reports the superseded copy separately rather than as a running hook. Invoke each skill by its namespaced name (`superpowers:brainstorming`) via the Skill tool. The `using-superpowers` skill bootstraps the set at SessionStart via the plugin's own hook.
- `skill-creator:skill-creator`
- `claude-md-management:revise-claude-md`, `claude-md-management:claude-md-improver`
- `frontend-design:frontend-design`
- `mattpocock-skills:*` v1.2.2 - 35 skills and 8 agents: diagnosing-bugs, tdd, prototype, research, domain-modeling, codebase-design, code-review, resolving-merge-conflicts, wizard, grilling, writing-for-agents, and more. The largest plugin here by skill count.
- `code-review:code-review` - one slash command, no agent and no hook. Code review pass on the active branch or pending changes. Invoke explicitly when a major project step is finished.
- `code-simplifier:code-simplifier` - one agent definition, no hook. Refines code for clarity while preserving behaviour. Invoke explicitly after a non-trivial change.

**Enabled, ships hooks that run on your turns:**

- `claude-security` v0.10.0 - enabled in `.claude/settings.local.json` (machine-local, not the tracked `settings.json`). Ships the `claude-security:scan` skill and seven agents. Its only hook is a display banner on `UserPromptExpansion` matching its own slash command, so it costs nothing on an ordinary turn.

**Deliberately disabled:**

- `security-guidance` v2.0.6 - disabled 2026-08-20 on measurement, after being enabled and undocumented for months. It registered a `SessionStart` bootstrap (timeout 180s, building a `claude_agent_sdk` venv under `~/.claude/security/`), a `UserPromptSubmit` hook, a `PostToolUse` hook on every write, five conditional `PostToolUse` hooks on `Bash` gated to `git commit` / `git push` / three Graphite commands, and a `Stop` hook. Measured: **333 ms per write against this workspace's own 71 ms**, paid on every Write and Edit. Against that, its own 1.3 MB log across 12,308 lines contains **zero findings** — it has never reported anything here. What it duplicates is already gated three times (`_dispatch.py` `check_prevent_secrets`, the `secret-scanner-31c` pre-commit hook, the unbypassable push-time content scan in `push-all.py`). Its one non-duplicate capability, an LLM review of each commit diff, is available on demand through `/code-review` and `claude-security:scan`. Re-enable it deliberately if you want the commit review always-on; do not let it back by accident. Its `~/.claude/security/` state directory (293 MB, mostly the SDK venv) is machine-local and can be deleted if the decision holds.
- `playwright` - the workspace drives browsers through `scripts/browser.py` and the local `/playwright` skill.
- `context7` - it ships an `.mcp.json` and would start an MCP server that duplicates the local `/context7` skill and `scripts/context7.py`. Cost with no gain.

**Documented before, not installed:** `andrej-karpathy-skills` is not in the plugin cache; only its marketplace (`forrestchang/andrej-karpathy-skills`) is registered. Install it with `claude plugin install andrej-karpathy-skills@karpathy-skills` before enabling it.

**Routing rule:** These skills are **never auto-routable from natural language**. The router does not match them against any trigger. They require one of:

1. Explicit slash-command form typed by the user (e.g., `/superpowers:brainstorming`)
2. Explicit Skill tool invocation by Claude when the plugin's own metadata says it applies (e.g., `using-superpowers` fires at session start per its own description)
3. Direct invocation by another skill that references it

**Why:** Plugin content evolves independently of this workspace. Auto-routing based on local keyword guesses would produce false positives against skills whose actual purpose may drift. When a plugin skill clearly applies, Claude invokes it explicitly; otherwise, local registry wins.

**Local-skill naming collision:** If a local `.claude/skills/{name}` ever collides with a plugin skill name (e.g., workspace has `/skill-creator` and plugin exposes `skill-creator:skill-creator`), the local skill wins on bare-name lookup. Use the namespaced form to force the plugin variant.
