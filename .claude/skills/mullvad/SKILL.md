---
name: mullvad
description: >-
  Benchmark Mullvad VPN relays by real network latency and connect on
  selection. Default invocation (/mullvad with no args) detects current
  connection state, disconnects if needed for an unbiased measurement, runs
  the parallel ICMP benchmark via scripts/mullvad-fastest.py, and presents
  the 3 fastest servers as a numbered menu. When the user replies with
  "connect 1", "connect 2", or "connect 3" (or an equivalent natural-language
  selection from the 3 shown), the skill executes mullvad relay set location
  HOSTNAME and mullvad connect and verifies the tunnel is up. Trigger on
  /mullvad, fastest mullvad server, switch mullvad server, check mullvad
  speed. Do NOT trigger on generic VPN questions, Mullvad account billing,
  other VPN providers, reading Mullvad help pages (use WebFetch), or when
  the user says the literal phrase "Mullvad" only as a topic of conversation
  without a clear operational intent.
argument-hint: "[connect <1|2|3>]"
allowed-tools: "Bash(mullvad:*), Bash(python:*), Bash(python3:*), Read"
model: haiku
effort: low
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: partial
  shared_state: []
  triggers:
    - /mullvad
    - fastest mullvad server
    - switch mullvad server
    - check mullvad speed
x-31c-capability:
  what: >
    Benchmarks Mullvad WireGuard relays by real ICMP latency from this host, presents the 3 fastest as a numbered menu, and connects to whichever one you select - verifying the tunnel is up.
  how: >
    Run /mullvad to disconnect, measure (scripts/mullvad-fastest.py --owned-only), and show the top 3; reply "connect 1/2/3" to switch. Results saved to outputs/operations/mullvad_fastest_YYYY-MM-DD.json.
  when: >
    Use to find and switch to the fastest Mullvad relay. For general VPN questions or Mullvad help pages use WebFetch; for the YouTube/Google residential-exit need use Proton, not Mullvad.
---
# /mullvad — Fastest Relay Benchmark & Connect

Finds the 3 fastest Mullvad WireGuard relays from this host (by median ICMP latency) and connects to whichever one the user selects. Reuses `scripts/mullvad-fastest.py` for the benchmark; uses the Mullvad CLI for tunnel control.

## Phase 0 — Preflight

Run these checks in order. Bail early with a clear message if any fails.

1. **CLI present:** `mullvad --version`. If not found → tell the user "Mullvad CLI is not installed. Get it from <https://mullvad.net/download/vpn/>" and stop.
2. **Daemon reachable:** `mullvad status`. If the command errors or prints "Daemon unreachable" / "service not running" → tell the user "Mullvad daemon is not running. Start the Mullvad VPN service and retry." and stop.
3. **Current connection state:** parse the `mullvad status` output.
   - If output begins with `Connected` → capture the current relay hostname (line `Relay: <hostname>`).
   - If output begins with `Disconnected` → no tunnel active.
4. **Tunnel-bias warning (only if currently connected):**
   - Show one line: `Currently connected via <hostname>. Pings through the tunnel bias results.`
   - Ask: "Disconnect, measure, and let you reconnect? (recommended) — [D]isconnect / [M]easure anyway / [C]ancel"
   - On `D` → run `mullvad disconnect`, wait 2 s, continue.
   - On `M` → continue without disconnecting; note the bias in the final summary.
   - On `C` → stop.

## Phase 1 — Benchmark

Run: `python scripts/mullvad-fastest.py --owned-only`

- Default region is Europe + Middle East (matches the script default).
- Owned-only is the default - Misha only uses Mullvad-owned relays (provider 31173/Blix).
- The script saves full results to `outputs/operations/mullvad_fastest_YYYY-MM-DD.json`. Read that file to get the sorted list — do not re-parse stdout.

If the script exits non-zero → surface the stderr and stop.

## Phase 2 — Present Top 3

Read the saved JSON. Show the top 3 entries in a compact numbered table:

```text
Top 3 fastest Mullvad relays from this host:

  1) <hostname>   <city>, <CC>   <Gbps> Gbps   <median> ms   [DAITA]
  2) <hostname>   <city>, <CC>   <Gbps> Gbps   <median> ms
  3) <hostname>   <city>, <CC>   <Gbps> Gbps   <median> ms

Reply "connect 1", "connect 2", or "connect 3" to connect.
```

Include `[DAITA]` suffix only on relays where `daita: true` in the JSON. If the user measured while still connected (chose `M` in Phase 0), add one line after the table: `Note: measured through <prev-hostname> tunnel — latencies are relative, not absolute.`

Keep the top-3 hostnames in conversation context for Phase 3. Do not write a state file.

## Phase 3 — Connect (on user selection)

Trigger: user replies with a clear selection from the 3 shown. Accept all of these:

- `connect 1` / `connect 2` / `connect 3`
- `1` / `2` / `3` standalone
- Ordinals: `first` / `second` / `third`
- City reference: e.g., `connect stockholm` — match against the 3 shown cities (unique match required; ask again if ambiguous)
- Hostname reference: e.g., `connect se-sto-wg-201` — must exactly equal one of the 3 shown hostnames

If the selection is ambiguous or refers to something not in the top 3, ask once for clarification instead of guessing.

Once resolved:

1. Announce: `Connecting to <hostname> (<city>, <country>)...`
2. If `mullvad status` still shows `Connected` → `mullvad disconnect`, wait 2 s.
3. `mullvad relay set location <hostname>` — sets the target relay.
4. `mullvad connect` — starts the tunnel.
5. Poll `mullvad status` up to 15 times, 1-second interval, until the output begins with `Connected` or the loop exits.
6. On success: run `mullvad status -v` once, extract `Visible location` and public IPv4, and report:
   `Connected: <hostname>. Visible: <city>, <country>. IPv4: <ipv4>.`
7. On timeout: show the last `mullvad status` output verbatim and say "Connect did not complete in 15 s. Check Mullvad UI or run `mullvad status -v` manually."

## Variations

- **No prior top-3 in context** (user says "connect 1" in a fresh session): re-run Phase 0–2 first, then Phase 3 on the newly generated list.
- **User cancels after seeing the list**: do nothing. Leave the tunnel in whatever state Phase 0 left it.
- **Benchmark returned zero reachable relays**: surface the error, suggest checking connectivity, stop.

## Voice Rules

- Use single hyphens in all output, never double dashes (workspace `hidden-chars.md` rule).
- Call it "Mullvad", not "the VPN" — the user asked specifically.
- Precise numbers in latency reporting: `89 ms`, not "about 90".

## NEVER

- Never write Mullvad account numbers, session tokens, or device keys to any workspace file.
- Never run `mullvad factory-reset`, `mullvad account logout`, or `mullvad custom-list delete` from within this skill.
- Never pick a relay silently — every connect requires the user's explicit selection from the presented top 3.
- Never invoke `mullvad connect` without first running `mullvad relay set location <hostname>` — otherwise the tunnel reconnects to whatever was last configured, not the selected relay.
- Never treat `mullvad connect` returning successfully as proof of connection — always verify with a follow-up `mullvad status` check.
- Never commit or log Mullvad public IPs to shared workspace locations; they reveal the current exit node.

## Roadmap (not in v1)

- `/mullvad status` — show current state without benchmarking.
- `/mullvad disconnect` — tear down the tunnel.
- `/mullvad all` — global scope instead of EU+ME.
- `/mullvad <hostname>` — direct connect to a named relay, bypassing the benchmark.
- `/mullvad daita-only` — restrict benchmark to DAITA-capable relays.
