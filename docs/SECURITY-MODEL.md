<!-- version: 1.2.0 | last-updated: 2026-08-20 -->
# Security model

Consumed by: `.claude/rules/security.md` (the always-on rule points here for the
layer detail), `AGENTS.md`, and the public docs site.

Last Updated: 2026-08-20

How HEADING OS protects your data and your principal. The controls here are
mechanical, enforced by code and tests, not by policy prose. This page explains the
model; the [SECURITY.md](https://github.com/mishahanin/heading-os/blob/main/SECURITY.md)
policy covers how to report a vulnerability.

The whole design answers one question: an agent reads your private data and reads
untrusted text from the outside world all day. How do you keep it from being steered
into leaking the first through a channel to the second?

---

## 1. The threat: the lethal trifecta

An AI agent becomes dangerous to its principal when three capabilities meet in the
same run:

1. **Access to private data** (CRM, the brain, mail, the datastore).
2. **Exposure to untrusted content** (inbound mail, web pages, messages, anything a
   stranger wrote that the agent reads and might be steered by).
3. **The ability to send externally** (email, messages, any outbound channel).

Any one or two legs is recoverable. All three together is the failure mode: untrusted
content instructs the agent, the agent reaches private data, the agent exfiltrates it,
with no human in the loop. HEADING OS handles private data and reads untrusted content
constantly, so legs 1 and 2 cannot be removed without removing the assistant's value.
The mitigation is therefore to keep **leg 3 permanently human-gated**, and to keep the
private data physically out of the shareable engine.

---

## 2. Engine and data, kept apart

The engine clone holds no private or personal data, and your data cannot leave on a
push, regardless of how a file was written. This is not a convention you have to
remember: it is enforced by several mechanical layers, each catching a different way
the boundary could be crossed:

- a static **bypass guard** against direct engine-root writes,
- a **leak guard** that classifies and refuses data-class files in the engine,
- a **data-path redirect** so writes resolve into your data overlay,
- a **build partition** keeping engine and data artifacts separate,
- a runtime **tree-clean check**,
- a **content guard** that scans engine files for real data-class entities,
- and an **unbypassable push-time wall**, in pure code with no skip flag.

The authoritative specification, the honest boundaries, and the proof (the layers are
asserted by tests, not just claimed) are in the
**[engine/data segregation contract](engine-data-segregation-contract.html)**. Read it
before adding code that writes files or touches the data seam.

---

## 3. Outbound send is always human-gated

Every skill, script, and daemon inherits one control: anything that can send to the
outside world is **drafted, queued, and recommended**, never sent autonomously. A
human approves before anything leaves.

This is enforced in code, not prose. Any action type that can send is floored to the
`gated` tier no matter what a config file claims, and an unknown or unclassified type
also resolves `gated` (fail-safe). A `config/tool-risk.json` edited to mark email
"autonomous" still resolves `gated`. The test suite asserts that a tampered ledger
cannot auto-send: the ledger is data, the send-gate is code.

An advisory layer may inspect a queued draft and attach a second opinion, but it can
only annotate. The human click is the only path from draft to sent.

---

## 4. The Action Queue (the approval surface)

The Action Queue is the one lane where proactive skills (cold-sweep, email-intel,
viraid) deposit a drafted action for your go or no-go. It is terminal-native and
daemon-free: the queue file is the source of truth, and you drive it from the CLI or
from chat.

```bash
uv run python scripts/action-queue.py list          # what is waiting
uv run python scripts/action-queue.py show <id>     # inspect one draft
uv run python scripts/action-queue.py approve <id>  # SENDS, synchronously, watched
uv run python scripts/action-queue.py edit <id>     # adjust a draft before approving
uv run python scripts/action-queue.py dismiss <id>  # drop it
uv run python scripts/action-queue.py retry <id>    # re-send a failed one
```

`/queue` is the chat equivalent. Each card carries a risk tier:

| Tier | Meaning | Flow |
|---|---|---|
| `autonomous` | read-only / display (a note, an alert) | surfaced; no click; never sends |
| `notify` | reversible state edit | auto-applied, with one-click undo |
| `gated` | irreversible outbound send | hard review gate: sends only on your explicit approve |

Your typed `approve` IS the human approval click, and the send happens in that same
command. There is no autonomous background send; the gate holds even with every daemon
down.

---

## 5. Secrets never reach a remote

Credentials load only from a gitignored `.env`, never from a tracked file. Two gates
back this:

- **Commit-time scan** (`pre-commit`): a fast local warning that content-scans staged
  files for secrets. It is bypassable (`git commit --no-verify` skips every hook), so
  treat it as a warning, not the wall. **Never pass `--no-verify`.**
- **Push-time content scan** (`push-all.py`): the authoritative, unbypassable gate. It
  scans every file about to leave the machine and refuses the push on any hit. Pure
  code on the sanctioned push path, no skip flag, so it catches a secret even if a
  commit hook was bypassed or absent.

Run `pre-commit install` once per clone to arm the commit-time gate. If a secret is
ever exposed, treat it as compromised: rotate it first, then scrub it from history.

---

## 6. The enforcement layers, end to end

Section 5 covers the two gates a secret meets on its way to a remote. This section
is the full layer stack, in the order a write meets it, moved here from
`.claude/rules/security.md` so the always-on rule carries only what a reader must
DO. Every layer below blocks whether or not anyone has read about it.

1. **Secret detection hook** (`.claude/hooks/_dispatch.py`, `check_prevent_secrets`): PreToolUse, registered for `Write|Edit|MultiEdit|NotebookEdit`, `Bash` and `Read` -- blocks content containing API key patterns, password patterns, and credential assignments on all four write tools before it reaches the filesystem, and the same patterns in a Bash command. A Read payload carries no content, so the check is inert there.
2. **Corporate boundary hook** (`.claude/hooks/_dispatch.py`, `check_protect_corporate`): PreToolUse, registered for `Write|Edit|MultiEdit|NotebookEdit` -- blocks writes to `corporate/` in exec workspaces (read-only, managed by CEO).
3. **Hidden character hook** (`post-write-sanitize.py`): PostToolUse Write|Edit -- scans written files for invisible Unicode characters and flags contamination.
4. **Prompt injection guard** (`prompt-guard.py`): PostToolUse Write|Edit -- advisory detection of prompt injection patterns in ingest-path files (knowledge/, datastore/, crm/contacts/).
5. **Pre-commit framework** (`.pre-commit-config.yaml`, `pre-commit install`): the engine commit gate. Its `secret-scanner-31c` local hook content-scans staged files with `scripts/secret-scanner.py`, alongside detect-secrets, `detect-private-key`, bandit, and the workspace guards. `.git/hooks` is machine-local and not shared by git, so run `pre-commit install` once per fresh engine clone or relocation (verify with `python scripts/install-hooks.py --check`). The data repo has no `.pre-commit-config.yaml` — it is covered at the push layer (next), not at commit time, because detect-secrets false-positives heavily on CEO data content. This is the EARLY-CATCH layer for engine, not the guarantee — see below.
6. **Push-time content scan** (`push-all.py` `content_scan()`): the AUTHORITATIVE, unbypassable gate for BOTH repos. Before pushing, `push-all.py` content-scans every file about to leave the machine (the `origin/main..HEAD` delta plus staged and unstaged tracked edits) via `secret-scanner.py` and refuses the push on any hit. It is pure code on the sanctioned push path (`push-all.py` / `/backup`) with no skip flag, so it catches secrets even when a commit hook was bypassed or absent.

(The former `protect-secure.py` vault air-gap hook was removed with the `_secure/` vault in Plan 5. Session sensitivity is now the fail-closed `SENSITIVE_MODE` flag — `scripts/utils/sensitive.py` — which suppresses observability and triggers external-API prompt sanitization; it is not a write-blocking hook.)

7. **Harness audit** (`scripts/harness-audit.py`): the only layer that looks
   OUTWARD rather than inward. Every layer above watches what this workspace
   writes; this one watches what it installs and then executes -- the plugin
   cache, the hooks plugins register, and user-level settings this repository
   does not own. It enumerates third-party hook commands, hashes the installed
   surface against a reviewed baseline kept in the PRIVATE data overlay (never in
   the public engine: 236 sha256 digests read as high-entropy strings and the
   commit gate refuses them, correctly) so an upgrade is a readable diff, and scans all loaded content for injected
   instructions using the shared vocabulary in
   `scripts/utils/injection_patterns.py`. **It is a reporter, not a gate**: it
   refuses nothing and is wired into no hook, on purpose, so that its first
   measurement decides whether it earns one. A missing baseline is reported, not
   read as agreement. The `<!-- audit-skip-start -->` allowance and the path
   allowance both apply to files in THIS repository only, never to installed
   content, because a marker an attacker can write is a marker an attacker can
   hide behind. Run it with `python scripts/harness-audit.py`; accept a reviewed
   surface with `--update-manifest`.

**Every refusal is counted.** Each layer above appends one redacted line to
`.logs/denials/denials.jsonl` when it refuses, via `log_denial()` from
`scripts/utils/denial_log.py`; read it with `python scripts/denials.py`. The
counter is telemetry, never a control: it changes no decision, it raises nothing
into a caller, and an unwritable log leaves every refusal intact. It exists
because until 2026-08-01 nothing counted a refusal, so a layer that was quietly
catching real mistakes and a layer that had never fired once looked identical
from the outside. For the PreToolUse layers the call sits in the dispatcher's
main loop rather than in the individual checks, so a check added later is counted
without its author doing anything. One thing is deliberately absent from a
record: the refused CONTENT, because both the reason and the path pass through
`redact()`. Everything else about a refusal is kept — which layer refused, when,
and against what action.

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

### The security-critical files, and what guards a change to them

The layers above are themselves code, and the files that implement them are the
ones where a wrong edit costs more than a bug: the hooks, the pattern vocabulary
and the scanner, the push wall and its detectors, the commit-time guards, the
send gate and its ledger, the two egress controls, the routing input, the test
gate, and the rules those controls implement in prose.

**The enumeration lives once, in `AGENTS.md`**, under "Which files are
security-critical here", and this page deliberately does not restate it. A
second copy is a second thing to maintain, and the copy that stops being
updated is the one someone reads: until 2026-08-07 a paragraph of
`.claude/rules/security.md` WAS that second copy, naming all eighteen files
directly above a sentence claiming they were named once. Read `AGENTS.md` for
the list; add a file there, not here.

What stands behind a change to any of them is the ordinary machinery and nothing
extra: the pre-commit gates (the `31C secret scanner` hook above all), the
unbypassable push-time content scan, and the `sovereignty guards` CI job. There
is no per-file gate and no depth classifier — the one that existed was deleted on
2026-08-07 with the Canopus freeze lifecycle it served, and two tests that had
asserted the egress controls were on its surface went with it. So the sentence is
addressed to the author, not to a tool: a change here earns a second read and a
test that fails without it. Do not describe it as guarded by anything more.

---

## 7. Other controls

- **Hidden-character policy.** Generated text carries zero invisible Unicode
  (zero-width spaces, soft hyphens, and the like). `scripts/sanitize-text.py` scans for
  it, and a post-write hook flags contamination.
- **Forbidden-pattern gates.** The test and lint suite blocks the usual dangerous
  patterns: `eval` / `exec` on input, `pickle` on untrusted data, `shell=True`,
  unsafe YAML loading, disabled TLS verification, and similar.
- **No hope-based waiting.** Every must-complete step (every push) runs under a
  progress watchdog that declares a hang only on real inactivity and verifies its
  postcondition, rather than trusting a wall-clock timeout or a bare exit code.

---

## 8. Your responsibilities

The engine enforces a great deal, but the first line of defense is you:

- Keep API keys, tokens, and passwords in the gitignored `.env` only.
- Keep your real data in your **private** data repository, not the engine clone.
- Run `pre-commit install` once per clone.
- Rotate any exposed secret before scrubbing it.
- When changing code that touches authentication, sending, or the data seam, expect
  (and apply) extra scrutiny.

---

## 9. Reference

| File | Role |
|---|---|
| [`THREAT-MODEL.md`](THREAT-MODEL.html) | Threat to control to test evidence map (every control, the test that proves it) |
| [`SECURITY.md`](https://github.com/mishahanin/heading-os/blob/main/SECURITY.md) | Reporting policy + posture summary |
| [`engine-data-segregation-contract.md`](engine-data-segregation-contract.html) | The engine/data guarantee, layers, and proof |
| `scripts/action-queue.py` | The terminal-native approval surface |
| `scripts/utils/tool_risk.py`, `config/tool-risk.json` | The tier model and the send-gate invariant |
| `scripts/push-all.py` | The push path with the unbypassable content scan |
| `scripts/sanitize-text.py` | Hidden-character scanner |

---

## Manual security drills

Some enforcement layers cannot be exercised headlessly, so they are verified by
a periodic manual drill rather than a green test. Listing them here keeps the
gap explicit rather than silent. The mechanical leak-path matrix
([`tests/security/test_leak_path_matrix.py`](https://github.com/mishahanin/heading-os/blob/main/tests/security/test_leak_path_matrix.py))
attacks every headless-testable segregation layer on purpose (write-vector by
data-class-target, asserting each leak is blocked by the expected layer); the
drills below cover the layers it cannot reach.

### data-path-redirect hook (PreToolUse Write/Edit)

The `data-path-redirect` hook fires only inside the Claude Code runtime, so no
pytest cell can exercise it without asserting a simulation instead of the
control. Drill it by hand, on a cadence:

1. In a live Claude Code session on an engine clone, attempt to write a
   data-class path inside the engine tree (for example, ask Claude to create
   `crm/contacts/drill-check.md`).
2. Confirm the write is redirected into the data overlay
   (`.heading-os-data/crm/contacts/drill-check.md`), not created under the
   engine clone.
3. Confirm the engine working tree stays clean: `git status` shows nothing under
   `crm/`.
4. Remove the drill file from the overlay afterward.

Expected observable: the file lands in the data overlay, never in the engine
clone; the engine tree remains code-only.

---

*HEADING OS · Security model · maintained by Misha Hanin · see also
[Extending the engine](EXTENDING.html) for the developer-side gates and
[Architecture](ARCHITECTURE.html) for how the controls sit in the whole.*
