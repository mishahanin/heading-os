---
name: modem-tune
description: "Change the reported IMEI on the configured GL.iNet GL-XE300 travel router for work testing. Generates a fresh, never-reused device-class IMEI locally (TAC from config), connects to the modem over SSH, records the outgoing IMEI with a timestamp, applies the change, confirms with the operator, resets the modem, and verifies the new IMEI is live. EXPLICIT INVOCATION ONLY via /modem-tune. Personal-hardware tool: dormant on any instance without its own private config/modem.json (device identity). Subcommand-style requests: status (read-only), revert (factory IMEI)."
argument-hint: "[status | revert]"
allowed-tools: "Bash(python3:*), Read, AskUserQuestion"
disable-model-invocation: true
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: false
  shared_state: ["outputs/operations/reference/modem-imei-ledger.json"]
  triggers: []
x-31c-capability:
  what: >
    Changes the reported IMEI on the GL.iNet GL-XE300 travel router over SSH -
    generates a fresh never-reused value, stages it, and after confirmation
    resets and verifies it live. Personal-hardware tool; dormant without a
    private config/modem.json on the running instance.
  how: >
    Explicit invocation only - run /modem-tune (full rotation), /modem-tune
    status (read-only), or /modem-tune revert (factory IMEI). A hard
    confirmation gate guards the reset; never auto-triggers.
  when: >
    Use to rotate or check the router IMEI for work testing. There is no
    alternative skill - this is the sole owner of the IMEI ledger.
---

# Modem Tune -- IMEI Reconfiguration

Automates changing the reported IMEI on the GL.iNet GL-XE300 travel router (Quectel
EG25-G modem) over SSH. Personal-hardware tool. All mechanics live in
`scripts/modem-tune.py`; this skill is the conversational wrapper that owns the
confirmation gate before any reset.

Spec: `docs/superpowers/specs/2026-05-30-modem-tune-skill-design.md` (data overlay: `.heading-os-data/docs/superpowers/specs/2026-05-30-modem-tune-skill-design.md`).
Device + procedure reference: `outputs/operations/reference/gl-inet-mobile-router-imei-reconfig.md`.

## Modes

- **Default / full rotation** (`/modem-tune`): generate -> apply -> confirm -> reset -> verify.
- **`/modem-tune status`**: read the live IMEI, SIM, network, and signal. No change.
- **`/modem-tune revert`**: restore the factory IMEI (from `config/modem.json`), then confirm + reset + verify.

## Pre-flight

The router is on the LAN (`192.168.8.1`) and reached over SSH with credentials from
`.env` (`MODEM_HOST`, `MODEM_USER`, `MODEM_SSH_PASSWORD`). No VPN pre-flight applies --
this is a local authenticated device, not a public web service.

If `MODEM_SSH_PASSWORD` is missing the engine exits with a clear error; tell the CEO to
add the `MODEM_*` block to `.env` and stop.

## Phase 0 -- Status (always run first)

Run `python3 scripts/modem-tune.py status`. Show the CEO the live IMEI, its Luhn
validity, SIM state, operator, and signal. This confirms the router is reachable before
anything is changed.

For `/modem-tune status`, stop here and report.

## Phase 1 -- Generate

Run `python3 scripts/modem-tune.py generate`. Capture the proposed IMEI (stdout is the
bare 15-digit value; the stderr line states it is a valid, unique iPhone 13 Pro Max
value). Present `old -> new` to the CEO.

For `/modem-tune revert`, skip generation; the target is the fixed factory IMEI.

## Phase 2 -- Apply (stages the change, no reset yet)

Run `python3 scripts/modem-tune.py apply --imei <NEW>` (or `revert` for the factory
value). The engine records the outgoing IMEI to the ledger history with a timestamp
BEFORE sending `AT+EGMR`, then sends it and expects `OK`. The change is staged but not
live until a reset.

If the engine reports the command did not return `OK`, stop and surface the raw output.
Do not reset.

## Phase 3 -- Confirmation gate (HARD STOP)

Use AskUserQuestion to confirm the reset. State plainly what happens:

- Full router reboot (the default and the reliable path on this device):
  ~2-3 min of downtime, SSH and internet drop and return.
- The modem-only reset (`AT+CFUN=1,1`, `reset --modem`) is opt-in and historically
  does not take on this GL-XE300 -- offer it only if the CEO asks.

Only an explicit yes proceeds. Silence or ambiguity means WAIT. Never reset without
this confirmation -- it is the one irreversible-feeling step.

## Phase 4 -- Reset + verify

On confirmation:

1. `python3 scripts/modem-tune.py reset` (full router reboot by default).
2. `python3 scripts/modem-tune.py verify --expect <NEW>`.

If verify still fails after the reboot, report the live value the modem reports and
stop -- do not loop indefinitely.

On success the engine has already marked the ledger `current` as verified. Report the
final live IMEI and its Luhn validity to the CEO.

## Output to the CEO

Close with a short summary: old IMEI -> new IMEI, ledger updated, verified live. No
hidden characters, plain prose.

## NEVER

- NEVER reset or reboot without the Phase 3 confirmation.
- NEVER run `revert` (factory IMEI) or any IMEI rollback unless the CEO explicitly
  asks for it in that turn. Do not offer it as an automatic recovery step.
- NEVER reuse an IMEI -- the engine enforces this via the ledger `used[]`; do not
  hand-pick a value that bypasses it (except the deliberate factory `revert`).
- NEVER write the SSH password into any tracked file, commit message, or output. It
  lives only in `.env`.
- NEVER document this skill, the router, IMEI values, or credentials in corporate or
  executive-facing files (`reference/workspace-overview.md`, `templates/`, corporate repo).
- NEVER scrape an external IMEI generator -- generation is local and deterministic.
- NEVER save router IMEI values or credentials to auto-memory.
