---
name: ast-grep
description: Guide for writing ast-grep rules to perform structural code search and analysis. Use when users need to search codebases using Abstract Syntax Tree (AST) patterns, find specific code structures, or perform complex code queries that go beyond simple text search. This skill should be used when users ask to search for code patterns, find specific language constructs, or locate code with particular structural characteristics.
argument-hint: "[search-query-or-pattern]"
allowed-tools: "Read, Glob, Grep, Bash(ast-grep:*), Bash(sg:*)"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
  upstream: ast-grep/agent-skill
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - "ast-grep"
    - "structural code search"
    - "AST pattern"
    - "find all functions that"
    - "find calls to"
    - "find code matching"
    - "structural pattern"
x-31c-capability:
  what: >
    Translates a plain-language code query into ast-grep AST patterns or YAML
    rules and runs them, finding code by structure (node kind, has/inside
    relations) rather than text - e.g. "async functions with no try-catch".
  how: >
    Run /ast-grep <search-query-or-pattern>. It writes a test snippet, drafts
    the rule, verifies it with ast-grep scan/run, then searches the codebase.
  when: >
    Use for structural code search across a codebase. For plain text or
    filename matching use Grep; for a semantic "what does this do" question
    just answer it.
---

# ast-grep Code Search

> Adapted from upstream [ast-grep/agent-skill](https://github.com/ast-grep/agent-skill). Workspace-customized frontmatter; body refactored 2026-06-16 (CLI catalog split to `references/cli-cookbook.md`, voice/never sections added).

## Overview

This skill helps translate natural language queries into ast-grep rules for structural code search. ast-grep uses Abstract Syntax Tree (AST) patterns to match code based on its structure rather than just text, enabling powerful and precise code search across large codebases.

## When to Use This Skill

Use this skill when users:
- Need to search for code patterns using structural matching (e.g., "find all async functions that don't have error handling")
- Want to locate specific language constructs (e.g., "find all function calls with specific parameters")
- Request searches that require understanding code structure rather than just text
- Ask to search for code with particular AST characteristics
- Need to perform complex code queries that traditional text search cannot handle

## General Workflow

Follow this process to help users write effective ast-grep rules:

### Step 1: Understand the Query

Clearly understand what the user wants to find. Ask clarifying questions if needed:
- What specific code pattern or structure are they looking for?
- Which programming language?
- Are there specific edge cases or variations to consider?
- What should be included or excluded from matches?

### Step 2: Create Example Code

Write a simple code snippet that represents what the user wants to match. Save this to a temporary file for testing.

**Example:**
If searching for "async functions that use await", create a test file:

```javascript
// test_example.js
async function example() {
  const result = await fetchData();
  return result;
}
```

### Step 3: Write the ast-grep Rule

Translate the pattern into an ast-grep rule. Start simple and add complexity as needed.

**Key principles:**
- Always use `stopBy: end` for relational rules (`inside`, `has`) to ensure search goes to the end of the direction
- Use `pattern` for simple structures
- Use `kind` with `has`/`inside` for complex structures
- Break complex queries into smaller sub-rules using `all`, `any`, or `not`

**Example rule file (test_rule.yml):**
```yaml
id: async-with-await
language: javascript
rule:
  kind: function_declaration
  has:
    pattern: await $EXPR
    stopBy: end
```

See `references/rule_reference.md` for comprehensive rule documentation.

### Step 4: Test the Rule

Use ast-grep CLI to verify the rule matches the example code. There are two main approaches:

**Option A: Test with inline rules (for quick iterations)**
```bash
echo "async function test() { await fetch(); }" | ast-grep scan --inline-rules "id: test
language: javascript
rule:
  kind: function_declaration
  has:
    pattern: await \$EXPR
    stopBy: end" --stdin
```

**Option B: Test with rule files (recommended for complex rules)**
```bash
ast-grep scan --rule test_rule.yml test_example.js
```

**Debugging if no matches:**
1. Simplify the rule (remove sub-rules)
2. Add `stopBy: end` to relational rules if not present
3. Use `--debug-query` to understand the AST structure (see below)
4. Check if `kind` values are correct for the language

### Step 5: Search the Codebase

Once the rule matches the example code correctly, search the actual codebase:

**For simple pattern searches:**
```bash
ast-grep run --pattern 'console.log($ARG)' --lang javascript /path/to/project
```

**For complex rule-based searches:**
```bash
ast-grep scan --rule my_rule.yml /path/to/project
```

**For inline rules (without creating files):**
```bash
ast-grep scan --inline-rules "id: my-rule
language: javascript
rule:
  pattern: \$PATTERN" /path/to/project
```

## ast-grep CLI quick reference

| Command | Use |
|---|---|
| `ast-grep run --pattern '<pat>' --lang <lang> <path>` | Simple single-node pattern search (add `--json` for structured output) |
| `ast-grep scan --rule <file>.yml <path>` | Complex YAML rule search (relational / composite logic) |
| `ast-grep scan --inline-rules "<yaml>" <path>` | Rule search without a rule file |
| `... scan --inline-rules "<yaml>" --stdin` | Test a rule against a code snippet (pipe via `echo`) |
| `ast-grep run --pattern '<pat>' --lang <lang> --debug-query=cst` | Dump the AST (`cst` / `ast` / `pattern`) to find `kind` values and debug non-matches |

**Always** add `stopBy: end` to relational rules (`has`/`inside`) for complete traversal.
In `--inline-rules`, escape metavariables (`\$VAR`) or use single quotes.

Full command catalog (every flag with examples), rule-writing tips, and common-use-case
recipes (functions-with-content, code-inside-context, code-missing-pattern):
`references/cli-cookbook.md`. Comprehensive rule syntax (atomic / relational / composite /
metavariables): `references/rule_reference.md`. Load these when detailed syntax is needed.

## Voice

- Report matches as locations, not prose: `path:line — <node kind>`, not "I found some functions".
- Verify a rule against an example snippet before searching the codebase; never report matches from an untested rule.
- Use hyphens (`-`), never double dashes (`--`).

## NEVER

- Never report matches from a rule that was not first verified against an example snippet (Step 4).
- Never use `ast-grep` for plain-text or filename matching — that is Grep's job.
- Never omit `stopBy: end` on a relational rule without a stated reason (silent partial traversal).
- Never run a structural rewrite (`ast-grep scan --rule ... --update-all`) without explicit user approval — this skill is search-only by default.
