---
name: dashboard
description: "Build the daily CEO morning dashboard - one page over CRM health, pipeline, calendar, email and data freshness. Use for the morning briefing. For the end-of-week review use /weekly-review; for one function's health use /state-check."
argument-hint: "(no arguments)"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
context: fork
allowed-tools: "Bash(python3:*), Read"
model: sonnet
x-heading-orchestration:
  parallel_safe: partial
  shared_state:
    - outputs/operations/dashboard/
  triggers:
    - dashboard
    - morning dashboard
    - daily brief
    - bridge view
x-heading-capability:
  what: >
    Generates the daily CEO Morning Dashboard - a single-page HTML + PDF briefing
    aggregating CRM health, pipeline, calendar, email, strategy, and data
    freshness.
  how: >
    Type /dashboard (no arguments). It runs scripts/generate-dashboard.py --pdf,
    writes to outputs/operations/dashboard/YYYY-MM-DD/, sanitizes the output, and
    reports urgent items, today's meetings, and a pipeline snapshot.
  when: >
    Use for the morning operational briefing. For a full context load at session
    start use /prime; for the logical next action use /next; for the end-of-week
    review use /weekly-review.
x-heading-routing:
  category: Operations
  triggers:
    - dashboard
    - morning dashboard
    - daily brief
    - bridge view
  exclusions:
    - Full prime -> /prime
  compound: 'No'
  router: auto
---
# CEO Morning Dashboard

Generate the daily CEO Morning Dashboard -- a single-page operational briefing aggregating CRM health, pipeline, calendar, email, strategy, and data freshness.

## Trigger Phrases
"dashboard", "morning dashboard", "morning brief", "daily brief", "bridge view", "daily dashboard"

## Execution

### Step 1: Generate the dashboard

Run the generation script:

```bash
python scripts/generate-dashboard.py --pdf
```

This reads all workspace data sources and produces:
- `outputs/operations/dashboard/YYYY-MM-DD/morning-dashboard.html`
- `outputs/operations/dashboard/YYYY-MM-DD/morning-dashboard.pdf`

### Step 2: Validate hidden characters

Bash gets no path redirect, so resolve the outputs root first.

```bash
OUTPUTS_DIR="$(python3 -c "import sys; sys.path.insert(0,'.'); from scripts.utils.workspace import get_outputs_dir; print(get_outputs_dir())")"
python scripts/sanitize-text.py "$OUTPUTS_DIR/operations/dashboard/YYYY-MM-DD/morning-dashboard.html" --scan
```

### Step 3: Report to Misha

Summarize key findings from the dashboard:
- Number of urgent items (RED contacts, overdue commitments)
- Today's meeting count
- Pipeline snapshot (active deals, won, investors)
- Any stale data files needing refresh

Format: concise 3-5 bullet summary with file paths.

After the hidden-characters confirmation line, append a one-line branding confirmation:
`Branding: 31C corporate (dark cover, GT Standard, orange corner, blue accents).`

The GT Standard fonts or the canonical brand logos can fail to embed, when a
file is missing from `datastore/brand/`. Surface that explicitly instead of a
silent fall back to Inter.

### Optional: Live Sea State Enrichment

If Misha requests a live sea state update, run a quick WebSearch for:
- "DPI deep packet inspection" latest news
- "telecom cybersecurity" latest developments
- Key regions ([priority regions]) telecom news

Add a brief 2-3 sentence sea state note to the summary.

## Data Sources

| Source | File | What It Provides |
|--------|------|-----------------|
| CRM Health | `scripts/crm-health.py --json` | Contact health scores, commitments |
| Pipeline | `context/pipeline.md` | Active deals, investors, partnerships |
| Calendar | `outputs/_sync/calendar/upcoming.md` | Today's meetings |
| Email | `outputs/_sync/emails/inbox-latest.md` | Latest inbox |
| Strategy | `context/strategy.md` | Heading, priorities, phase |
| Metrics | `context/current-data.md` | Headcount, product, market data |
| Freshness | `context/*.md` headers | Data age tracking |
| Capture Payoff (R10) | `knowledge/**` + `scripts/odin-cadence.py --json` | Signals captured in the last 7 days + episode clusters ripe to promote to an Odin principle. CEO-only: the panel auto-hides on a workspace with no Odin brain. |

## Output Location

`outputs/operations/dashboard/YYYY-MM-DD/morning-dashboard.html` (+ `.pdf`)
