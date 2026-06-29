<!-- version: 1.1.0 | last-updated: 2026-05-24 -->
# VPN Pre-flight Gate

Last Updated: 2026-05-24
Last Verified: 2026-05-24

Before any skill or script that makes outbound requests to public web services which blacklist datacenter/VPN IPs -- YouTube (transcripts/captions), Google Search, LinkedIn, some OSINT sources -- Claude MUST present a VPN pre-flight gate and wait for explicit user confirmation before proceeding.

## Why

The default system VPN (Mullvad) uses datacenter exit IPs. YouTube's transcript API and some Google endpoints return `IpBlocked` / `RequestBlocked` from these ranges. Proton VPN (a residential-friendly exit, e.g. Amsterdam) passes these checks. Running a browsing skill on the wrong exit burns time and produces a degraded result (fallback to web search instead of native data).

On Windows the workspace has no programmatic control over the Proton VPN GUI client - the user manages the tunnel manually. On Linux, Proton ships an official CLI (`protonvpn-cli`) that allows scripted connect/disconnect, so the gate can offer to run the switch directly after explicit confirmation. macOS is GUI-only like Windows. This rule compensates for the GUI-only gap by forcing Claude to pause before any affected operation.

## Linux: Proton CLI

On Linux, Proton VPN ships an official CLI (`protonvpn-cli`, installed alongside the Proton VPN GTK app via `proton-vpn-gnome-desktop` on Debian/Ubuntu, the matching Flatpak, or the AUR package on Arch). Useful commands:

| Command | Purpose |
|---|---|
| `protonvpn-cli status` | Show tunnel state + current exit server |
| `protonvpn-cli connect --fastest` | Auto-pick the fastest server overall |
| `protonvpn-cli connect --cc NL` | Connect to a Netherlands exit (residential-friendly for YouTube/Google) |
| `protonvpn-cli disconnect` | Drop the tunnel |

When the gate triggers on Linux and the user picks "let me switch VPN first", Claude MAY run `protonvpn-cli connect --cc NL` directly after an explicit confirmation in that turn. On Windows and macOS the user always switches manually via the GUI.

If `protonvpn-cli` is not on PATH (some setups install only the GUI Flatpak with a sandboxed CLI binary), fall back to the manual prompt - tell the user to switch in the GUI and re-confirm.

## Which skills trigger the gate

| Trigger | Skill / script | Reason |
|---|---|---|
| Always | `/yt-pulse` | youtube-transcript-api is the primary victim |
| Always | `/playwright` youtube subcommand | same transcript API |
| Conditional | `/osint`, `/osint-advanced` | some OSINT sources rate-limit datacenter IPs |
| Conditional | `scripts/firecrawl.py` callers, `/playwright` on Google/LinkedIn URLs | bot detection |
| Never | Scripts hitting 31C Exchange, Telegram, Google Workspace APIs | authenticated endpoints, IP doesn't matter |
| Never | `/email-intel`, `/telegram`, `/sentinel` | as above |

When in doubt: gate it. Over-gating costs 5 seconds; under-gating costs a full skill run.

## The gate

1. Announce what's about to run:
   > Running `/yt-pulse` for "[query]".

2. Present the pre-flight notice:
   > **Pre-flight check before browsing operation.**
   >
   > **VPN:** Confirm you are connected to **Proton VPN** (or a residential proxy). If you are on Mullvad or bare ISP, YouTube/Google may block the request.

   The default automation browser (Brave) does not need to be closed for yt-dlp cookie reads - `--cookies-from-browser brave:ClaudeCode` uses the SQLite Online Backup API and works on a live profile. Skills that use CDP-attach automation (the `scripts/browser.py` helper) refuse to launch when Brave is already running; that error message is enough, no gate-level advisory needed.

3. Use `AskUserQuestion` with options:
   - `Yes, Proton VPN is active` -> proceed
   - `No, let me switch VPN first` -> halt, wait for user (on Linux: Claude may offer to run `protonvpn-cli connect --cc NL` after one more confirmation)
   - `Skip the check this time` -> proceed with a warning noted in the output

4. Only proceed on explicit confirmation. Silence or ambiguity means WAIT.

## Exit IP verification (optional, faster than asking)

Before presenting the gate, Claude MAY run a silent IP check:

```bash
python -c "import urllib.request, json; r = urllib.request.urlopen('https://api.ipify.org?format=json', timeout=5); print(json.loads(r.read().decode())['ip'])"
```

Then geolocate via `https://ipinfo.io/<ip>/json`. If the ASN contains `Proton AG` or the IP is in a known residential range, Claude can note "Detected Proton exit" or "Detected residential IP" in the gate message and default the confirmation to `Yes`. The user still confirms.

If the ASN contains `Mullvad` or the country matches the user's configured baseline home ISP, flag it in the gate message: "Detected [VPN/bare ISP] exit -- recommend switching to Proton before proceeding."

## Re-gating within a session

Once confirmed in a session, the gate does not need to re-fire for the same skill within a 30-minute window UNLESS:

- A prior skill run returned `IP_BLOCKED` or equivalent (tunnel may have dropped)
- The user explicitly says "check my IP" or similar

## Never bypass silently

If Claude proceeds past the gate without explicit confirmation, that is a protocol violation. The gate is non-negotiable for the skills listed above.
