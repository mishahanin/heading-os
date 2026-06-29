# YT-Pulse - Configuration Reference

Consumed by: `.claude/skills/yt-pulse/SKILL.md` Configuration section.

Last Updated: 2026-06-10

Static configuration knobs for `pulse.py` and `pw.py youtube`: per-channel cap, browser cookie sourcing, and optional residential proxy fallback. Refactored 2026-05-15 to close P2.2 from the workspace deep audit - inline configuration bloated SKILL.md past the 300-line ceiling.

---

## Per-Channel Cap (v1.3, BEHAVIOUR CHANGE)

`pulse.py --per-channel-cap N` limits the number of videos any single YouTube channel can contribute to the ranked output. Default: **3**. Pass `--per-channel-cap 0` to restore the legacy uncapped behaviour from v1.2 and earlier.

The cap is applied AFTER engagement scoring, BEFORE the final ranking output. Surviving N videos per channel are the highest-scoring N from that channel; the remainder are suppressed and surfaced as a `more_from_channel_count` field on the highest-scoring survivor (rendered as "+N more from this channel" in downstream briefs).

**Migration note:** v1.3 introduces this default cap=3 to prevent any single channel from dominating the trending list. Existing /yt-pulse runs will produce diversified rankings starting v1.3. To reproduce a v1.2 baseline ranking, pass `--per-channel-cap 0`.

```bash
# New default: cap=3
python pulse.py -q "AI agents" -t 72h -m 50 -o results.json

# Legacy uncapped behaviour
python pulse.py -q "AI agents" -t 72h -m 50 --per-channel-cap 0 -o results.json
```

Channel grouping uses `channel_id` if available in yt-dlp metadata, with normalised `channel_name` as the fallback (lowercase, trimmed).

---

## Browser Cookie Source (default: Brave ClaudeCode profile)

Both `pulse.py` and `pw.py youtube` default to pulling YouTube cookies directly from
the Brave `ClaudeCode` profile via yt-dlp's `cookiesfrombrowser` mechanism. Brave is
cross-platform (Linux/macOS/Windows) and yt-dlp-native, with sessions pre-loaded on
the CEO machine as of 2026-05-23. This gives the yt-dlp search and metadata paths
authenticated access to logged-in-only videos and reduces bot-detection friction -
zero setup required.

To override or disable:

```bash
# Use a different browser:profile
python pulse.py -q "query" --browser "chrome:Default"

# Use a Netscape cookie file instead (overrides --browser)
python pulse.py -q "query" --cookies path/to/cookies.txt

# Disable entirely
python pulse.py -q "query" --browser none
```

Caveat: Chromium-based browsers (Brave, Chrome, Edge, Vivaldi) lock their cookie
SQLite DB while the browser is running on Windows. If the cookie-source browser
is open when `/yt-pulse` runs, yt-dlp errors with "Could not copy Chrome cookie
database" and yt-pulse proceeds without authenticated cookies (not fatal - just
loses the bot-detection benefit). To use this feature, close the browser briefly
before running, or accept the degraded-but-working path. Linux is generally
unaffected - cookies live in plaintext files there.

Note: the `youtube-transcript-api` path used by `pw.py youtube` for the actual
transcript text does NOT benefit from cookies (its cookie support is broken upstream
and cookies don't bypass YouTube's datacenter-IP block). The VPN pre-flight in SKILL.md
handles that.

---

## Proxy Setup (optional, only if VPN is unavailable)

If transcript extraction fails due to IP blocking AND the VPN pre-flight cannot
provide a working exit, fall back to a residential proxy:

1. Get a residential proxy (Webshare, BrightData, or any SOCKS5/HTTP proxy)
2. Pass to pw.py:

   ```bash
   python pw.py youtube "[id]" --proxy "http://user:pass@host:port" -f json -o output.json  # pragma: allowlist secret
   ```
