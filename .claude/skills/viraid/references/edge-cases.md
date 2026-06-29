# Viraid - Edge Cases

Consumed by: `.claude/skills/viraid/SKILL.md` for runtime fallback behavior across both message-processing and sweep modes.

- **First run (no state)** -> Create state with empty ledger (`last_message_id: 0`), fetch all with `--limit 500`, process all available messages
- **No new messages** -> Report count and last run date, stop
- **100+ new messages between runs** -> `--limit 100` picks up oldest 100, advances `last_message_id`; next run picks up more. Multiple runs drain the backlog.
- **Media-only message** -> Note category, "(media-only)" flag, skip enrichment
- **Person not in CRM** -> Note "Not in CRM" in enrichment, suggest `/crm add`
- **Empty message text** -> Auto-ignore, log in ledger as "ignored: empty"
- **State file corrupted** -> Start fresh with warning (all messages re-processed, `last_message_id: 0`)
- **Message already in ledger** -> Skip entirely, ledger is authoritative
- **Channel deletion fails** -> Log warning in action_summary ("channel delete failed"), set `channel_deleted: false` in ledger. Do NOT retry automatically -- the message may have already been manually deleted.
- **DB locked error** -> telegram_client.py auto-retries up to 3 times with backoff (Sentinel coexistence)
- **Legacy tasks without priority** -> During sweep, classify existing tasks that lack `P[1/2/3]` tags using the Priority Classification rules, then present with assigned priority
- **Sweep with zero active tasks** -> Report "No active tasks. Completion rate: 100%." and stop
- **Message ID gap detected** -> If integrity check finds a gap > 5 between `last_message_id` and highest tracked key, report the gap. Do NOT automatically reset `last_message_id` -- the gap likely means messages were processed in a session that didn't save state, or were manually deleted from Telegram.
