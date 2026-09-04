# Skill Router — rationale, worked examples, and what left the rule

Consumed by: `.claude/rules/skill-router.md`.

Last Updated: 2026-09-04

The router rule is always-on, so every byte in it is paid on every session before
the operator has typed anything. This file holds the parts of it that a reader
needs only AFTER they have already decided to think about routing: the reasoning
behind the protocol, the worked examples, and the record of what moved out and
why. Nothing here is an obligation the rule does not already state.

## Why compound triggers are checked before individual skills

A compound pattern involves several skills, and the operator gets the answer
faster when they run in parallel. "Check what's new" should reach Morning Comms
(email plus viraid, dispatched together), not `/email-intel` alone. Falling
through to individual matching after a compound trigger has matched turns a
parallel sweep into a serial one and drops half the surfaces.

## Why a false positive costs more than a missed match

A missed match costs one clarifying exchange. A wrong match spends a skill's full
execution on the wrong job, and the operator has to notice it went wrong before
they can say so. So the rule is to leave a request in ordinary conversation
rather than force it into the nearest skill.

## Why the resident index carries triggers at all

The harness ships every skill's `description` in its own skill listing, and those
descriptions already contain trigger phrases, so a resident trigger table looks
redundant. It is not, and the reason is measured.

Claude Code budgets that listing at `skillListingBudgetFraction` of the model's
context window (this workspace sets `0.03` in `.claude/settings.json`) and, on
overflow, silently drops DESCRIPTIONS rather than skills, starting with the
least-invoked. Measured 2026-09-04: the 94 skills carry 46,459 bytes of
description. On a 1M-token session 0.03 buys roughly 30,000 characters; on a
200K-token session it buys roughly 6,000. So on any smaller-window session most
of the corpus is silently mute, and the only thing left that can match a message
to `/cold-sweep` is the trigger cell in the resident index.

That is why the registry's Triggers column stayed always-on through the
2026-09-04 context diet while almost everything else in the rule moved. See the
auto-memory note `skill-listing-budget-silently-drops-descriptions`.

## Why explicit-only skills carry no resident trigger text

A `router: manual` skill cannot be reached by matching a natural-language
message, so the resident registry owes it no trigger text. Before 2026-09-04 the
23 manual skills spent 3,868 bytes of the always-on core index on trigger cells
whose content was prose explaining that they must never be matched — `/checkpoint`
alone spent 1,072 to describe three session switches — inside a rule whose entire
job is matching.

What actually stops them being matched is not that prose. 22 of the 23 carry
`disable-model-invocation: true`, which the harness enforces: the model cannot
invoke them from natural language at all. The 23rd is `/brain-audit`, which omits
the flag deliberately so that composing skills can reach it through the Skill
tool, and which is invoked by a skill rather than by a user message either way.

So each manual skill keeps its ROW and loses its trigger CELL, which now reads
"Explicit invocation only; never auto-routed." The row has to stay: an operator
who types `/backup` should find the command listed, and three separate gates read
the registry through that row shape —
`tests/test_skill_graph_covers_the_router.py` asserts two-way set equality
between the `` | `/name` `` rows and `reference/skill-graph.csv`,
`tests/test_three_flag_lists_that_described_one_skill.py` reads `/scrutinize`'s
five flags out of its label, and `workspace-health.py::check_skill_router_coverage`
requires every skill directory to be named in the rule. The reasons, flags and
argument grammar survive in full in `reference/skill-router/<category>.md`.

## Why triggers that repeat the skill's own name are dropped

`/viraid` listing "viraid", `/mullvad` listing "/mullvad", `/telegram` listing
"telegram": the Skill cell of the same row already spells it, so the duplicate
buys no match. `core_triggers()` in `scripts/generate-skill-router.py` drops a
trigger whose normalised form EXACTLY equals the skill's name or label.

The exactness is deliberate and was chosen against a measurement. Dropping every
trigger that merely CONTAINS the name saved 1,587 bytes on 2026-09-04 by deleting
real matching surface: `/thread` fell from 8 triggers to 1, losing "open a
thread", "close thread", "thread list" and "thread find"; `/design` kept only
"design social" while "design infographic", "design mockup" and "design logo"
went. A containment rule cannot tell a redundant echo from a compound phrase
built on the name. The exact rule saves less and loses nothing.

## Plugin-namespaced skills — why they are never auto-routed

Plugin content evolves independently of this workspace, so a local keyword guess
routes against a purpose that may have drifted since the roster was written.

Which plugins are enabled, which are deliberately off, and what each costs per
turn: `reference/plugin-roster.md`. Verify that file with
`python scripts/harness-audit.py`, never by reading the list.

## What moved out of the rule

**2026-08-20, four sections.** None was routing logic.

- Plugin roster (installed / enabled / disabled and why) → `reference/plugin-roster.md`
- Scheduled and background tasks → `reference/scheduled-tasks.md`
- Trigger regression tests → `docs/EXTENDING.md`, under writing a skill; it is
  authoring guidance, never routing
- Archived skills convention → `docs/EXTENDING.md`

**2026-09-04, the context diet.** The rule went from 18,812 bytes to roughly
9,500. Three changes, in descending size: the manual skills' trigger cells
became a list of names (above); the name-echo triggers were dropped (above); and
the protocol prose was compressed, with its rationale and worked examples moved
into this file. The registry's auto-routable trigger cells were deliberately NOT
cut, for the budget reason recorded above.
