# Spec Core — the five-field deliverable kernel

The immutable core every plan or proposal resolves against: **Why / Capabilities / Constraints / Non-Goals / Success Signal**. Ported from BMAD-Method `bmad-spec` (v6.8.0), adapted to a CEO operations workspace.

Last Updated: 2026-06-04
Consumed by: `/create-plan` (plan template), `/proposal` (drafting discipline). Future adopters cite this file in one line rather than restating the contract.
Classification: corporate.

## The load-bearing test

> A claim is load-bearing if any consumer — the person or skill who acts on this, or the verification pass that checks it — would change a decision without it.

This is the single gate. A statement enters the core only if it passes. Everything that does not is wrapper ceremony (boilerplate, throat-clearing, restated context) and stays out. When a field starts bulleting into sub-bullets, the content has outgrown the kernel — move it to a named companion section the kernel cites, not into the kernel itself.

## The five fields

1. **Why** — one paragraph naming the force behind this work: a pain to solve, an opportunity to capture, a vision to realize, or a mandate to meet (regulation, deadline, contractual obligation). Name which applies, who is affected, and the backdrop that makes it matter now. This is the anchor every downstream trade-off resolves against.

2. **Capabilities** — a list, each entry an `id` + `intent` + `success` triple:
   - `id: CAP-N` — stable, unique, never reused or renumbered.
   - `intent:` — one sentence, "X can do Y to achieve Z." WHAT, not HOW.
   - `success:` — a criterion a test or a real demonstration can decide.
   - Missing either intent or success means it is not a capability.

3. **Constraints** — non-negotiables that bend a design decision. If a constraint rules nothing out, it is decoration — drop it.

4. **Non-Goals** — explicit out-of-scope items. **At least one is mandatory.** Absence means a downstream skill (or reader) fills the vacuum with its own assumption.

5. **Success Signal** — one or two sentences describing the world-change moment, not a dashboard. Concrete enough to write a test or run a demonstration against. "Users love it" or "the deal closes faster" do not qualify; "classification accuracy ≥ 95% on N live flows by day 47" does.

Two optional sections, omitted entirely when empty:
- **Assumptions** — inferred calls made without direct confirmation ("assumed the configured timezone since no zone was given").
- **Open Questions** — gaps that need a human decision before the work proceeds, phrased so a human can answer.

## Skeleton (copy into a plan's Spec Core block or a proposal's drafting notes)

```
Why: <one paragraph; the force + who + why now>

Capabilities:
- CAP-1 | intent: <what, not how> | success: <testable/demonstrable criterion>

Constraints:
- <a non-negotiable that bends a decision>

Non-Goals:
- <at least one explicit out-of-scope item>

Success Signal: <one testable world-change moment>
```

## Worked example (a deal-pursuit deliverable)

```
Why: ExampleTelco's Q2 sovereign-DPI tender opens a window to land a reference operator in
the target region before two competitors qualify; missing it pushes the regional anchor to 2027.

Capabilities:
- CAP-1 | intent: 31C demonstrates line-rate encrypted-traffic classification on the
  operator's own pcap set | success: a witnessed PoV run on >=10M flows with a signed
  accuracy sheet.
- CAP-2 | intent: the operator can self-serve a sovereignty audit of the deployment |
  success: the audit checklist runs to completion with zero external data egress.

Constraints:
- All processing stays on operator soil (no cloud callback) — rules out any SaaS framing.
- PoV must run inside the existing 6-week tender window.

Non-Goals:
- Not proposing a managed-service operating model in this engagement.
- Not committing to non-DPI modules (TrustONE) in this phase.

Success Signal: The operator's CTO signs the PoV accuracy sheet and names 31C in the
shortlist memo by day 47.
```

## Rules of use

- Non-Goals must list at least one item. A plan or proposal with zero Non-Goals is not done.
- Success Signal must be a single testable observable, not an aspiration.
- The block is a tight abstract — the surrounding plan/proposal sections expand on it; they do not contradict it.
- This skill distills; it does not coach. If the input is too thin to fill the five fields honestly, stop and ask (or route to `/deep-think` / `/create-plan`) rather than fabricate. A missing field flagged as an Open Question beats an invented one.
