# Lethal-Trifecta Control - Outbound Send Is Always Human-Gated

Last Updated: 2026-06-04
Last Verified: 2026-06-04

Always-active rule. States, in prose, the one control that every skill, script, and daemon in this workspace inherits: anything that can send to the outside world is ALWAYS gated behind an explicit human approval, and is NEVER sent autonomously. This rule is policy; the mechanical enforcement lives in `.claude/rules/tiered-risk.md` and `scripts/utils/tool_risk.py`. The two are siblings - the rule says what the control is and why; the invariant is its teeth.

## The lethal trifecta

An AI agent becomes dangerous to its principal when three capabilities are present in the same execution at the same time:

1. **Access to private data** - CRM contact files, the Odin brain, Exchange mail, Telegram history, the datastore, anything in the workspace a stranger should never see.
2. **Exposure to untrusted content** - inbound email, web pages, Telegram messages, any text authored by someone outside the Tribe that the agent reads and may be steered by (prompt injection lives here).
3. **The ability to send externally** - email via `scripts/send-email.py`, Telegram via the live client, any outbound message to a third party.

Any one or two legs alone is recoverable. All three together is the failure mode: untrusted content instructs the agent, the agent reaches private data, and the agent exfiltrates it through the send channel - with no human in the loop. The workspace handles private data and reads untrusted content constantly, so legs 1 and 2 cannot be removed without removing the assistant's usefulness. The mitigation 31C takes is therefore to keep **leg 3 permanently human-gated**: the agent may draft, queue, and recommend a send, but a human must click before anything leaves.

## The control (non-negotiable)

Every outbound send is drafted, never auto-sent. Across every surface:

- The Action Queue routes any send-capable card to the `gated` tier. Since 2026-06-27 the send is SYNCHRONOUS and terminal-native: the CEO's typed `scripts/action-queue.py approve <id>` (or `/queue approve`) IS the explicit human approve click and the send happens in that same command - there is no autonomous background send. `send_card` still refuses anything that does not resolve `gated`. The control is unchanged; only the click moved from a web page to the terminal.
- A skill or daemon that produces an outbound message produces a **draft** for review - it does not call the send transport itself as an autonomous step.
- New automation that gains a send capability inherits this control by default. If you add a new `action_type` that can send anything outbound, add it to `send_capable` in `config/tool-risk.json` so it floors at `gated`. Forgetting also fails safe: an unclassified type resolves `gated`.

Advisory layers may inspect a queued draft and attach a second opinion (see the R5b pre-approval critique, `scripts/utils/draft_critique.py`), but an advisory layer can only annotate - it can never approve, dismiss, or send. The mandatory human click is the only path from draft to sent.

## Mechanical enforcement (where the teeth are)

The control is not enforced by prose alone. `scripts/utils/tool_risk.tier_for()` resolves any `action_type` in the ledger's `send_capable` set to `gated` regardless of what its `tiers` entry claims, and resolves unknown types to `gated`. A `config/tool-risk.json` edited to mark `email_send` autonomous still resolves `gated`. `tests/test_tool_risk.py` and `tests/test_action_queue_tiers.py` assert that a tampered ledger cannot auto-send. The ledger is data; the send-gate is code. See `.claude/rules/tiered-risk.md` for the full tier model and the invariant.

## Classification

Corporate - the control applies to every workspace in the fleet, not only the CEO's. Shared via the `.claude/rules/` directory default.

## Change control

Changes to this rule, or any weakening of the `send_capable -> gated` invariant it depends on, require Misha's explicit approval.
