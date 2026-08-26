# Sentinel Integration Tests

Last Updated: 2026-08-27

Integration tests for `scripts/sentinel.py` that exercise classes and narrow-except sites with mocked external services (Exchange, Telethon, Anthropic). Built 2026-04-19. The plan behind them is archived in the operator's private data overlay and is not part of this repository, so nothing below depends on reading it.

## Running

```bash
# Full run with coverage
python scripts/run-integration-tests.py

# Quick run, no coverage
python scripts/run-integration-tests.py --quiet --no-cov

# Direct pytest
python -m pytest tests/integration/ -v
```

## Layout

Sentinel-related files only. The directory also holds unrelated integration
suites (`test_aggregate_crm_per_exec.py`, `test_convert_to_md.py`,
`test_setup_wizard_e2e.py`, `test_setup_wizard_snapshot.py`,
`test_workspace_helpers_per_exec.py`) that this README does not describe.

```
tests/integration/
├── __init__.py
├── conftest.py                      # Shared fixtures + Windows stdout workaround
├── fixtures/
│   ├── sample_emails.json           # 5 synthetic Exchange items
│   ├── sample.docx / corrupt.docx / unsupported.bin  # for test_convert_to_md.py
├── test_sentinel_components.py      # 6 component-level tests (state, duration, theme)
├── test_sentinel_hardening.py       # 8 tests covering all 7 narrow-except sites
└── README.md
```

Three JSON corpora were removed on 2026-08-27: `sample_tg_messages.json`,
`sample_meeting_invites.json` and `sample_analyzer_responses.json`. Each was
reachable only through a conftest fixture that no test had ever requested, so
none of them contributed a single assertion. Fabricate replacements from the
vendor docs if a future test needs them; the recipe is in "Adding New Fixtures"
below.

## Test Coverage

| File | Count | Purpose |
|---|---|---|
| `test_sentinel_components.py` | 6 | State manager roundtrip, missing/corrupt state, happy-path duration calc, keyword theme alignment |
| `test_sentinel_hardening.py` | 8 | All 7 narrow-except sites from 2026-04-19 hardening, plus broad-catch guard |
| **Total** | **14** | |

Coverage contribution, measured once on 2026-04-19: baseline 0% -> 18% (+18
points; target was +15-20). That figure has not been re-measured since and is a
historical record, not a current claim. Re-measure with
`python scripts/run-integration-tests.py` before quoting it anywhere.

## Narrow-Except Site Coverage

| Symbolic anchor | Test |
|---|---|
| `MeetingInviteSource.check_new_invites` duration calc | `test_meeting_duration_calc_with_incompatible_datetime` |
| `CalendarPolicyEngine._check_theme_alignment` LLM path (specific exception) | `test_theme_classify_llm_fails_falls_back_to_keywords` |
| `CalendarPolicyEngine._check_theme_alignment` LLM path (broad-catch guard) | `test_theme_classify_custom_exception_falls_back` |
| `TelegramSource.connect` WAL checkpoint | `test_telegram_wal_checkpoint_on_locked_session` |
| `TelegramSource.disconnect` session _conn close | `test_telegram_disconnect_with_preclosed_connection` |
| `Sentinel.run` disconnect-for-sleep | `test_telegram_disconnect_during_sleep_fails` |
| `Sentinel._fetch_all` retry-disconnect | `test_telegram_retry_disconnect_fails_second_disconnect` |
| `check_status` digest print | `test_status_prints_on_corrupt_state` |

## Mock Strategy

- **One shared mock in `conftest.py`:** `mock_exchange_account`, a `MagicMock` of `exchangelib.Account` whose inbox returns `fixture_emails`.
- **Telethon and Anthropic are mocked per test, inline.** Each hardening test injects a different failure (`AsyncMock` that raises on the second `disconnect`, an analyzer whose `create` raises a custom exception), so a shared client mock would have to be rebuilt in every test anyway. `conftest.py` carried `mock_telegram_client` and `mock_anthropic_client` from 2026-04-19 until 2026-08-27 and no test ever requested either.
- **Synthetic fixtures only** per operator decision 2026-04-19: no real email/telegram data, even sanitized. Fixtures use fabricated tokens (`alice@example.com`, `User1..User5`, Lorem ipsum bodies). Since the engine repository went public this is also the engine/data separation, not only a preference.
- **Logger assertions** use `MagicMock(spec=logging.Logger)` with `mock_logger.debug.call_args_list` inspection. `caplog` is avoided because mocks don't propagate through Python's logging hierarchy.
- **Stderr assertions** (e.g., `check_status`) use pytest's `capsys` fixture.

## Windows stdout/stderr Workaround

`scripts/sentinel.py` replaces `sys.stdout`/`sys.stderr` with `TextIOWrapper` at import time on Windows, inside its top-level `if sys.platform == "win32":` guard. This destroys pytest's capture layer. `conftest.py` patches `sys.platform` to `"linux"` briefly during the initial sentinel import to skip that branch, then restores. All subsequent imports reuse the cached module.

No line numbers here on purpose. This paragraph said "lines 80-82" from 2026-04-19 until 2026-08-27, by which time the guard sat at 97-99 and the citation sent a reader to the wrong place.

## Adding New Fixtures

Default: fabricate from scratch using Exchange / Telethon / Anthropic docs. No real data.

If a test genuinely needs a shape the docs don't cover, follow these six steps. They are stated in full here; the plan they came from lives in the private overlay and a reader of this repository cannot open it.

1. Keep the scratch file OUTSIDE this repository entirely. The original step named `_secure/fixtures-scratch/`, a vault that was removed when `SENSITIVE_MODE` replaced it; there is no in-repo location where real data is acceptable, even temporarily, even gitignored.
2. `scripts/sanitize-text.py --scan` gate.
3. `scripts/sanitize-check.py` leak scan.
4. Replace identifiers with synthetic tokens.
5. The operator manually reviews the diff.
6. Keep it under 5 KB.

Do NOT bypass steps 2-5.

## Known Issues

- **Windows ResourceWarning:** recorded 2026-04-19 as `ResourceWarning: unclosed database` from Telethon mocks holding sqlite handles in GC. Re-checked on Linux 2026-08-27 with `-W always::ResourceWarning` and it does not reproduce; the shared Telethon client mock it was blamed on has been removed. Kept here as a historical note, not a current defect.
- **No full Sentinel.run_cycle() test:** plan originally scoped tests 1-9 at the orchestrator level. Implemented at component level instead (80% of the validation value at 20% of the mock surface). The scope reduction was taken in the 2026-04-19 plan's Phase 2+3, which is archived privately; the decision itself is the sentence you just read, so nothing is lost by not having it.

## Classification

**Engine. Public.** `config/routing-map.yaml` has no rule for `tests/`, so every
file here falls to the `engine` default and ships in the public repository. That
is the intended state under "clean engine, no exceptions" (2026-06-14): code and
its tests are engine, instance values are private data.

This section said "CEO-only, not published" from 2026-04-19 until 2026-08-27. It
described the single-workspace topology that the engine/data split replaced, and
by the time the engine went public it was telling a contributor these files were
private when they were on the internet. A new test file added here is public the
moment it is committed: it must contain no real name, address, roster, price or
credential.
