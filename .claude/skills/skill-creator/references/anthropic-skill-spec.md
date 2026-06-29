# Skill-Creator - Anthropic SKILL.md Spec and Writing Guide

Consumed by: `.claude/skills/skill-creator/SKILL.md` "Write the SKILL.md" phase. Read when authoring or restructuring a SKILL.md to ensure it matches Anthropic's spec and the workspace's progressive-disclosure conventions.

Last Updated: 2026-06-10

---

## Frontmatter Fields

Based on the user interview, fill in these components in the SKILL.md frontmatter:

- **name**: Skill identifier (top-level, kebab-case)
- **description**: When to trigger, what it does. Top-level. This is the primary triggering mechanism - include both what the skill does AND specific contexts for when to use it. All "when to use" info goes here, not in the body. Note: currently Claude has a tendency to "undertrigger" skills -- to not use them when they'd be useful. To combat this, please make the skill descriptions a little bit "pushy". So for instance, instead of "How to build a simple fast dashboard to display internal Anthropic data.", you might write "How to build a simple fast dashboard to display internal Anthropic data. Make sure to use this skill whenever the user mentions dashboards, data visualization, internal metrics, or wants to display any kind of company data, even if they don't explicitly ask for a 'dashboard.'"
- **`x-31c-orchestration:`** (namespaced workspace extension - signals "not part of Anthropic's standard SKILL.md spec"):
  - **parallel_safe**: `true`, `partial`, or `false`. Determines if the orchestrator can dispatch this skill as a parallel background agent. `true` = read-only or writes to isolated unique output paths. `partial` = has both safe research phases and unsafe write phases (CRM, pipeline, state files). `false` = writes to shared state throughout, multi-repo operations, or inherently sequential. When in doubt, use `false`.
  - **shared_state**: List of file/directory paths this skill writes to, e.g., `["crm/contacts/", "context/pipeline.md"]`. Empty `[]` if the skill is read-only or only writes to unique timestamped output directories. The orchestrator uses this to detect conflicts between parallel agents.
  - **triggers**: List of natural-language phrases that should auto-invoke this skill via the router, e.g., `["investigate", "research", "dig into", "dossier"]`. These are action-verb focused. Empty `[]` for skills that should never auto-trigger (like /prime, /osint-advanced).
- **compatibility**: Required tools, dependencies (optional, rarely needed)
- **the rest of the skill :)**

---

## Anatomy of a Skill

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter (name, description required)
│   └── Markdown instructions
└── Bundled Resources (optional)
    ├── scripts/    - Executable code for deterministic/repetitive tasks
    ├── references/ - Docs loaded into context as needed
    └── assets/     - Files used in output (templates, icons, fonts)
```

---

## Progressive Disclosure

Skills use a three-level loading system:
1. **Metadata** (name + description) - Always in context (~100 words)
2. **SKILL.md body** - In context whenever skill triggers (<500 lines ideal)
3. **Bundled resources** - As needed (unlimited, scripts can execute without loading)

These word counts are approximate and you can feel free to go longer if needed.

**Key patterns:**
- Keep SKILL.md under 500 lines; if you're approaching this limit, add an additional layer of hierarchy along with clear pointers about where the model using the skill should go next to follow up.
- Reference files clearly from SKILL.md with guidance on when to read them
- For large reference files (>300 lines), include a table of contents

**Domain organization**: When a skill supports multiple domains/frameworks, organize by variant:

```
cloud-deploy/
├── SKILL.md (workflow + selection)
└── references/
    ├── aws.md
    ├── gcp.md
    └── azure.md
```

Claude reads only the relevant reference file.

---

## Principle of Lack of Surprise

This goes without saying, but skills must not contain malware, exploit code, or any content that could compromise system security. A skill's contents should not surprise the user in their intent if described. Don't go along with requests to create misleading skills or skills designed to facilitate unauthorized access, data exfiltration, or other malicious activities. Things like a "roleplay as an XYZ" are OK though.

---

## Writing Patterns

Prefer using the imperative form in instructions.

**Defining output formats** - You can do it like this:

```markdown
## Report structure
ALWAYS use this exact template:
# [Title]
## Executive summary
## Key findings
## Recommendations
```

**Examples pattern** - It's useful to include examples. You can format them like this (but if "Input" and "Output" are in the examples you might want to deviate a little):

```markdown
## Commit message format
**Example 1:**
Input: Added user authentication with JWT tokens
Output: feat(auth): implement JWT-based authentication
```

---

## Writing Style

Try to explain to the model why things are important in lieu of heavy-handed musty MUSTs. Use theory of mind and try to make the skill general and not super-narrow to specific examples. Start by writing a draft and then look at it with fresh eyes and improve it.
