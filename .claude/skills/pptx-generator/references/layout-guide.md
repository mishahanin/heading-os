# Layout Selection Guide

> Reference for selecting and using cookbook layouts. Read this when generating presentation slides.

---

## Layout Frontmatter Fields

Every cookbook layout `.py` file starts with a `# /// layout` frontmatter block in the first 40 lines:

```python
# /// layout
# name = "floating-cards-slide"
# purpose = "Feature highlights, process steps, multiple equal items with depth"
# best_for = [
#     "Exactly 3 related features or concepts",
#     "Process with 3 steps",
# ]
# avoid_when = [
#     "More than 3 items - use multi-card-slide instead",
#     "Long card titles (over 15 characters)",
# ]
# max_cards = 3
# card_title_max_chars = 15
# instructions = [
#     "EXACTLY 3 cards required - no more, no less",
#     "Card titles must be SHORT: 1-2 words, max 15 characters",
# ]
# ///
```

**Key fields:**

| Field | Description |
|-------|-------------|
| `name` | Layout identifier |
| `purpose` | What this layout is for |
| `best_for` | Ideal use cases (array) |
| `avoid_when` | When NOT to use this layout (array) |
| `max_*` / `min_*` | Item limits (cards, bullets, stats) |
| `instructions` | Specific tips for using this layout |

---

## Layout Selection Process

After reading ALL frontmatters:

1. User specifies layout → Use it (verify it fits content)
2. User describes content → Match to `best_for` criteria
3. Check `avoid_when` → Don't use a layout in situations it warns against
4. Respect limits → If content exceeds `max_*`, use a different layout
5. Multiple slides → Select appropriate layout for each
6. No good fit → Create a custom layout (Mode 3)

**Example:** User wants "5 pillars of AI infrastructure"
- `floating-cards-slide`: `max_cards = 3` → Won't work
- `multi-card-slide`: `max_cards = 5` → Perfect fit

**Why read ALL frontmatters?** Layouts reference each other in `avoid_when` (e.g., "use multi-card-slide instead"). You can't make the right choice without knowing all options.

---

## Visual-First Layout Selection (CRITICAL FOR VARIETY)

**DEFAULT TO VISUAL LAYOUTS. Content-slide is the LAST RESORT, not the default.**

### The Variety Problem

The biggest mistake is defaulting to content-slide (title + bullets) whenever you have information to convey. This creates repetitive, boring presentations.

### Variety Enforcement Rules

**HARD LIMITS:**
1. Never use the same layout more than 2-3 times consecutively
2. Content-slide should be <25% of total slides
3. Visual layouts (cards, stats, columns, hero, diagonal) should be 50%+ of slides
4. Section breaks are NOT variety — they're structural

### Decision Tree: "Should I Use content-slide?"

Ask these questions IN ORDER before defaulting to content-slide:

```
Do I have 3-5 equal items?
  YES → Use multi-card-slide

Do I have 2-4 big numbers/metrics?
  YES → Use stats-slide

Am I comparing two things?
  YES → Use two-column-slide

Do I have a central concept with surrounding items?
  YES → Use circular-hero-slide

Do I have exactly 3 related items?
  YES → Use floating-cards-slide

Do I have 1-3 words I want to emphasize dramatically?
  YES → Use giant-focus-slide or bold-diagonal-slide

Do I have a powerful quote or principle?
  YES → Use quote-slide

Is this the ONLY way to present this information?
  YES → NOW you can use content-slide
  NO → Go back through the decision tree
```

### Transforming Bullets Into Visual Layouts

**Example 1: "Validation Patterns"**

❌ **BAD (content-slide):**
```
Title: Validation Patterns
Bullets:
- Run comprehensive test suites
- Type checking and linting
- Code review by humans and AI
- Deployment previews
```

✅ **GOOD (multi-card-slide):**
```
Title: Validation Patterns
Cards:
1. Testing | Run comprehensive test suites after every change
2. Linting | Type checking and formatting as guardrails
3. Review | Human and AI code review process
4. Preview | Deployment previews for visual regression
```

**Example 2: "Why PIV Works"**

❌ **BAD (content-slide):** 4 bullets

✅ **GOOD (floating-cards-slide):** 3 cards (reduced from 4 to fit max_cards limit)

**Example 3: "Human-in-the-Loop Strategy"**

❌ **BAD (content-slide):** 4 bullets mixing two concepts

✅ **GOOD (two-column-slide):** Left: In-the-Loop / Right: On-the-Loop

### Active Visual Thinking

Before planning any slide:
1. "Can this be more visual?" — Almost always YES
2. "Have I used content-slide in the last 2 slides?" — If yes, use something else
3. "Does this slide look like the previous slide?" — If yes, change layout
4. "Am I falling into a pattern?" — Break it immediately

### When content-slide IS Appropriate

- You've genuinely tried all other layouts and they don't fit
- The information is inherently linear and textual (rare)
- You need a "breather" slide between two complex visuals
- You're at layout distribution limits

### Content Type → Best Layout

| Content Type | Best Layout | Why |
|--------------|-------------|-----|
| 3-5 equal features/steps | multi-card-slide | Cards create visual hierarchy |
| Exactly 3 featured items | floating-cards-slide | Elevated cards add depth |
| 2-4 metrics/KPIs | stats-slide | Big numbers grab attention |
| Before/after comparison | two-column-slide | Side-by-side shows contrast |
| Hub concept with types | circular-hero-slide | Radiating pattern shows relationships |
| Dramatic emphasis (1-3 words) | giant-focus-slide | Scale creates impact |
| High-energy warning | bold-diagonal-slide | Dynamic shapes convey urgency |
| Powerful quote/principle | quote-slide | Attribution adds authority |
| Process with steps | floating-cards-slide | Visual flow beats text |
| Technical comparison | two-column-slide | Structured comparison |

### Good Variety Distribution (30-slide presentation)

- Content-slide: 6-7 slides (20-23%)
- Section breaks: 5 slides (17%)
- Visual layouts: 15-16 slides (50-53%)
  - Multi-card: 3-4 / Two-column: 2-3 / Stats: 1-2 / Floating-cards: 2-3 / Circular-hero: 1-2 / Giant-focus/Bold-diagonal: 2-3 / Quote: 1
- Title/Closing: 2-3 slides (7-10%)
