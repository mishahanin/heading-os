# Generation & Combining Workflow

> Reference for batch generation, quality validation, output handling, and combining PPTX batches.

---

## Batch Generation Rules

**MAXIMUM 5 SLIDES PER BATCH. This is a hard limit.**

1. Generate 1-5 slides in a single PPTX file
2. **STOP and review the output** before generating more
3. Only after validation passes, continue with the next batch
4. Repeat until all slides are generated

**Why batching matters:** Prevents token limit errors, allows quality checks, catches issues early.

---

## Background Bug Fix (CRITICAL)

**EVERY slide MUST have its background explicitly set.** Without this, slides use PowerPoint's default WHITE background — making text unreadable on dark-themed brands.

**Mandatory for every slide:**
```python
slide = prs.slides.add_slide(prs.slide_layouts[6])
slide.background.fill.solid()  # ← REQUIRED
slide.background.fill.fore_color.rgb = hex_to_rgb(BRAND_BG)  # ← REQUIRED
```

This is especially critical when:
- Generating multiple batches (each batch is a new Presentation object)
- Using helper functions to create slides
- Combining separate PPTX files

---

## Execution Methods

**Resolve the DATA-overlay deck directory first.** The generated PPTX is a DATA artifact -- it must
land in the data overlay, never the engine tree. The heredoc runs under `uv run --with python-pptx`,
an ephemeral env that may lack the workspace's deps, so resolve the path in plain `python3` (full env)
in bash and pass it into the heredoc via an env var (`$DECK_DIR`). A bare `output/{brand}` relative
path would resolve into the engine root:

```bash
cd "$(git rev-parse --show-toplevel)"
OUTPUTS_DIR="$(python3 -c "import sys; sys.path.insert(0,'.'); from scripts.utils.workspace import get_outputs_dir; print(get_outputs_dir())")"
export DECK_DIR="$OUTPUTS_DIR/content/decks/{brand-name}"   # substitute the brand folder name
mkdir -p "$DECK_DIR"
```

Inside the heredoc read it with `os.environ["DECK_DIR"]` (keeps the heredoc body quoted so brand
values are not shell-expanded).

**PREFERRED: Use heredoc (no files created):**
```bash
uv run --with python-pptx==1.0.2 python << 'EOF'
import os
from pathlib import Path
output_dir = Path(os.environ["DECK_DIR"])   # data-overlay deck dir resolved above
output_dir.mkdir(parents=True, exist_ok=True)
# [Adapted code with brand values and content; save parts/final under output_dir]
EOF
```

**IF heredoc fails (Windows): Use temp directory:**
```bash
cd "$(git rev-parse --show-toplevel)"  # anchor at root -- the paths below are root-relative
mkdir -p .claude/skills/pptx-generator/.tmp
# Write script to .claude/skills/pptx-generator/.tmp/gen.py
uv run --with python-pptx==1.0.2 python .claude/skills/pptx-generator/.tmp/gen.py
# MANDATORY: Clean up immediately
rm .claude/skills/pptx-generator/.tmp/gen.py
```

**CRITICAL: Never create Python files in the repository root.**

---

## Quality Validation (MANDATORY after every batch)

Check for these common issues:

| Issue | What to Look For | Fix |
|-------|------------------|-----|
| White background | Slide has white bg instead of brand color | Add slide.background.fill.solid() |
| Duplicate titles | Same title text appearing twice | Remove duplicate text boxes |
| Spacing problems | Title too close to content | Increase Y position of lower elements |
| Text overflow | Content beyond slide bounds | Reduce font size or split content |
| Missing elements | Decorative elements not rendering | Check shape positions and colors |
| Wrong colors | Colors not matching brand | Verify hex values (no # prefix) |
| Bad punctuation | Trailing periods/commas on titles | Remove unnecessary punctuation |

If issues found: fix before continuing. If validation passes: proceed to next batch.

---

## Output Configuration

Use settings from config.json:

| Config Setting | Default | Description |
|----------------|---------|-------------|
| `output.directory` | `content/decks/{brand}` (under the DATA overlay) | Where to save files |
| `output.naming` | `{name}-{date}` | File naming pattern |
| `output.keep_parts` | `false` | Keep part files after combining |

**Resolve placeholders:** `{brand}` → brand folder name, `{name}` → presentation name, `{date}` → YYYY-MM-DD

**Path resolution:** the output directory always resolves under the DATA overlay via `$DECK_DIR`
(`get_outputs_dir()/content/decks/{brand}`, see Execution Methods). A relative `output/...` would land
in the engine tree -- never save there. If a brand's `config.json` carries an absolute path, use it;
otherwise default to `$DECK_DIR`.

**Batched workflow:**
1. Generate each batch as `{name}-part1.pptx`, `{name}-part2.pptx`, etc.
2. Validate each batch
3. After ALL batches: combine into final file
4. Delete part files

---

## Combining Batches

**CRITICAL BUG: BACKGROUND MUST BE SET WHEN COMBINING**

When combining, `add_slide()` creates slides with DEFAULT WHITE BACKGROUNDS. Shape copying does NOT copy the slide background. You MUST set the background immediately after creating each new slide.

```python
from pptx import Presentation
from pptx.dml.color import RGBColor
from pathlib import Path
import os, shutil, copy

def hex_to_rgb(hex_color: str) -> RGBColor:
    h = hex_color.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

BRAND_BG = "REPLACE_WITH_BRAND_BACKGROUND"  # from brand.json

output_dir = Path(os.environ["DECK_DIR"])  # data-overlay deck dir (see Execution Methods)
part_files = sorted(output_dir.glob("{name}-part*.pptx"))

if len(part_files) > 1:
    combined = Presentation(str(part_files[0]))

    for part_file in part_files[1:]:
        part_prs = Presentation(str(part_file))
        for slide in part_prs.slides:
            new_slide = combined.slides.add_slide(combined.slide_layouts[6])

            # CRITICAL: Set background IMMEDIATELY
            new_slide.background.fill.solid()
            new_slide.background.fill.fore_color.rgb = hex_to_rgb(BRAND_BG)

            # Copy all shapes
            for shape in slide.shapes:
                el = copy.deepcopy(shape.element)
                new_slide.shapes._spTree.append(el)

    combined.save(output_dir / "{name}-final.pptx")

    # MANDATORY: Clean up part files
    for part_file in part_files:
        part_file.unlink()
else:
    shutil.move(str(part_files[0]), str(output_dir / "{name}-final.pptx"))
```

**Why this bug happens:**
- `add_slide()` creates a NEW slide with PowerPoint's default white background
- `shapes._spTree` copies shapes but NOT the background (it's a slide property, not a shape)
- Without explicit background setting, added slides will be white

**Testing after combining:**
- Open the combined PPTX
- Scroll through ALL slides (not just the first few)
- Verify EVERY slide has the correct background color
