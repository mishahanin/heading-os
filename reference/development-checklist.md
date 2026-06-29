# Development Checklist

Step-by-step quality checklist for creating or modifying workspace artifacts. Use this when building skills, scripts, reference files, rules, or any workspace component.

Last Updated: 2026-03-26
Last Verified: 2026-06-10

---

## Pre-Development

- [ ] **Research with Context7.** Run `python scripts/context7.py "{library}" "{query}"` for any external library, API, or framework being used. Validate patterns against current documentation.
- [ ] **Check existing patterns.** Search workspace for similar skills/scripts. Match established conventions (frontmatter format, Python imports, output structure).
- [ ] **Use deep-think for non-trivial decisions.** Architecture choices, multi-file changes, trade-offs, and complex integration points need structured reasoning via `/deep-think` before implementation.
- [ ] **Define scope clearly.** What files will be created/modified? Document in a plan or task list before starting.

---

## New Skill Checklist

### Frontmatter
- [ ] `name:` -- kebab-case, matches directory name
- [ ] `description:` -- detailed, includes trigger phrases AND anti-trigger phrases where appropriate
- [ ] `argument-hint:` -- e.g., `"[target]"`, `"[company]"`
- [ ] `allowed-tools:` -- explicit list (e.g., `"WebSearch, WebFetch, Read, Bash(python3:*)"`)
- [ ] `context: fork` -- if skill needs isolated context
- [ ] `metadata.author:` -- `31c`
- [ ] `metadata.version:` -- semantic version starting at `"1.0"`

### Body
- [ ] Under 500 lines (use `references/` subdirectory for overflow)
- [ ] H1 title matching skill purpose
- [ ] Clear phase structure (Phase 0: context loading -> Phase N: output)
- [ ] Context loading reads: relevant context files, reference files, CRM/pipeline as needed
- [ ] Output directory follows pattern: `outputs/intel/{skill-name}/YYYY-MM-DD-[slug]/`
- [ ] Confidence ratings: HIGH/MEDIUM/LOW/UNVERIFIED applied to every section
- [ ] Voice rules section (hyphens only, ODUN.ONE, DPI+, no em-dashes)
- [ ] NEVER section (explicit prohibitions)
- [ ] Skill chain recommendations at the end

### Reference Files (in `references/`)
- [ ] H1 title
- [ ] "Consumed by:" pointer to parent SKILL.md
- [ ] "Last Updated: YYYY-MM-DD"
- [ ] Content organized by stream/category

---

## New Script Checklist

### File Structure
- [ ] Shebang: `#!/usr/bin/env python3`
- [ ] Module docstring with description and Usage examples
- [ ] All CLI flags documented in docstring

### Imports
- [ ] `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`
- [ ] `from scripts.utils.workspace import get_workspace_root` (not manual `Path.parent`)
- [ ] `from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET`
- [ ] `from scripts.utils.api import load_api_key` (if any API keys needed)
- [ ] `from pathlib import Path` (not `os.path` for path construction)

### Error Handling
- [ ] `from urllib.error import HTTPError, URLError` (in that order)
- [ ] Catch `HTTPError` BEFORE `URLError` (HTTPError is a subclass)
- [ ] Colored error output using RESET constants

### CLI
- [ ] `argparse` for command-line interface
- [ ] Subcommands or flags for different modes
- [ ] `if __name__ == "__main__":` guard

### Validation
- [ ] `python3 -m py_compile {script}` -- syntax OK
- [ ] `python scripts/sanitize-text.py {script} --scan` -- hidden characters clean

---

## New Reference File Checklist

- [ ] H1 title on line 1
- [ ] One-line description
- [ ] "Last Updated: YYYY-MM-DD"
- [ ] Clear `##` section organization
- [ ] If consumed by a skill: "Consumed by:" pointer

---

## New Rule File Checklist

- [ ] H1 title describing the rule's domain
- [ ] Actionable instructions (imperative, not passive)
- [ ] Why-first: explain reasoning before MUSTs
- [ ] Short enough to load every session without bloat (aim for <50 lines)

---

## Post-Development Validation

- [ ] **Sanitizer scan:** `python scripts/sanitize-text.py {file} --scan` on EVERY new/modified file
- [ ] **Python syntax:** `python3 -m py_compile {script}` for all Python files
- [ ] **YAML frontmatter:** Parse test confirms valid YAML, all required fields present
- [ ] **Line count:** SKILL.md under 500 lines
- [ ] **Context7 validation:** External library patterns verified against current docs
- [ ] **Live tool testing:** For any external APIs/tools, test actual endpoints and document results
- [ ] **Artifact evaluation:** Run `/evaluate {artifact-path}` for full quality assessment (deterministic + qualitative). Or use `python scripts/artifact-evaluator.py --path {artifact}` for quick deterministic-only check. Use `/implement --evaluate` to add feedback loop during implementation.

---

## Documentation Propagation (per `.claude/rules/documentation.md`)

After creating or modifying any artifact:

- [ ] **CLAUDE.md** -- Update Quick Reference section and/or skill table
- [ ] **templates/GETTING-STARTED.md** -- Update skill reference table (if new skill)
- [ ] **templates/CLAUDE.md.template** -- Update rules list (if new rule)
- [ ] **templates/CEO-ADMIN-GUIDE.md** -- Update admin tools section (if new admin tool)
