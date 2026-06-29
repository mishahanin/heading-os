# Email-Intel - Execution Templates

Consumed by: `.claude/skills/email-intel/SKILL.md` Phase 4 (Execute Approved Actions).

Exact formats for the four write paths Phase 4 produces: CRM interaction logs, task entries, new CRM contacts, and knowledge notes. Pipeline updates have their own gated flow described in SKILL.md and remain there - they require a second explicit confirmation, not a static template.

## CRM Interaction Log

Exact format matching existing entries:

```markdown
### YYYY-MM-DD | Email | [Subject/Topic]
[Summary]. Direction: [sent/received/exchange].
[If outgoing] Recipients: [email addresses]
[If commitment] Commitment: [who] to [what] by [when].
Source: Email Intel digest YYYY-MM-DD.
```

CRM execution rules:

1. Read the contact file fresh before writing (avoid conflicts with concurrent edits)
2. Prepend new entry at TOP of `## Interaction Log` section (newest first)
3. Update `last_touch:` in frontmatter YAML to the conversation date
4. If commitments detected: add to `## Active Commitments` section using format:
   ```markdown
   - [ ] [Description] -- [who] by [when]
   ```
5. One CRM log per conversation thread per day - never duplicate

## Task Entry

Exact Viraid-compatible format:

```markdown
- [ ] **YYYY-MM-DD** | `P[1/2/3]` | [Description] | *Email Intel* | Source: [sender] "[subject]"
```

- Append to `outputs/operations/email-intelligence/tasks.md` under `## Active` (newest at top)
- If file doesn't exist, create it:
  ```markdown
  # Email Intelligence Action Items

  > Captured from 31C Exchange email processing. Managed by `/email-intel`.
  > Last updated: YYYY-MM-DD

  ## Active

  ## Completed
  ```

## New CRM Contact

Create `crm/contacts/{firstname-lastname}.md` with:

```yaml
---
name: [Full Name]
company: [Company]
title: [Title]
type: prospect
email: [email]
cadence: 14
last_touch: [today YYYY-MM-DD]
created: [today YYYY-MM-DD]
---
```

Then add:

- `## Profile` section with what's known from the email
- Empty `## Active Commitments` section
- `## Interaction Log` section with first entry from the email (use the CRM Interaction Log format above)

## Pipeline Update Card (R4)

When an approved conversation signals a CLEAR pipeline stage advance, deposit a
`pipeline_update` **notify** card to the Action Queue (it auto-applies into the
queue with a one-click undo record) AND write the new stage to `context/pipeline.md`.
The card is the reversible audit record; the producer is the pipeline writer
(the daemon never invents pipeline state). Deposit DAEMON-FREE (the queue is
terminal-native since 2026-06-27) via the in-process helper, passing the DATA
root (the queue lives under `get_data_root()`, NOT the engine root):
`scripts/bridge_daemon/sources/action_queue.append_cards(get_data_root(), [card])`
— or from a shell, `python scripts/action-queue.py deposit --file <cards.json>`.

```python
card = {
    "action_type": "pipeline_update",            # -> notify tier (tool_risk.tier_for)
    "title": f"{route}: {company} {current_stage} -> {new_stage}",
    "reasoning": f"{why}. Email: {subject_or_thread}",
    "priority": "P1",                            # pipeline moves are revenue-impacting
    "source": "email-intel",
    "citations": [{"source": email_ref, "excerpt": stage_signal_text}],
    "company": company,                          # dedup key
    "prev_value": {"stage": current_stage, "stage_date": current_stage_date},  # STAMP BEFORE applying -> undo restores this
    "applied_value": {"stage": new_stage, "stage_date": today},                # documentation
}
```

Contract: stamp `prev_value` BEFORE editing pipeline.md (a card with no
`prev_value` makes undo a safe no-op but loses the revert intent). Dedup is by
`company`; a pending/approved card for the same company is skipped, and a
dismissed pipeline card inside the 14-day cooldown suppresses re-proposal.
Ambiguous advances never use this card -- surface a `note` card instead.

## Knowledge Note

Create in appropriate `knowledge/{type}/` subdirectory.

Frontmatter fields:

- `id` - YYYYMMDDHHMMSS (timestamp at creation)
- `title` - one-line distilled headline
- `type` - matches the subdirectory (signal | research | insight | decision)
- `keywords` - 3-6 lowercase tags
- `status: seed`
- `created: YYYY-MM-DD`

Body must reference source email subject and date.
