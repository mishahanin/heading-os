<!-- version: 1.0.0 | last-updated: 2026-04-28 -->
# Workspace Security Policy

Last Verified: 2026-05-15

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

## Defense Layers

1. **Secret detection hook** (`.claude/hooks/_dispatch.py`, `check_prevent_secrets`): PreToolUse, registered for `Write|Edit|MultiEdit|NotebookEdit`, `Bash` and `Read` -- blocks content containing API key patterns, password patterns, and credential assignments on all four write tools before it reaches the filesystem, and the same patterns in a Bash command. A Read payload carries no content, so the check is inert there. `prevent-secrets.py`, the filename the block text still names, is a 28-line runpy shim that delegates here.
2. **Corporate boundary hook** (`.claude/hooks/_dispatch.py`, `check_protect_corporate`): PreToolUse, registered for `Write|Edit|MultiEdit|NotebookEdit` -- blocks writes to `corporate/` in exec workspaces (read-only, managed by CEO). `protect-corporate.py`, the filename this layer used to name, is a 28-line runpy shim that delegates here, exactly like `prevent-secrets.py` in layer 1. Layers 3 and 4 below name real files, not shims.
3. **Hidden character hook** (`post-write-sanitize.py`): PostToolUse Write|Edit -- scans written files for invisible Unicode characters and flags contamination.
4. **Prompt injection guard** (`prompt-guard.py`): PostToolUse Write|Edit -- advisory detection of prompt injection patterns in ingest-path files (knowledge/, datastore/, crm/contacts/).
5. **Pre-commit framework** (`.pre-commit-config.yaml`, `pre-commit install`): the engine commit gate. Its `secret-scanner-31c` local hook content-scans staged files with `scripts/secret-scanner.py`, alongside detect-secrets, `detect-private-key`, bandit, and the workspace guards. `.git/hooks` is machine-local and not shared by git, so run `pre-commit install` once per fresh engine clone or relocation (verify with `python scripts/install-hooks.py --check`). The data repo has no `.pre-commit-config.yaml` — it is covered at the push layer (next), not at commit time, because detect-secrets false-positives heavily on CEO data content. This is the EARLY-CATCH layer for engine, not the guarantee — see below.
6. **Push-time content scan** (`push-all.py` `content_scan()`): the AUTHORITATIVE, unbypassable gate for BOTH repos. Before pushing, `push-all.py` content-scans every file about to leave the machine (the `origin/main..HEAD` delta plus staged and unstaged tracked edits) via `secret-scanner.py` and refuses the push on any hit. It is pure code on the sanctioned push path (`push-all.py` / `/backup`) with no skip flag, so it catches secrets even when a commit hook was bypassed or absent.

(The former `protect-secure.py` vault air-gap hook was removed with the `_secure/` vault in Plan 5. Session sensitivity is now the fail-closed `SENSITIVE_MODE` flag — `scripts/utils/sensitive.py` — which suppresses observability and triggers external-API prompt sanitization; it is not a write-blocking hook.)

**Every refusal is counted.** Each layer above appends one redacted line to
`.logs/denials/denials.jsonl` when it refuses, via `log_denial()` from
`scripts/utils/denial_log.py`; read it with `python scripts/denials.py`. The
counter is telemetry, never a control: it changes no decision, it raises nothing
into a caller, and an unwritable log leaves every refusal intact. It exists
because until 2026-08-01 nothing counted a refusal, so a layer that was quietly
catching real mistakes and a layer that had never fired once looked identical
from the outside. For the PreToolUse layers the call sits in the dispatcher's
main loop rather than in the individual checks, so a check added later is counted
without its author doing anything. Two things are deliberately absent from a
record: the refused content (both the reason and the path pass through
`redact()`) and any Canopus gate refusal (that is slice friction, not an attempted
policy violation).

**Generated artifacts are redacted at birth.** `.claude/hooks/checkpoint-save.py`
runs the compact summary through `redact()` from `scripts/utils/secret_patterns.py`
before writing the handoff archive, so a session that merely DISCUSSES a
credential pattern cannot produce a tracked file that blocks its own backup.
Redaction is best-effort and never costs the handoff; layer 6 remains the wall.

The pattern vocabulary lives in `scripts/utils/secret_patterns.py`. The scanner
and the redactor import it. `.claude/hooks/_dispatch.py` keeps an embedded copy
on purpose, because a guarded import in the blocking PreToolUse gate would be
fail-open, and `tests/security/test_SEC_004_credential_patterns.py` holds the two
in lockstep.

### The commit hook is bypassable; the push scan is not

`git commit --no-verify` (or `-n`) skips every pre-commit hook, and git offers no setting to forbid that flag — the hook file can also simply be deleted. So the commit-time gate can never be made truly mandatory on its own. **Never pass `--no-verify`.** The guarantee that secrets never reach a remote lives at the push layer (layer 6, pure code, both repos) and, for a server-side guarantee, in GitHub push protection / secret scanning enabled on both private repos. Treat the commit hook as a fast local warning, not the wall. Do NOT set `core.hooksPath` (a literal path value once silently bypassed every hook — see `reference/workspace-overview.md`).

If a commit hook blocks your commit:
1. Remove the secret from the file
2. Move it to `.env` or password manager
3. Re-stage and commit (never with `--no-verify`)

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
