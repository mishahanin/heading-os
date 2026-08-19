---
paths:
  - "scripts/census.py"
  - "scripts/utils/sandbox.py"
  - "scripts/utils/census_schema.py"
---

# Executing Model-Written Code - the /census carve-out

Last Updated: 2026-08-13
Last Verified: 2026-08-13

Path-scoped rule. Loads when work touches the `/census` engine, its sandbox, or
its return schema. It states the one place in this workspace where generated
code is executed on purpose, what makes that acceptable, and the four conditions
that end the exception.

## The rule this carves out of

The global policy in `~/.claude/CLAUDE.md` forbids `eval()`, `exec()` and
`compile()` on input, with no exceptions listed. `/census` writes a Python
traversal program and runs it.

**Precisely which part collides, corrected 2026-08-13.** The census surface
contains no `eval`, no `exec` and no `compile` - the program runs through
`subprocess.run` with a fixed argv, which is the form the global policy
PRESCRIBES. So the letter of the ban is not breached. What is breached is its
intent: model-written code is executed on the operator's data. Stating the
collision as an `eval/exec` one was sloppy in a way that matters, because a
future author could cite this rule to justify a genuine `exec()`. It licenses
no such thing. Adding `eval`, `exec` or `compile` to any script, this one
included, remains forbidden outright.

The collision is written here rather than silenced at the linter, because a
`# nosec` with no rule behind it is read by whoever is turning a gate off, and a
rule is read by whoever is about to widen the hole.

## Why the global ban does not fit this case, and what it was really about

The ban addresses production code feeding untrusted input to an interpreter: a
web handler that `eval`s a query parameter, a loader that unpickles a payload.
The attacker supplies the code, and the process runs with the application's full
authority.

That is not this. Claude Code executes model-written Python and Bash in this
workspace every day, with the operator's own permissions, and the workspace is
built on that. So "a model runs Python" is not the new risk, and an argument
that stopped there would be arguing for a control the workspace already
declines to apply anywhere else.

## What the ACTUAL new risk is

One thing changes, and it is worth naming precisely because the earlier framing
in this project got it wrong twice.

Today, between a file being read and an action being taken, there is a human.
The model reads an email, shows it, and the next step meets a gate. In a
traversal loop the human is not between the iterations. Text goes into a
variable, code reads the text, the result shapes the next code, and none of that
passes a person. An instruction sitting inside a counterparty's email or a
web-clipped note reaches a self-writing execution loop instead of a reader.

The blast radius is the difference: that loop would otherwise run in a tree
holding `.env`, `.sessions` and the whole private data repository, with the
network up. Leg 2 of the lethal trifecta wired to an executor rather than to a
reader (`.claude/rules/lethal-trifecta.md`).

## The compensating control

Four properties, three protecting the child and one protecting the parent. All
four are code in `scripts/utils/sandbox.py` and `scripts/utils/census_schema.py`,
not conventions.

| Control | What it removes | Where |
|---|---|---|
| `--unshare-all` | any way to send what was read | empty network namespace |
| `--clearenv` | anything worth sending | empty environment, one explicit `PATH` |
| `--ro-bind` on the corpus | a planted file that outlives the run | read-only mount; a `--tmpfs` overlay blanks any air-gapped child |
| schema-validated return | an injected instruction riding home in prose | counts, paths and pairs; free text is opt-in per run and tagged `provenance: untrusted` |

The fourth is the one that is easy to underrate. The first three protect the
process in the box; the RESULT travels to a parent that has the network, the
credentials and the tools. An injection does not need to execute inside the
sandbox. It only needs to be quoted in the return and read by the orchestrator
afterwards. Because the questions are counts and paths, requiring exactly that
shape leaves free prose nowhere to sit.

Verified on this machine 2026-08-13 (WSL2, kernel 6.18.33.2, bubblewrap 0.9.0):
network unreachable with `Errno 101`, the engine's `.env` absent, corpus writes
refused with a read-only filesystem, and no parent environment variable crossing
the boundary. Held by `tests/test_census_sandbox.py` and
`tests/contract/2026-08-13-census-primitive/test_contract.py`.

One honest limit, measured rather than assumed: `bwrap` itself IS reachable
inside the box, because `/usr` must be bound for python to run at all. A nested
sandbox gains nothing - the empty network namespace is inherited and an
unprivileged nested bwrap cannot remount a read-only bind as writable - and that
is the claim the tests make. The claim that the binary is absent would be false.

## What VOIDS the exception

The carve-out covers a traversal that runs under all four controls. It does not
cover generated code in general. Any one of these ends it, and ending it means
the code must not ship, not that the rule needs an amendment:

1. **A run outside the sandbox.** Including a fallback for a machine without
   bubblewrap. A soft degradation here silently converts the design into "an
   agent runs generated Python next to `.env` with the network up", which is the
   configuration this whole arrangement exists to refuse. `bwrap` missing is a
   hard failure with its own exit code.
2. **A corpus mount that is not read-only**, or one that exposes an air-gapped
   path. The CEO-private thread branch and any `_secure/` prefix are absent from
   the box, not merely denied.
3. **A return that reaches the caller without passing the schema.** A
   best-effort pass-through of an unvalidated return is a breach, not a
   degradation.
4. **A network channel of any kind out of the box**, including a broker for
   model calls. This is why sub-model calls happen in the parent over paths the
   traversal returned, and why `socat` and a proxy runtime were rejected: their
   whole purpose is partial network, and the requirement here is none.

## Scope

This rule licenses `/census` and nothing else. A new caller of
`scripts/utils/sandbox.py` inherits the controls but not the argument: it needs
its own reason, written here, before it executes anything a model wrote. Adding
`eval`, `exec` or `compile` to any other script remains forbidden outright.
## Change control

Changes to this rule, or to any of the four controls it depends on, require
Misha's explicit approval.
