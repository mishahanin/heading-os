# LinkedIn Carousel Workflow

LinkedIn carousels are multi-page PDFs in square (1:1) format. Each page is a swipeable slide.

## Carousel vs Presentation

| Aspect | Presentation | Carousel |
|--------|--------------|----------|
| Dimensions | 16:9 (13.333" x 7.5") | 1:1 (7.5" x 7.5") |
| Layouts | `cookbook/*.py` | `cookbook/carousels/*.py` |
| Output | PPTX | PDF (via PPTX conversion) |
| Slides | 10-50+ typical | 5-10 optimal |
| Text size | Standard | Larger (mobile readable) |
| Content | Detailed | One idea per slide |

## Step 1: Brand Discovery

Same as slide generation - read brand.json, config.json, and tone-of-voice.md.

## Step 2: Carousel Layout Discovery

**Discover carousel-specific layouts:**
```
Glob: .claude/skills/pptx-generator/cookbook/carousels/*.py
```

**Available carousel layouts:**

| Layout | Purpose | Best For |
|--------|---------|----------|
| `hook-slide` | Opening attention-grabber | First slide only |
| `single-point-slide` | One key point with explanation | Body content |
| `numbered-point-slide` | Numbered list item with big number | Listicles, steps |
| `quote-slide` | Quote with attribution | Social proof, insights |
| `cta-slide` | Call to action | Last slide only |

Read frontmatters to understand limits and constraints for each.

## Step 3: Carousel Planning

**Typical carousel structure (5-10 slides):**

```markdown
| # | Layout | Content |
|---|--------|---------|
| 1 | hook-slide | Attention-grabbing hook |
| 2-8 | single-point or numbered-point | Body content |
| 9/10 | cta-slide | Call to action |
```

**Carousel content rules:**
- **One idea per slide** - Don't cram multiple points
- **Large text** - Must be readable on mobile
- **Short copy** - Max 50 chars for headlines, 150 for body
- **Clear flow** - Each slide should make sense if viewed alone
- **Strong hook** - First slide stops the scroll
- **Clear CTA** - Last slide tells them what to do

## Step 4: Generate Carousel

**Carousel dimensions (square 1:1):**
```python
prs.slide_width = Inches(7.5)
prs.slide_height = Inches(7.5)
```

Generate all slides as a single PPTX file (carousels are typically 5-10 slides, so batching rarely needed).

**Resolve the DATA-overlay deck dir first** (same as Mode 1 -- the carousel is a DATA artifact, never
the engine tree):
```bash
cd "$(git rev-parse --show-toplevel)"
OUTPUTS_DIR="$(python3 -c "import sys; sys.path.insert(0,'.'); from scripts.utils.workspace import get_outputs_dir; print(get_outputs_dir())")"
export DECK_DIR="$OUTPUTS_DIR/content/decks/{brand}"   # substitute brand folder
mkdir -p "$DECK_DIR"
```

**Execution** (read `os.environ["DECK_DIR"]` inside the heredoc; save the PPTX under it):
```bash
uv run --with python-pptx==1.0.2 python << 'SCRIPT'
# Carousel generation code with 7.5" x 7.5" dimensions; save to os.environ["DECK_DIR"]
SCRIPT
```

## Step 5: Export to PDF

LinkedIn requires PDF for carousel posts. Convert the PPTX to PDF:

**Option A: Using LibreOffice (recommended)**
```bash
libreoffice --headless --convert-to pdf --outdir "$DECK_DIR" "$DECK_DIR/carousel.pptx"
```

**Option B: Using soffice**
```bash
soffice --headless --convert-to pdf --outdir "$DECK_DIR" "$DECK_DIR/carousel.pptx"
```

**Note:** LibreOffice must be installed. On macOS: `brew install --cask libreoffice`

## Step 6: Output

Save both files under `$DECK_DIR` (the data-overlay deck dir):
- `$DECK_DIR/{name}-carousel.pptx` - Editable source
- `$DECK_DIR/{name}-carousel.pdf` - LinkedIn-ready

## Carousel Checklist

- [ ] Read brand configuration
- [ ] Read carousel layout frontmatters from `cookbook/carousels/`
- [ ] Plan carousel structure (hook -> body -> CTA)
- [ ] Keep text SHORT (check character limits in frontmatter)
- [ ] Use 7.5" x 7.5" dimensions
- [ ] Generate PPTX
- [ ] Validate output
- [ ] Export to PDF
- [ ] Test PDF in LinkedIn preview
