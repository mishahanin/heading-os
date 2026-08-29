---
name: radar
description: Manual-actions + silent-health detector. Runs scripts/ops-radar.py, which surfaces which high-consequence MANUAL actions are objectively overdue (backup, publish-to-fleet, weekly-review, cold-sweep, Odin collect/reflect) and which machine-domain states have degraded (ollama down, memory-index stale), self-healing the latter (Tier A) and nudging only on exception for the former (Tier B), counts-only. Use when the user says "radar", "ops radar", "what's overdue", "what do I need to run", "what am I forgetting", or wants to ack/silence a known item or toggle crunch-mode. Do NOT use for a full morning brief (use /dashboard or /prime), for naming the single next action (use /next), or for function-by-function operational health (use /state-check) - the radar FEEDS those, it does not replace them.
argument-hint: "[ack <key> | crunch on|off]"
allowed-tools: "Read, Bash(python3:*)"
model: haiku
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-heading-orchestration:
  parallel_safe: true
  shared_state:
    - "outputs/operations/ops-radar/"
  triggers:
    - radar
    - ops radar
    - what's overdue
    - what am I forgetting
    - ack this radar item
    - crunch mode on
x-heading-capability:
  what: >
    Deterministic detector of overdue sovereign manual actions (backup, publish,
    weekly-review, cold-sweep, Odin) and degraded machine-health (ollama, the
    accelerated ollama host, memory-index). Tier A auto-heals silently; Tier B
    nudges only when objectively overdue, counts-only. Never auto-executes a
    manual action.
  how: >
    Run /radar for the detailed due-items view; /radar ack <key> silences one
    signal until its TTL or it worsens; /radar crunch on|off toggles the
    critical-floor-only posture. A daily timer pushes the same counts-only line to
    Telegram; /prime renders the same panel.
  when: >
    Use to see what is overdue or to ack/crunch. For a full morning brief use
    /dashboard or /prime; for the single next action use /next; for function
    health use /state-check.
x-heading-routing:
  category: Operations
  triggers:
    - radar
    - ops radar
    - what's overdue
    - what am I forgetting to run
    - what manual actions are due
    - ack a radar item
    - crunch mode on/off
  exclusions:
    - Morning brief -> /dashboard or /prime
    - single next action -> /next
    - function-by-function health -> /state-check. Detector that FEEDS those
    - never executes a manual action. CEO-only data, fleet-safe code.
  compound: 'No'
  router: auto
---
# Ops-Radar

Surface what is objectively overdue and what silently degraded. This skill is the chat surface over `scripts/ops-radar.py`. It is a DETECTOR, not an executor: it never sends, commits, publishes, or runs a manual action for you. Outbound sends stay human-gated.

## Phase 0 - Route the request

- Bare "radar" / "what's overdue" / "what am I forgetting" -> **run the detector** (Phase 1).
- "ack <key>" / "silence backup" -> **ack** (Phase 2).
- "crunch on" / "crunch off" -> **crunch** (Phase 3).

## Phase 1 - Run the detector

```bash
python scripts/ops-radar.py
```

Read-only. It computes every signal, applies ack + crunch suppression, and prints the detailed due-items view (or "all clear"). It does NOT auto-heal in this mode (heal runs on the timer). Present the result plainly, grouped by severity, and for each due Tier-B item name the one command that clears it:

- `backup` -> `/backup`
- `publish` -> `/push-updates`
- `weekly_review` -> `/weekly-review`
- `cold_sweep` -> `/cold-sweep`
- `odin_cadence` -> `/odin collect` or `/odin reflect`
- `ollama_accel` -> the accelerated ollama host does not answer. This machine cannot restart it, because it runs outside this OS. Work continues on the local daemon, more slowly. Tell the operator to restart it on its own host.
- a critical `*_autoheal` line -> machine auto-heal has FAILED repeatedly; surface it, do not try to fix it inline.

State the posture: "N item(s) due" or "all clear". Never invent an item the script did not report.

## Phase 2 - Ack (silence a known item)

```bash
python scripts/ops-radar.py ack <key> [--ttl 24h|7d]
```

Silences that one signal until the TTL expires OR its severity band worsens (worsening always re-surfaces it). Confirm what was acked and for how long. Valid keys: `backup`, `publish`, `weekly_review`, `cold_sweep`, `odin_cadence`, `ollama`, `ollama_accel`, `memory_index`, `router_accuracy`, `queue`.

## Phase 3 - Crunch-mode

```bash
python scripts/ops-radar.py crunch on    # suppress all Tier-B except the critical floor
python scripts/ops-radar.py crunch off   # normal posture
```

`crunch on` suppresses every Tier-B nudge EXCEPT the critical floor that always pierces: imminent data-loss (backup debt beyond the hard band) and any auto-heal failure. Confirm the new posture. This honours "Deliver Under Pressure" without letting a crunch hide a true emergency.

## NEVER

- NEVER send, commit, push, publish, or run a manual action on the CEO's behalf - the radar only detects; the CEO runs the named command.
- NEVER put operational CONTENT on the Telegram wire. The notify path sends the
  `--quiet` line and nothing else, and every signal summary in it is a fixed
  label plus numbers (`cold-sweep: 7 red-debt contacts`). No contact name, no
  subject, no quoted text, no path. Never the detailed view and never the JSON.
  Pinned by `tests/test_a_radar_that_refused_to_silence_its_own_alarm.py`.
- NEVER auto-clear or auto-ack a signal the CEO did not ack - suppression is the CEO's call.
- NEVER claim an item is due that `ops-radar.py` did not report, or hide one it did.
