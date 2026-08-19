# Lethal-Trifecta Control - Outbound Send Is Always Human-Gated

Last Updated: 2026-08-20
Last Verified: 2026-08-20

Always-active security rule. An agent that holds private data (leg 1), reads untrusted content (leg 2), and can send externally (leg 3) can be steered into exfiltrating the first through the third, so this workspace keeps leg 3 permanently human-gated: anything that can send to the outside world is ALWAYS gated behind an explicit human approval, and is NEVER sent autonomously.

Threat model, and why legs 1 and 2 cannot be removed: `docs/SECURITY-MODEL.md` § 1. Mechanical enforcement - the `send_capable -> gated` floor, the unclassified-type fail-safe, and the tests that hold them: `.claude/rules/tiered-risk.md` and `scripts/utils/tool_risk.py`.

## The control (non-negotiable)

Every outbound send is drafted, never auto-sent. Across every surface:

- The Action Queue routes any send-capable card to the `gated` tier. Since 2026-06-27 the send is SYNCHRONOUS and terminal-native: the CEO's typed `scripts/action-queue.py approve <id>` (or `/queue approve`) IS the explicit human approve click and the send happens in that same command - there is no autonomous background send. `send_card` still refuses anything that does not resolve `gated`. The control is unchanged; only the click moved from a web page to the terminal.
- A skill or daemon that produces an outbound message produces a **draft** for review - it does not call the send transport itself as an autonomous step.
- New automation that gains a send capability inherits this control by default. If you add a new `action_type` that can send anything outbound, add it to `send_capable` in `config/tool-risk.json` so it floors at `gated`. Forgetting also fails safe: an unclassified type resolves `gated`.

Advisory layers may inspect a queued draft and attach a second opinion (see the R5b pre-approval critique, `scripts/utils/draft_critique.py`), but an advisory layer can only annotate - it can never approve, dismiss, or send. The mandatory human click is the only path from draft to sent.
