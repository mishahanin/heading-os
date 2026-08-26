# Security Test Suite

Last Updated: 2026-03-25

Each test file corresponds to a finding in `docs/security/findings-registry.md`. Tests are named `test_SEC_XXX_description.py` where XXX matches the finding ID.

## Test Files

| Test File | Finding | What It Proves |
|---|---|---|
| `test_SEC_001_email_html_injection.py` | SEC-001 | Plain text email bodies are HTML-escaped before wrapping in tags |
| `test_SEC_002_hook_silent_failure.py` | SEC-002 | session-start.py has no bare `except Exception: pass` blocks |
| `test_SEC_003_gitignore_coverage.py` | SEC-003 | All sensitive paths are covered by .gitignore |
| `test_SEC_004_credential_patterns.py` | SEC-004 | Secret scanners detect Firecrawl and Google OAuth patterns |
| `test_SEC_005_sanitizer_trojan_source.py` | SEC-005 | Sanitizer detects Trojan Source bidirectional isolate characters |
| `test_SEC_006_oauth_dir_permissions.py` | SEC-006 | OAuth token directories created with mode=0o700 |
| `test_SEC_007_hook_timeout_logging.py` | SEC-007 | post-write-sanitize logs timeouts instead of silently passing |
| `test_SEC_010_sentinel_atomic_state.py` | SEC-010 | Sentinel state saves use atomic write-then-replace |
| `test_SEC_011_sentinel_signal_handler.py` | SEC-011 | Signal handlers registered after sentinel object creation |
| `test_SEC_012_sentinel_graceful_shutdown.py` | SEC-012 | Sentinel uses asyncio.Event for interruptible sleep |
| `test_SEC_013_sentinel_batch_error.py` | SEC-013 | analyze_batch separates JSONDecodeError from Exception |
| `test_SEC_014_sentinel_chat_timeout.py` | SEC-014 | Per-chat timeout in _check_monitored_chats |
| `test_SEC_015_sentinel_state_finally.py` | SEC-015 | run_cycle uses try/finally with state.save() |
| `test_SEC_016_sentinel_pid_lock.py` | SEC-016 | PID file creation uses file locking |
| `test_SEC_017_dispatch_check_branches.py` | SEC-017 | Each of the eight PreToolUse dispatcher checks exercised directly |
| `test_SEC_018_gate_behaviour.py` | SEC-018 | The blocking gate observed as a gate, not as a pattern list |
| `test_SEC_019_commit_air_gap.py` | SEC-019 | A private commit subject never reaches a memory-index store |
| `test_regression.py` | All SEC | Only-grows floor of SEC ids: fails if a regression file is deleted, renamed out of pytest's pattern, or emptied |

## Running Tests

```bash
pytest tests/security/ -v
```

## Rules

- Security tests are NEVER deleted
- Each finding gets its own test file
- Tests must fail against the vulnerable code and pass against the fixed code
- Permanent enforcement is the CI `security-tests` job running this directory,
  which collects every file here by name. `test_regression.py` used to re-import
  them all instead; that ran each test twice (721 nodes for 398 distinct tests)
  and published the inflated figure as the repository's security-test count.
- What replaced it is `KNOWN_SEC_IDS` in `test_regression.py`: an only-grows
  floor of SEC ids. A deleted file, one renamed out of `test_*.py`, or one with
  no test function left fails the suite. Add a new SEC id there in the same
  change that adds the file.
- The engine's SEC numbering does NOT match the private findings registry past
  SEC-016. See the docstring in `test_regression.py`.
