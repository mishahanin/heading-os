# Layout CRUD Operations

Create, edit, update, and delete cookbook layout templates.

## Creating New Layouts

When user requests a new layout type:

1. **Study existing layouts for patterns:**
   ```
   Glob: .claude/skills/pptx-generator/cookbook/*.py
   ```
   Read 2-3 layouts to understand:
   - Code structure and imports
   - How brand variables are used
   - Decorative element patterns
   - Positioning conventions

2. **Design with these quality standards:**

   **MUST be production-ready:**
   - Professional, polished appearance
   - Visually engaging (not plain or generic)
   - Distinctive decorative elements
   - Strong visual hierarchy
   - Proper use of whitespace

   **Use appropriate elements:**
   - **Charts** - Pie, doughnut, bar, column for data visualization
   - **Images** - Placeholder shapes for screenshots, photos
   - **Shapes** - Circles, rectangles, parallelograms for visual interest
   - **Cards** - Floating cards with shadows for depth
   - **Geometric patterns** - Bold shapes anchored to corners/edges

   **Avoid:**
   - Plain text-only layouts
   - Generic bullet points without styling
   - Tiny decorative elements that don't make impact
   - Centered-everything boring compositions

3. **Write the layout file with detailed frontmatter:**

   **CRITICAL: The frontmatter is documentation for future AI agents.**

   Every layout MUST include comprehensive frontmatter that teaches future AI agents:
   - WHEN to use this layout (and when NOT to)
   - HOW to use it correctly
   - WHAT limits and constraints exist
   - WHY certain choices matter

   ```python
   #!/usr/bin/env -S uv run
   # /// script
   # requires-python = ">=3.11"
   # dependencies = [
   #     "python-pptx==1.0.2",
   # ]
   # ///
   # /// layout
   # name = "layout-name"
   # purpose = "When to use this layout - be specific"
   # best_for = [
   #     "Ideal use case 1",
   #     "Ideal use case 2",
   # ]
   # avoid_when = [
   #     "Situation to avoid 1 - and what to use instead",
   #     "Situation to avoid 2 - and what to use instead",
   # ]
   # max_items = 5  # or other relevant limits
   # instructions = [
   #     "Specific tip 1",
   #     "Specific tip 2",
   # ]
   # ///
   """
   LAYOUT: [Name]
   PURPOSE: [When to use this layout - be specific]

   CUSTOMIZE:
   - [List customizable elements]
   """

   # ... implementation
   ```

   **Required frontmatter fields (be DETAILED and SPECIFIC):**

   | Field | Description | Example |
   |-------|-------------|---------|
   | `name` | Layout identifier (matches filename) | `"multi-card-slide"` |
   | `purpose` | Clear one-line description | `"Multiple items as cards in a row, 3-5 cards"` |
   | `best_for` | **Detailed** array of ideal scenarios | `["Exactly 3 related features", "Process with 3 steps"]` |
   | `avoid_when` | **Specific** situations with alternatives | `["More than 3 items - use multi-card-slide instead"]` |
   | `instructions` | **Actionable** tips for correct usage | `["Card titles must be SHORT: 1-2 words, max 15 chars"]` |

   **Optional but recommended fields:**
   | Field | Description | Example |
   |-------|-------------|---------|
   | `max_*` / `min_*` | Hard limits on items | `max_cards = 3`, `min_surrounding_items = 4` |
   | `*_max_chars` | Character limits for text | `card_title_max_chars = 15` |

   **Writing good frontmatter:**

   DO: Be specific and actionable
   ```python
   # avoid_when = [
   #     "More than 3 items - use multi-card-slide instead",
   #     "Long card titles (over 15 characters) - abbreviate or use content-slide",
   # ]
   # instructions = [
   #     "EXACTLY 3 cards required - no more, no less",
   #     "Card titles must be SHORT: 1-2 words, max 15 characters",
   #     "If titles are too long, abbreviate or use different layout",
   # ]
   ```

   DON'T: Be vague or unhelpful
   ```python
   # avoid_when = ["Too many items", "Wrong content"]
   # instructions = ["Use correctly", "Follow the pattern"]
   ```

   **Think of frontmatter as teaching a colleague** - what would they need to know to use this layout correctly without asking you questions?

4. **Save to cookbook:**
   ```
   .claude/skills/pptx-generator/cookbook/{layout-name}-slide.py
   ```

5. **Test by generating** a sample with the new layout

## Editing Existing Layouts

1. **Find the layout:**
   ```
   Glob: .claude/skills/pptx-generator/cookbook/*{name}*.py
   ```

2. **Read and understand current structure** including the frontmatter

3. **Make modifications** while preserving:
   - The script header format
   - Brand variable naming conventions
   - Docstring format (LAYOUT, PURPOSE, CUSTOMIZE)

4. **Update the frontmatter** if your changes affect:
   - What the layout is best for (`best_for`)
   - When to avoid it (`avoid_when`)
   - Item limits (`max_*`, `min_*`)
   - Usage instructions (`instructions`)

5. **Save back to the same file**

6. **Test the modified layout**

## Updating/Improving Layouts

When asked to improve layout quality:

1. **Analyze current weaknesses:**
   - Is it visually engaging?
   - Does it have enough decorative elements?
   - Is there good visual hierarchy?
   - Does it use space well?

2. **Apply improvements:**
   - Add bold geometric shapes
   - Improve color usage
   - Add depth (shadows, overlapping)
   - Better typography sizing
   - More distinctive decorative elements

3. **Preserve functionality** - Don't break what works

4. **Review and enhance frontmatter:**
   - Are `best_for` and `avoid_when` still accurate?
   - Do `instructions` reflect any new constraints?
   - Add any lessons learned from the improvements
   - Update limits if element sizes/counts changed

## Deleting Layouts

Simply remove the file:
```bash
rm .claude/skills/pptx-generator/cookbook/{layout-name}.py
```
