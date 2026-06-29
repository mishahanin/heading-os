# ast-grep — CLI Cookbook

Consumed by: `.claude/skills/ast-grep/SKILL.md`.
Last Updated: 2026-06-16

Detailed CLI command reference, rule-writing tips, and common-use-case recipes for
ast-grep. Kept out of the SKILL body so the inline workflow stays under the line budget.
Adapted from upstream [ast-grep/agent-skill](https://github.com/ast-grep/agent-skill).

## CLI commands

### Inspect code structure (`--debug-query`)

Dump the AST structure to understand how code is parsed:

```bash
ast-grep run --pattern 'async function example() { await fetch(); }' \
  --lang javascript \
  --debug-query=cst
```

**Available formats:**
- `cst`: Concrete Syntax Tree (shows all nodes including punctuation)
- `ast`: Abstract Syntax Tree (shows only named nodes)
- `pattern`: Shows how ast-grep interprets your pattern

**Use this to:** find the correct `kind` values for nodes; understand the structure of
code you want to match; debug why patterns aren't matching.

```bash
# See the structure of your target code
ast-grep run --pattern 'class User { constructor() {} }' \
  --lang javascript \
  --debug-query=cst

# See how ast-grep interprets your pattern
ast-grep run --pattern 'class $NAME { $$$BODY }' \
  --lang javascript \
  --debug-query=pattern
```

### Test rules (`scan --stdin`)

Test a rule against a code snippet without creating files:

```bash
echo "const x = await fetch();" | ast-grep scan --inline-rules "id: test
language: javascript
rule:
  pattern: await \$EXPR" --stdin
```

Add `--json` for structured output:

```bash
echo "const x = await fetch();" | ast-grep scan --inline-rules "..." --stdin --json
```

### Search with patterns (`run`)

Simple pattern-based search for single AST node matches:

```bash
# Basic pattern search
ast-grep run --pattern 'console.log($ARG)' --lang javascript .

# Search specific files
ast-grep run --pattern 'class $NAME' --lang python /path/to/project

# JSON output for programmatic use
ast-grep run --pattern 'function $NAME($$$)' --lang javascript --json .
```

**When to use:** simple, single-node matches; quick searches without complex logic; when
you don't need relational rules (inside/has).

### Search with rules (`scan`)

YAML rule-based search for complex structural queries:

```bash
# With rule file
ast-grep scan --rule my_rule.yml /path/to/project

# With inline rules
ast-grep scan --inline-rules "id: find-async
language: javascript
rule:
  kind: function_declaration
  has:
    pattern: await \$EXPR
    stopBy: end" /path/to/project

# JSON output
ast-grep scan --rule my_rule.yml --json /path/to/project
```

**When to use:** complex structural searches; relational rules (inside, has, precedes,
follows); composite logic (all, any, not); when you need the power of full YAML rules.

**Tip:** for relational rules (inside/has), always add `stopBy: end` to ensure complete
traversal.

## Tips for writing effective rules

### Always use `stopBy: end`

For relational rules, always use `stopBy: end` unless there's a specific reason not to:

```yaml
has:
  pattern: await $EXPR
  stopBy: end
```

This ensures the search traverses the entire subtree rather than stopping at the first
non-matching node.

### Start simple, then add complexity

1. Try a `pattern` first.
2. If that doesn't work, try `kind` to match the node type.
3. Add relational rules (`has`, `inside`) as needed.
4. Combine with composite rules (`all`, `any`, `not`) for complex logic.

### Use the right rule type

- **Pattern:** simple, direct code matching (e.g., `console.log($ARG)`).
- **Kind + Relational:** complex structures (e.g., "function containing await").
- **Composite:** logical combinations (e.g., "function with await but not in try-catch").

### Debug with AST inspection

When rules don't match: use `--debug-query=cst` to see the actual AST structure; check if
metavariables are being detected correctly; verify the node `kind` matches expectation;
ensure relational rules search in the right direction.

### Escaping in inline rules

When using `--inline-rules`, escape metavariables in shell commands:
- Use `\$VAR` instead of `$VAR` (shell interprets `$` as a variable).
- Or use single quotes: `'$VAR'` works in most shells.

```bash
# Correct: escaped $
ast-grep scan --inline-rules "rule: {pattern: 'console.log(\$ARG)'}" .

# Or use single quotes
ast-grep scan --inline-rules 'rule: {pattern: "console.log($ARG)"}' .
```

## Common use cases

### Find functions with specific content

Async functions that use await:

```bash
ast-grep scan --inline-rules "id: async-await
language: javascript
rule:
  all:
    - kind: function_declaration
    - has:
        pattern: await \$EXPR
        stopBy: end" /path/to/project
```

### Find code inside specific contexts

`console.log` inside class methods:

```bash
ast-grep scan --inline-rules "id: console-in-class
language: javascript
rule:
  pattern: console.log(\$\$\$)
  inside:
    kind: method_definition
    stopBy: end" /path/to/project
```

### Find code missing expected patterns

Async functions without try-catch:

```bash
ast-grep scan --inline-rules "id: async-no-trycatch
language: javascript
rule:
  all:
    - kind: function_declaration
    - has:
        pattern: await \$EXPR
        stopBy: end
    - not:
        has:
          pattern: try { \$\$\$ } catch (\$E) { \$\$\$ }
          stopBy: end" /path/to/project
```
