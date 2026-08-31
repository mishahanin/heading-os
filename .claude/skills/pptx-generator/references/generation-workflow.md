# Generation & Combining Workflow

Consumed by: `.claude/skills/pptx-generator/SKILL.md`, section "Step 5: Generate, Validate & Combine".

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

**The cookbook templates enforce this.** Each one resolves `$DECK_DIR` at the top of `main()` and
exits non-zero when it is unset. Until 2026-08-31 each ended with `output = Path("title-slide.pptx")`,
a bare relative name that resolves against the CWD. A run from the engine clone dropped a `.pptx`
into the root of a public repository, and `config/routing-map.yaml` routes a root-level `.pptx` as
`engine`, so `tests/test_engine_tree_clean.py` did not flag it. Keep the `deck_dir` block when you
copy a layout; the trap lived in the template, not in the caller.

Every template also runs standalone through its `uv run` shebang. It could not before the same date:
PEP 723's reference regex is greedy over its content group and `# ///` matches that group's `# .*`
alternative, so the `# /// script` block ran past its terminator into the `# /// layout` block below
it. Twenty-one of twenty-two files failed with `TOML parse error at line 5, column 4 | ///`. A blank
line between the two blocks fixes it, because the content group needs every line to begin with `#`.
Keep that blank line when you add a layout.

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

**Do not hand-roll the combine. Run the script.**

```bash
python3 .claude/skills/pptx-generator/scripts/combine_decks.py \
  --parts "$DECK_DIR/{name}-part*.pptx" \
  --out   "$DECK_DIR/{name}-final.pptx" \
  --background REPLACE_WITH_BRAND_BACKGROUND \
  --delete-parts
```

`--background` is the brand background hex from `brand.json`, without the `#`.
`--delete-parts` performs step 4 of the batched workflow. A single part file is
copied straight through.

*Part order.* The script sorts by the number in each filename, not lexically.
The names above are unpadded, batches cap at 5 slides, and a deck over 45 slides
therefore reaches ten parts, where `sorted()` returns `part1, part10, part11,
part2 ...`. Until 2026-08-31 that was the order the merge received, so a
55-slide deck shipped with slides 46 to 55 sitting between slide 5 and slide 6.
A part file whose name carries no number sorts after every numbered one; it is
never dropped and never raises.

**Two things a naive combine loses, both of them silently.**

*Slide background.* `add_slide()` creates a slide with PowerPoint's default
white background, and copying shapes does not carry it - the background is a
slide property, not a shape. The script sets it on every slide it appends.

*Charts and pictures.* This one is newer, found by the 2026-08-23 audit. The
loop this section used to prescribe was:

```python
el = copy.deepcopy(shape.element)
new_slide.shapes._spTree.append(el)   # WRONG for charts and pictures
```

A chart lives in its own package part; a picture lives in `ppt/media/`. The
shape XML holds only a relationship id pointing at them, so copying the element
into a slide whose part has no such relationship leaves the id dangling.
Measured on python-pptx 1.0.2 with one chart slide and one picture slide:

```
charts in package : []
media in package  : []
slide 2 chart   -> KeyError "no relationship with key 'rId2'"
slide 3 picture -> KeyError "no relationship with key 'rId2'"
```

Zero chart parts and zero media parts in the combined file; in PowerPoint those
slides render blank or broken. The cookbook ships `chart-slide.py` and
`image-caption-slide.py` and batching is MANDATORY over five slides, so this hit
the ordinary path. `combine_decks.py` walks every relationship-namespace
attribute in the copied XML, re-attaches the target part to the destination, and
writes back the new id - charts, their embedded workbook, and images all
survive.

**Testing after combining:**
- Open the combined PPTX
- Scroll through ALL slides (not just the first few)
- Verify EVERY slide has the correct background color
- Verify every chart still plots and every image still renders
