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

## The one exception: self-notification

A system notification to the operator's OWN sink is not a leg-3 send, so the six timer-driven notifiers and the checkpoint hook fire without a click. Leg 3 is reach to a third party; a message that can only arrive at the operator is the machine telling the human who already holds the data. Gating it would mean asking him to approve being told.

The boundary is mechanical, and its limit is stated here rather than left to be discovered. `scripts/utils/telegram_notify.py` resolves an allowlist of the operator's own sinks from the ENVIRONMENT (pinned by `HEADING_OS_SELF_TELEGRAM_TARGET`, else the per-feature `*_TELEGRAM_TARGET` set) and REFUSES any other recipient with a logged `REFUSED` line and no send. Absent, blank or unrecognised configuration refuses; it never falls back to sending somewhere.

What that closes: a recipient a caller can produce WITHOUT touching the environment, which is the shape a prompt-injected instruction actually reaches for. A literal in a caller, a value derived from fetched content, an argument handed in by a skill: all refused.

What it does NOT close, and this sentence used to claim it did. The read is `os.environ`, not the `.env` FILE, so a value the running process assigned to one of those names is indistinguishable from one the operator typed. MEASURED 2026-09-01: with the six names cleared, assigning `OPS_RADAR_TELEGRAM_TARGET` to a stranger and calling `notify()` returned True and reached the transport. Reading the file instead is a WORSE trade, not a better one: `tests/conftest.py` contains the whole suite by blanking those names in `os.environ`, so a file-reading resolver would let a test run message the operator, and a systemd unit passing the target via `Environment=` would go dark. An adversary who can assign to `os.environ` in this process can also call `TelegramBot` directly and skip this module, so the boundary buys nothing against that one. Do not "harden" it to a file read without settling both of those first. The reasoning lives in full at `own_targets()`.

Nothing may widen that transport to carry a recipient that arrived through the running process rather than from the operator's own configuration. That is a rule for whoever edits this next, not a property the code checks, and the paragraph above is why the two are not the same sentence. No other send path inherits this exception.

Advisory layers may inspect a queued draft and attach a second opinion (see the R5b pre-approval critique, `scripts/utils/draft_critique.py`), but an advisory layer can only annotate - it can never approve, dismiss, or send. The mandatory human click is the only path from draft to sent.
