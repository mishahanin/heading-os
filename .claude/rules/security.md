<!-- version: 2.0.0 | last-updated: 2026-08-20 -->
# Workspace Security Policy

Last Verified: 2026-08-20

## Never Write Secrets to Tracked Files

NEVER write passwords, API keys, tokens, session data, or credentials to any file that is (or could be) tracked by Git. This includes:
- `knowledge/` notes (tracked)
- `context/` files
- `reference/` files
- `scripts/` (except `.env.example` with placeholder values)
- Any markdown, YAML, JSON, or code file in the workspace

## Where Secrets Belong

| Secret Type | Storage | Access Method |
|---|---|---|
| API keys | `.env` (gitignored) | `load_api_key()` from `scripts/utils/api.py` |
| Account passwords | Password manager (1Password/Bitwarden) | NEVER in workspace files |
| OAuth tokens | `.sessions/` (gitignored) | Auto-refreshed by scripts |
| Session files | `.sessions/` (gitignored) | Script-managed |
| Browser cookies | `outputs/browser/cookies.json` (gitignored) | `/setup-browser-cookies` |

## When Referencing Credentials in Documentation

Use these patterns:
- "Stored in password manager (1Password/Bitwarden)"
- "See `.env` (`HUNTER_API_KEY`)"
- "Auto-managed in `.sessions/`"

Never include the actual credential value, even partially.

## The two gates you can disarm

Hooks, scanners, and the push wall block whether or not you have read about them. Two of them you can switch off by hand, so do not:

- **Never pass `git commit --no-verify` (or `-n`).** It skips every commit gate.
- **Never set `core.hooksPath`.** A literal path value once silently bypassed every hook.

Run `pre-commit install` once per fresh clone or relocation, or the commit gates are not armed. `python scripts/install-hooks.py --check` verifies it: it reads the hook file git would actually run, and exits non-zero unless that file exists, carries the marker the pre-commit framework stamps into what it generates, and is executable. It follows `core.hooksPath` rather than assuming `.git/hooks`, so a redirect that leads to no hook is reported rather than passed. It does NOT run the hook, so it cannot tell you whether the hook still matches `.pre-commit-config.yaml` or whether its checks pass. Until 2026-09-02 it checked only that `.pre-commit-config.yaml` existed, which is true in every clone, so it exited 0 on a clone with no commit gate at all.

If a commit hook blocks your commit:
1. Remove the secret from the file
2. Move it to `.env` or password manager
3. Re-stage and commit (never with `--no-verify`)

The enumeration of security-critical files lives once, in `AGENTS.md` ("Which files are security-critical here") — add a file there, never here. A change to one of them earns a second read and a test that fails without it. No per-file gate stands behind it, so do not describe one.

Layer detail (the seven hooks and gates, what each refuses, the denial log): `docs/SECURITY-MODEL.md` § 6. Hook-by-hook reference: `docs/HOOKS-REFERENCE.md`.

## Credential Rotation

| Credential | Rotation | Owner |
|---|---|---|
| Exchange password | Every 90 days | Misha |
| OSINT service passwords | Every 90 days | Misha |
| API keys (Anthropic, etc.) | On compromise only | Misha |
| Google OAuth tokens | Auto-refresh | Scripts |
| Telegram session | On compromise only | Misha |

## Incident Response

If a secret is accidentally committed:
1. **Rotate the credential immediately** (before scrubbing history)
2. Scrub from Git history: `git filter-repo --replace-text expressions.txt --force`
3. Re-add remote: `git remote add origin <url>`
4. Force-push: `git push --force origin main`
5. Document in `outputs/operations/security/`
