# Sentinel Integration Tests

Last Updated: 2026-04-19

Integration tests for `scripts/sentinel.py` that exercise classes and narrow-except sites with mocked external services (Exchange, Telethon, Anthropic). Built per plan `plans/2026-04-19-sentinel-integration-tests.md`.

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

```
tests/integration/
├── __init__.py
├── conftest.py                      # Shared fixtures + Windows stdout workaround
├── fixtures/
│   ├── sample_emails.json           # 5 synthetic Exchange items
│   ├── sample_tg_messages.json      # 5 synthetic Telegram messages
│   ├── sample_meeting_invites.json  # 3 invites (incl. intentionally bad datetime)
│   └── sample_analyzer_responses.json  # 5 Anthropic-shaped responses
├── test_sentinel_components.py      # 6 component-level tests (state, duration, theme)
├── test_sentinel_hardening.py       # 8 tests covering all 7 narrow-except sites
└── README.md
```

## Test Coverage

| File | Count | Purpose |
|---|---|---|
| `test_sentinel_components.py` | 6 | State manager roundtrip, missing/corrupt state, happy-path duration calc, keyword theme alignment |
| `test_sentinel_hardening.py` | 8 | All 7 narrow-except sites from 2026-04-19 hardening, plus broad-catch guard |
| **Total** | **14** | |

Coverage contribution: baseline 0% -> 18% (+18 points; target was +15-20).

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

- **Full mocks** (`unittest.mock.MagicMock`, `AsyncMock`) for Exchange `Account`, Telethon `TelegramClient`, Anthropic `Anthropic`.
- **Synthetic fixtures only** per CEO decision 2026-04-19: no real email/telegram data, even sanitized. Fixtures use fabricated tokens (`alice@example.com`, `User1..User5`, Lorem ipsum bodies).
- **Logger assertions** use `MagicMock(spec=logging.Logger)` with `mock_logger.debug.call_args_list` inspection. `caplog` is avoided because mocks don't propagate through Python's logging hierarchy.
- **Stderr assertions** (e.g., `check_status`) use pytest's `capsys` fixture.

## Windows stdout/stderr Workaround

`scripts/sentinel.py` replaces `sys.stdout`/`sys.stderr` with `TextIOWrapper` at import time on Windows (lines 80-82). This destroys pytest's capture layer. `conftest.py` patches `sys.platform` to `"linux"` briefly during the initial sentinel import to skip that branch, then restores. All subsequent imports reuse the cached module.

## Adding New Fixtures

Default: fabricate from scratch using Exchange / Telethon / Anthropic docs. No real data.

If a test genuinely needs a shape the docs don't cover, follow the 6-step protocol in `plans/2026-04-19-sentinel-integration-tests.md` Phase 1 step 3:

1. Scratch file in `_secure/fixtures-scratch/` (vault, out of git).
2. `scripts/sanitize-text.py --scan` gate.
3. `scripts/sanitize-check.py` leak scan.
4. Replace identifiers with synthetic tokens.
5. CEO manually reviews the diff.
6. Keep <5KB.

Do NOT bypass steps 2-5.

## Known Issues

- **Windows ResourceWarning:** pytest reports `ResourceWarning: unclosed database` at the end of runs due to Telethon mocks holding sqlite handles in GC. Cosmetic, does not affect test outcomes.
- **No full Sentinel.run_cycle() test:** plan originally scoped tests 1-9 at the orchestrator level. Implemented at component level instead (80% of the validation value at 20% of the mock surface). See `plans/2026-04-19-sentinel-integration-tests.md` -> Phase 2+3 scope.

## Classification

CEO-only. Not published to corporate repo (sentinel is CEO-specific infrastructure; execs don't run the daemon). See `config/routing-map.yaml` when adding new test files under `tests/integration/`.
