---
paths:
  - "scripts/**"
  - "config/tool-risk.json"
---

# Tiered Risk Gate (R3)

Last Updated: 2026-06-04
Last Verified: 2026-06-04

Path-scoped rule. Loads when work touches the Action Queue executor, the risk ledger, or the daemon job that routes cards. Sibling to `trace-id.md`. Governs how the Action Queue decides friction per action, and the one invariant that must never be weakened.

## The model

Every Action Queue card carries an `action_type`. The reversibility ledger `config/tool-risk.json` maps each `action_type` to one of three tiers, resolved by `scripts/utils/tool_risk.tier_for()`:

| Tier | Meaning | Flow |
|---|---|---|
| `autonomous` | No-op / read-only / display-only (`note`, `alert`) | Surfaced read-only; no CEO click; never auto-sends anything. |
| `notify` | Reversible state edit (`pipeline_update`) | Auto-applied by the daemon, with a one-click undo via the disposition log (`undo_card`). The producer stamps `prev_value` before applying. |
| `gated` | Irreversible outbound send (`email_send`, any `telegram_send`) | Hard review gate: the executor sends only after an explicit CEO approve click. **Unchanged. This is the lethal-trifecta control.** |

The tier is stamped on the card at append time (`append_cards`) and the daemon's `_sweep_non_gated_cards` routes by it: autonomous display types stay surfaced for the CEO to read and dismiss, notify cards auto-apply, gated sends wait for approval.

> **`telegram_send` is reserved-and-gated but not yet wired.** It is pre-registered in `send_capable` (floors to `gated`) and shown in the UI, but `scripts/action-queue-execute.py` has no telegram executor branch — an approved telegram card cannot actually fire. This is the safe direction (a gated send that cannot send), but do not assume approved telegram cards deliver until the executor branch lands.

## The invariant (non-negotiable)

**The ledger is data; the send-gate is code.** Any `action_type` listed in the ledger's `send_capable` set resolves to `gated` no matter what its `tiers` entry says. A `tool-risk.json` edited to mark `email_send` autonomous still resolves `gated`. Unknown or missing types also resolve `gated` (safe default, matching the workspace "missing metadata → friction-maximal" convention).

This makes the lethal-trifecta control impossible to defeat by editing a config file. `tests/test_tool_risk.py` and `tests/test_action_queue_tiers.py` assert that a tampered ledger cannot auto-send. The ledger can *raise* friction freely; it can never *lower* a send below `gated`.

When adding a new `action_type`:
- If it can send anything outbound, add it to `send_capable` (it floors at `gated`).
- Otherwise add a `tiers` entry; if you forget, it defaults to `gated` until classified.
- Never remove an entry from `send_capable`. Changes to the invariant require CEO approval.

## Classification

This rule is corporate — the tier taxonomy and the send invariant apply to every workspace's executor. The fleet-safe primitives (`tool_risk.py`, `config/tool-risk.json`) are corporate. The CEO-only spine pieces that consume the gate during prove-out (the alert router, the watchdog, the dead-letter CLI) are pinned private in `config/routing-map.yaml` and are not synced to executives.

## Change control

Changes to the `send_capable` invariant or this rule require Misha's explicit approval.
