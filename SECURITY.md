# Security Policy

Security is a first-class concern in HEADING OS, not an afterthought. This document explains how to report a vulnerability and summarizes the controls the engine ships with.

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Report privately, by either:

1. **GitHub private advisory** — use the repository's **Security → Report a vulnerability** tab (GitHub private vulnerability reporting), or
2. **Email** — `misha.hanin@odinix.com` with the subject line `HEADING OS security`.

Please include enough detail to reproduce: the affected file or path, the conditions, and the impact you observed. If you have a proof of concept, include it.

**What to expect:**

- An acknowledgement within a few business days.
- An assessment and, where the report is valid, a fix on a timeline proportional to severity.
- Credit in the release notes if you would like it (tell us how you wish to be named).

Please give us a reasonable window to remediate before any public disclosure.

## Supported versions

The project is pre-1.0. Security fixes are applied to the latest `main`. There are no long-term support branches yet.

| Version | Supported |
|---------|-----------|
| `main` (latest) | Yes |
| older tags | No |

## What the engine already enforces

These are mechanical controls in this repository, not aspirations:

- **Engine ⟂ data separation.** Five enforcement layers (a static bypass guard, a leak guard, a data-path redirect, a build partition, and a runtime tree-clean check) ensure the engine clone holds no private or personal data, regardless of how a file was written. Specified in [`docs/engine-data-segregation-contract.md`](docs/engine-data-segregation-contract.md).
- **Outbound send is always human-gated** (the lethal-trifecta control). Anything that can send to the outside world is drafted and queued; a human approves before it leaves. New send-capable actions inherit this by default and fail safe.
- **Secrets never reach a remote.** A content scan on the sanctioned push path is pure code with no skip flag — it catches a credential even if a commit-time hook was bypassed. Secrets load only from a gitignored `.env`, never from a tracked file.
- **No hope-based waiting.** Every must-complete step (every push) runs under a progress watchdog that declares a hang only on real inactivity and verifies its postcondition, rather than trusting a wall-clock timeout or a bare exit code.
- **Forbidden-pattern gates.** The test and lint suite blocks the usual dangerous patterns (`eval`/`exec` on input, `pickle` on untrusted data, `shell=True`, unsafe YAML loading, TLS verification disabled, and similar).

## Handling your own secrets and data

If you run HEADING OS:

- Keep API keys, tokens, and passwords in the gitignored `.env` only. Never commit them.
- Keep your real data in your **private** data repository, not in the engine clone.
- Run `pre-commit install` once per clone so the commit-time secret scan is armed.
- If a secret is ever exposed, treat it as compromised: rotate it first, then scrub it from history.

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for the full setup, including the secret gate.
