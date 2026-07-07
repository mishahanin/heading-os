<!-- version: 1.0.0 | last-updated: 2026-07-07 -->
# Threat model

This page maps each threat HEADING OS defends against to the concrete control
that stops it and to the exact test or CI guard that proves the control holds.
It is the auditable companion to the [Security model](SECURITY-MODEL.html): that
page explains the design in prose; this one is the evidence table.

The rule for every row: it resolves to a runnable test ID, a runnable CI guard,
or an explicitly-marked manual drill. Rows that resolve to nothing are listed
honestly in the gap list at the end. That honesty is the point: a security story
is only as credible as the parts of it you can run, plus a truthful account of
the parts you cannot.

Test IDs are pytest node paths (`pytest <path>`); CI guards are the named steps
in `.github/workflows/ci.yml` (each is a shell command you can run locally too).

---

## 1. Lethal trifecta: leg 3 (outbound send)

The one control the whole design refuses to weaken: nothing leaves without a
human click.

| Threat / attack class | Control | Test / guard |
|---|---|---|
| Agent sends outbound (email/telegram) without human approval | Send-gate invariant: any send-capable `action_type` floors to the `gated` tier regardless of the ledger | `pytest tests/test_tool_risk.py tests/test_action_queue_tiers.py` |
| Tampered risk ledger tries to auto-send | `tier_for()` resolves unknown/tampered types to `gated` | `pytest tests/test_action_queue_tiers.py` |

## 2. Lethal trifecta: leg 2 (untrusted content / prompt injection)

| Threat / attack class | Control | Test / guard |
|---|---|---|
| Prompt injection via inbound email fields (sender/subject/body) | Untrusted-input isolation before analysis | `pytest tests/security/test_email_injection_corpus.py`; SEC-001 `pytest tests/security/test_SEC_001_email_html_injection.py` |
| Prompt-injection payloads in ingest-path files (knowledge, datastore, CRM) | PostToolUse prompt-guard advisory detection | `pytest tests/security/test_protect_corporate_and_prompt_guard.py` |
| Adversarial injection corpus regressions | Curated attack corpus replayed on every commit | `python tests/security/prompt-injection/run-adversarial-suite.py --dry-run` (pre-commit) |
| Trojan Source / hidden bidirectional Unicode smuggled into text | Sanitizer detects bidi isolates + zero-width characters | SEC-005 `pytest tests/security/test_SEC_005_sanitizer_trojan_source.py`; `pytest tests/security/test_sanitize_text_subprocess.py` |

## 3. Lethal trifecta: leg 1 (private data) plus exfiltration channels

| Threat / attack class | Control | Test / guard |
|---|---|---|
| Private/corporate content leaves in the public engine repo | Engine/data segregation + push-time content scan + leak guard | `pytest tests/test_engine_tree_clean.py tests/test_data_root_no_bypass.py`; CI guard `Leak guard`, `Engine tree clean` |
| A data directory is joined onto an engine root (path bypass) | Data-root seam refuses engine-root joins | `pytest tests/test_data_root_no_bypass.py`; CI guard `HEADING OS data-root guard` |
| Read of CEO-only personal thread files by a subagent | PreToolUse dispatch read-guard blocks the read | `pytest tests/security/test_dispatch_read_guard.py` |

## 4. Secret exfiltration

| Threat / attack class | Control | Test / guard |
|---|---|---|
| A credential is committed | detect-secrets + workspace secret-scanner + push content scan | CI guard `detect-secrets baseline drift (F-9.4)`; SEC-004 `pytest tests/security/test_SEC_004_credential_patterns.py` |
| A sensitive path is not gitignored | `.gitignore` coverage assertion | SEC-003 `pytest tests/security/test_SEC_003_gitignore_coverage.py` |
| Session/OAuth files world-readable | `.sessions/` permission enforcement (0700/0600) | SEC-006 `pytest tests/security/test_SEC_006_oauth_dir_permissions.py` |

## 5. Hook bypass and silent failure

| Threat / attack class | Control | Test / guard |
|---|---|---|
| A hook swallows an exception and fails open | No bare-except; log-or-raise | SEC-002 `pytest tests/security/test_SEC_002_hook_silent_failure.py` |
| A hook hangs and its timeout is swallowed | Timeouts are logged, not swallowed | SEC-007 `pytest tests/security/test_SEC_007_hook_timeout_logging.py` |
| The consolidated dispatch hook mis-routes or skips a branch | Branch-coverage regression on `_dispatch.py` | SEC-017 `pytest tests/security/test_SEC_017_dispatch_check_branches.py`; `pytest tests/security/test_dispatch_routing.py` |

## 6. Daemon compromise and dashboard exposure

| Threat / attack class | Control | Test / guard |
|---|---|---|
| DNS-rebinding / localhost-CSRF reaches the local dashboard | Host/Origin guard (421 non-loopback Host, 403 cross-origin) | `pytest tests/bridge/test_host_origin_guard.py` |
| Bearer-token file drifts to world-readable | Token file is 0600; health check catches drift | `python scripts/workspace-health.py --section daemon-token`; SEC-006 |
| Daemon state corrupts on crash/kill | Atomic write-then-replace, interruptible sleep, try/finally save, PID lock | SEC-010..016 `pytest tests/security/test_SEC_01*_sentinel_*.py` |

## 7. Supply chain and integrity

| Threat / attack class | Control | Test / guard |
|---|---|---|
| A vendored skill tree is tampered | `skills-lock.json` hash verifier (sha256-tree-v1) | CI guard `Vendored-skill hashes (F-9.5)` = `python scripts/verify-skills-lock.py --check` |
| A dependency ships a known CVE | pip-audit on the pinned requirements | pre-commit / CI `pip-audit`; SBOM job publishes a CycloneDX bill of materials (F-9.6) |
| Overall supply-chain posture regresses | OpenSSF Scorecard, weekly, SARIF to code-scanning | `.github/workflows/scorecard.yml` (F-9.6) |
| A write lands on an un-migrated data overlay | Migration framework + `require_writable_data_root()` refusal | `pytest tests/test_data_migrations.py` (F-9.7) |
| A file drifts into the wrong classification/routing | routing-map resolution health | CI guard `Classification health (F-9.3)` |

## 8. Governance integrity

| Threat / attack class | Control | Test / guard |
|---|---|---|
| The Security Constitution goes missing | Existence regression | `pytest tests/security/test_security_constitution_exists.py` |
| An open security finding ships unresolved | Findings registry well-formed and zero-open (acceptance) | `pytest tests/security/test_findings_registry.py` |
| General security regressions creep back | Permanent regression suite | `pytest tests/security/test_regression.py` |

---

## Coverage

Of the rows above, every one resolves to a runnable pytest node or a runnable CI
guard. The mapped fraction is 100% of listed rows; the value of the table is that
the list is honest about what it does *not* claim below.

## The honest gap list (threats not mechanically tested here)

These are real and deliberately out of scope for a mechanical test. They are
managed by process, not by a green check, and are named so the table does not
imply coverage it lacks.

- **Operator social engineering.** A human tricked into approving a gated send is
  outside the send-gate's reach. Mitigation: the approval surface shows the full
  draft; process, not code.
- **Physical/host compromise.** An attacker with the machine and the `.env` has
  the keys. Mitigation: disk encryption and OS-level controls, out of engine scope.
- **A pinned action's upstream compromise before the SHA changes.** SHA-pinning
  stops tag-mutation, not a compromise published at the pinned SHA. Mitigation:
  Scorecard trend + Dependabot review; a manual drill, not a test.
- **A zero-day in a third-party dependency with no published advisory.** pip-audit
  only knows published CVEs. Mitigation: SBOM makes the exposure enumerable when
  an advisory lands.

---

*HEADING OS · Threat model · maintained by Misha Hanin · companion to the
[Security model](SECURITY-MODEL.html). Run the whole evidence set with
`pytest tests/security -q` plus the named CI guards in
[`ci.yml`](https://github.com/mishahanin/heading-os/blob/main/.github/workflows/ci.yml).*
