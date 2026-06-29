# Devil - Worked Examples

Consumed by: `.claude/skills/devil/SKILL.md` Phase 2 as concrete templates for adversarial output formatting and honesty-floor behaviour.

Last Updated: 2026-06-10

Two worked examples covering the two output shapes: a clean N=M pass (Example A) and an honesty-floor M<N pass (Example B).

## Example A - /devil 3 against a 3-title LinkedIn list

User: `/devil 3` after the assistant produced three LinkedIn post titles.

Response:

```
Adversarial pass on: "three LinkedIn title options for a post on sovereign DPI"

## Point 1: [HIGH] Tired number

**Tired number.** "10x" in title 1 is the most worn-out word on LinkedIn
right now - operators have learned to skim past it.

## Point 2: [HIGH] Buried verb

**Buried verb.** Title 2 puts the action word in position seven, so
phone-skimming readers drop off before they reach the point.

## Point 3: [MEDIUM] One-note feed

**One-note feed.** All three titles open with the same product name -
the feed reads like a press kit, not a founder voice.

Reply with point numbers to address, or any other message to continue normally.
```

## Example B - honesty floor (M < N)

User: `/devil 5: We should sign PartnerCo as our exclusive [region] reseller before the expo.`

Response:

```
Adversarial pass on: "sign PartnerCo as exclusive [region] reseller before the expo"

## Point 1: [BLOCKER] Wrong stakeholder

**Wrong stakeholder.** Alex leads commercial conversations with PartnerCo;
locking in exclusivity without his agreement breaks the existing
relationship line and surfaces in the first PartnerCo call afterwards.

## Point 2: [HIGH] Premature exclusivity

**Premature exclusivity.** Exclusive resale before a closed deal removes
your second-mover leverage with Lars and Maya, and PartnerCo has no signed
revenue commitment yet to justify the trade.

/devil honesty floor: I could only produce 2 substantive points from
distinct angles. The remaining 3 would be strawmen or repetition.
Padding to N would defeat the purpose. If you want me to stretch
anyway, reply "force N" and I'll surface the weakest cuts with explicit
"weak: " prefixes.

Reply with point numbers to address, or any other message to continue normally.
```
