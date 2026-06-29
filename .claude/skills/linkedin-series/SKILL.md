---
name: linkedin-series
description: LinkedIn Content Series
argument-hint: "[theme]"
allowed-tools: "Read"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.2"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - linkedin series
    - content series
    - plan posts for the week
    - 3 posts
x-31c-capability:
  what: >
    A multi-post LinkedIn content series plan in Misha's voice around one theme - series overview, per-post angle/hook/proof-point/hashtags, the narrative arc, plus a ready-to-publish draft of Post 1.
  how: >
    Run /linkedin-series [theme]. Keeps an append-only memlog at outputs/content/linkedin/[theme-slug]/.memlog.md so the plan survives across sessions.
  when: >
    Use to plan several connected posts around a strategic moment. For a single standalone post use /linkedin-post.
---
# LinkedIn Content Series

Plan a multi-post LinkedIn content series in Misha's voice around a theme or strategic moment.

## Variables

theme: [Core theme or strategic narrative — e.g., "sovereignty vs. compliance", "the DPI category we're creating", "what MWC taught us"]
posts: [Number of posts — default: 4]
timeframe: [When to publish — e.g., "leading up to MWC", "during launch week", "over 4 weeks"]
goal: [What this series should accomplish — e.g., "establish category leadership", "build investor intrigue", "Tribe culture signal"]

---

## Instructions

Before planning, read:
- `reference/misha-voice.md` — Voice guide including LinkedIn section
- `datastore/content/linkedin-archive/old-archive/goal-is-a-cage.md` — Voice and narrative example
- `context/strategy.md` — Strategic priorities to align content with
- `context/current-data.md` — Current milestones and proof points to reference

Produce a content series plan with:

**Series Overview:**
- Theme and why it matters now
- Strategic goal this series serves
- Audience (who we're talking to)

**For each post:**
- Post number and publish date
- Title / working concept
- Opening line (draft)
- Core angle and narrative arc (2-3 sentences)
- Key proof point or story to anchor it
- Hashtags
- How it connects to the next post in the series

**Series Arc:**
- Post 1: Hook / provocation (sets up the tension)
- Posts 2-N: Build evidence, story, proof
- Final post: Resolution / call to the future

After the plan, produce a ready-to-publish draft of Post 1.

---

## Session Memory (memlog)

A multi-post series is planned across turns. Keep an append-only working memory so the plan survives a context compaction and a later session can resume it.

- **On start:** if `outputs/content/linkedin/[theme-slug]/.memlog.md` is absent, `python scripts/memlog.py init --workspace outputs/content/linkedin/[theme-slug] --field topic="[theme]" --field mode=series`. If it already exists, do NOT re-run `init` — read it to resume, then `append`/`set`.
- **As you go:** record each settled angle, hook, or proof point — `python scripts/memlog.py append --workspace outputs/content/linkedin/[theme-slug] --text "post 2 anchors on the MWC line-rate demo" --type decision`.
- **On wrap-up:** `python scripts/memlog.py set --workspace outputs/content/linkedin/[theme-slug] --key status --value complete`.

The `.memlog.md` file is gitignored; the series plan is the deliverable derived from it.
