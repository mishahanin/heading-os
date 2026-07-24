---
name: modem-tune
description: "Change the reported IMEI on a configured GL.iNet travel router (GL-XE300 or GL-E5800, auto-detected) for work testing. Generates a fresh, never-reused device-class IMEI locally (TAC from per-device config), connects to the modem over SSH, records the outgoing IMEI with a timestamp, applies the change, confirms the device AND the reset with the operator, resets the modem, and verifies the new IMEI is live. EXPLICIT INVOCATION ONLY via /modem-tune. Personal-hardware tool: dormant on any instance without its own private config/modem.json (per-device identity). Subcommand-style requests: status (read-only), revert (factory IMEI)."
argument-hint: "[status | revert]"
allowed-tools: "Bash(python3:*), Read, AskUserQuestion"
disable-model-invocation: true
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-heading-orchestration:
  parallel_safe: false
  shared_state: ["outputs/operations/reference/modem-imei-ledger.json"]
  triggers: []
x-heading-capability:
  what: >
    Changes the reported IMEI on a GL.iNet travel router (GL-XE300 or GL-E5800,
    auto-detected from the live modem) over SSH - generates a fresh never-reused
    value, stages it, and after two separate confirmations (device, then reset)
    resets and verifies it live. Personal-hardware tool; dormant without a
    private per-device config/modem.json on the running instance.
  how: >
    Explicit invocation only - run /modem-tune (full rotation), /modem-tune
    status (read-only), or /modem-tune revert (factory IMEI). Device is
    auto-detected and must be confirmed before any change; a hard confirmation
    gate also guards the reset. Never auto-triggers.
  when: >
    Use to rotate or check the IMEI on either travel router for work testing.
    There is no alternative skill - this is the sole owner of the IMEI ledger.
x-heading-routing:
  category: Operations
  triggers:
    - NEVER auto-trigger. Explicit `/modem-tune [status \| revert]` only. Changes the reported IMEI on a GL.iNet travel router (GL-XE300 or GL-E5800) over SSH. CEO-only
    - never synced to executives.
  exclusions:
    - 'All natural language (`disable-model-invocation: true`)'
  compound: 'No'
  router: manual
---

# Modem Tune -- IMEI Reconfiguration

Automates changing the reported IMEI on either of two personal GL.iNet travel routers,
auto-detected from the live modem: the GL-XE300 (Quectel EG25-G, `gl_modem AT` over
SSH) and the GL-E5800 "Mudi 7" (Quectel RG650V-EU, ubus `modem.CPU.AT` over SSH). All
mechanics live in `scripts/modem-tune.py`; this skill is the conversational wrapper
that owns the device-confirm gate and the reset-confirm gate.

Spec: `docs/superpowers/specs/2026-05-30-modem-tune-skill-design.md` (data overlay: `.heading-os-data/docs/superpowers/specs/2026-05-30-modem-tune-skill-design.md`).
Device + procedure reference: `outputs/operations/reference/gl-inet-mobile-router-imei-reconfig.md`.

## Modes

- **Default / full rotation** (`/modem-tune`): detect -> confirm device -> status ->
  generate -> apply -> confirm reset -> reset -> verify.
- **`/modem-tune status`**: detect -> confirm device -> read the live IMEI(s), SIM,
  network, and signal. No change.
- **`/modem-tune revert`**: detect -> confirm device -> restore the factory IMEI (from
  `config/modem.json`), then confirm reset + reset + verify.

Every mode accepts an explicit `--device {xe300,e5800}`, which skips auto-detection.

## Pre-flight

Both routers are reached over SSH with credentials from `.env` (`MODEM_HOST`,
`MODEM_USER`, `MODEM_SSH_PASSWORD` -- unchanged, shared across devices). No VPN
pre-flight applies -- these are local authenticated devices, not public web services.
`config/modem.json` is now per-device (one entry per router: transport, host, TAC,
factory IMEI); an unconfigured device exits cleanly (exit 2) on `generate`/`apply`/
`revert`, not on `detect`/`status`.

If `MODEM_SSH_PASSWORD` is missing the engine exits with a clear error; tell the CEO to
add the `MODEM_*` block to `.env` and stop.

## Phase 0 -- Detect (always run first)

Run `python3 scripts/modem-tune.py detect` (or `detect --device <xe300|e5800>` if the
CEO already named the device). Read-only; identifies the connected modem from its
live model string.

If detection is ambiguous (the engine cannot classify the modem model), it exits
non-zero -- do not guess. Ask the CEO for an explicit `--device` and re-run.

## Phase 0.5 -- Confirm device (HARD STOP)

Show the CEO the resolved device id and the modem model string the engine printed.
Use AskUserQuestion to get an explicit "yes, that is the right device" before
proceeding to any further phase -- wrong-device confirmation would stage an IMEI
change against the wrong hardware.

Only an explicit yes proceeds. Silence or ambiguity means WAIT. From here on, pass
`--device <resolved>` explicitly to every subcommand so a stale auto-detect can never
silently re-target mid-flow.

## Phase 1 -- Status

Run `python3 scripts/modem-tune.py status --device <resolved>`. Show the CEO the live
IMEI(s), Luhn validity, SIM state, operator, and signal. The E5800 is dual-SIM and
prints both slot IMEIs; the XE300 prints one. This confirms the router is reachable
before anything is changed.

For `/modem-tune status`, stop here and report.

## Phase 2 -- Generate

Run `python3 scripts/modem-tune.py generate --device <resolved>`. Capture the proposed
IMEI (stdout is the bare 15-digit value; the stderr line states it is a valid, unique
device-class value). Present `old -> new` to the CEO.

For `/modem-tune revert`, skip generation; the target is the fixed factory IMEI.

## Phase 3 -- Apply (stages the change, no reset yet)

Run `python3 scripts/modem-tune.py apply --device <resolved> --imei <NEW>` (or `revert`
for the factory value). The engine records the outgoing IMEI to the ledger history with
a timestamp BEFORE sending `AT+EGMR`, then sends it and expects `OK`. The change is
staged but not live until a reset. On the E5800 (dual-SIM), `apply`/`revert` act on the
PRIMARY (slot 1) IMEI only -- slot 2 is never touched.

If the engine reports the command did not return `OK`, stop and surface the raw output.
Do not reset.

## Phase 4 -- Confirmation gate (HARD STOP)

Use AskUserQuestion to confirm the reset. State plainly what happens: a full router
reboot -- the only reset path now (~2-3 min of downtime, SSH and internet drop and
return).

Only an explicit yes proceeds. Silence or ambiguity means WAIT. Never reset without
this confirmation -- it is the one irreversible-feeling step.

## Phase 5 -- Reset + verify

On confirmation:

1. `python3 scripts/modem-tune.py reset --device <resolved>` (full router reboot).
2. `python3 scripts/modem-tune.py verify --device <resolved> --expect <NEW>`.

If verify still fails after the reboot, report the live value the modem reports and
stop -- do not loop indefinitely.

On success the engine has already marked the ledger `current` as verified. Report the
final live IMEI and its Luhn validity to the CEO.

## Output to the CEO

Close with a short summary: old IMEI -> new IMEI, ledger updated, verified live. No
hidden characters, plain prose.

## NEVER

- NEVER proceed past Phase 0.5 without an explicit device confirmation, and NEVER
  reset or reboot without the Phase 4 confirmation.
- NEVER run `revert` (factory IMEI) or any IMEI rollback unless the CEO explicitly
  asks for it in that turn. Do not offer it as an automatic recovery step.
- NEVER reuse an IMEI -- the engine enforces this via the shared ledger `used[]`; do
  not hand-pick a value that bypasses it (except the deliberate factory `revert`).
- NEVER change the slot-2 IMEI (out of scope / unvalidated on this firmware) -- act
  on the primary (slot 1) only, on either device.
- NEVER write the SSH password into any tracked file, commit message, or output. It
  lives only in `.env`.
- NEVER document this skill, the routers, IMEI values, or credentials in corporate or
  executive-facing files (`reference/workspace-overview.md`, `templates/`, corporate repo).
- NEVER scrape an external IMEI generator -- generation is local and deterministic.
- NEVER save router IMEI values or credentials to auto-memory.
