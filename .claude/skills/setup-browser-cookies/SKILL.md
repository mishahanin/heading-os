---
name: setup-browser-cookies
description: "Import logged-in cookies from a real Chromium-family browser (Brave, Chrome, Chromium, Edge) into the workspace's Playwright/headless cookie store at outputs/browser/cookies.json, using the workspace-native scripts/utils/chromium_cookies.py reader. Use before QA-testing or scraping pages that require an authenticated session. For the browser automation itself use /playwright."
argument-hint: "[domain] (omit to be prompted for the domains to import)"
allowed-tools: "Bash(python3:*), Read, Write"
model: haiku
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "2.0"
x-31c-orchestration:
  parallel_safe: false
  shared_state:
    - outputs/browser/cookies.json
  triggers:
    - setup browser cookies
    - import cookies
x-31c-capability:
  what: >
    Imports logged-in cookies from a real Chromium-family browser (Brave, Chrome,
    Chromium, Edge) into the workspace cookie store at outputs/browser/cookies.json
    so Playwright and the headless browse session reuse the authenticated session.
  how: >
    Type /setup-browser-cookies <domain> (or omit the domain to be prompted). It
    runs scripts/utils/chromium_cookies.py to decrypt that domain's cookies from
    the local browser profile and writes a Playwright-compatible cookies.json.
  when: >
    Use before QA-testing or scraping pages that require an authenticated session.
    For the browser automation itself use /playwright.
---

# Setup Browser Cookies

Import logged-in sessions from your real Chromium-family browser into the workspace
cookie store at `outputs/browser/cookies.json`. Backed by the workspace-native
reader `scripts/utils/chromium_cookies.py` (the same decryptor `/yt-pulse` uses) —
no external binaries.

## How it works

`scripts/utils/chromium_cookies.py` reads and decrypts cookies for a domain directly
from the browser's profile DB (DPAPI on Windows, libsecret on Linux, Keychain on
macOS), per profile, per browser. This skill drives it per domain and assembles a
Playwright-compatible `outputs/browser/cookies.json`.

## Steps

### 1. Determine domain(s) and browser

If the user gave a domain (e.g. `/setup-browser-cookies github.com`), use it. Otherwise
ask: **"Which domain(s) should I import cookies for, and from which browser (brave /
chrome / chromium / edge, default brave)?"** Then STOP and wait.

The profile defaults to `ClaudeCode`; pass `--profile "<name>"` if the user logs in
under a different Chromium profile.

### 2. Extract cookies for the domain

```bash
python3 scripts/utils/chromium_cookies.py "<domain>" --browser brave --profile ClaudeCode --json --values
```

This prints a `{name: value}` JSON map for the domain (and its subdomains). If it
errors:

- **`No cookies found`** — the profile is not logged in to that domain; ask the user to
  log in in that browser/profile first.
- **`App-bound v20 ... not yet supported`** — Chrome M127+ app-bound encryption. Fall
  back to `yt-dlp --cookies-from-browser brave` for that workflow (documented in
  `.claude/rules/vpn-preflight.md`).
- **`secretstorage not installed` / locked keyring (Linux)** — only v10 cookies decrypt;
  unlock the keyring (gnome-keyring / kwallet) for v11.

### 3. Assemble the Playwright cookie store

For each imported domain, convert the `{name: value}` map into Playwright cookie
objects and merge them into `outputs/browser/cookies.json` (preserve any cookies for
other domains already present). Each object:

```json
{"name": "<name>", "value": "<value>", "domain": ".<domain>", "path": "/", "secure": true, "httpOnly": false, "sameSite": "Lax"}
```

Write the merged array to `outputs/browser/cookies.json` with the Write tool.

### 4. Confirm

Tell the user: **"Imported N cookie(s) for <domain> into `outputs/browser/cookies.json`
— Playwright will auto-load them for future browser commands."** Report only domain
names and counts, never cookie values.

## NEVER

- NEVER print cookie values to the chat or to any log — they are live session tokens.
  Use them only to build `outputs/browser/cookies.json` (which is gitignored).
- NEVER commit `outputs/browser/cookies.json` or paste its contents anywhere.
- NEVER send cookie data to any external service.

## Notes

- `outputs/browser/cookies.json` is gitignored and on the secret-scan allow-list.
- On Linux only Brave / Chrome / Chromium / Edge are supported (Comet and Arc are not
  available there).
- First read per browser may trigger a credential-vault prompt (macOS Keychain "Allow",
  Linux Secret Service unlock; Windows DPAPI is silent).
