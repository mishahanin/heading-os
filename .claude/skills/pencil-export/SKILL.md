---
name: pencil-export
description: "Export a Pencil (.pen) deck to PNG + PDF + a self-contained HTML on WSL, where the Pencil MCP export_nodes tool is broken. PPTX defaults to an EDITABLE twin (identical look, native editable text, brand fonts embedded); the image-per-slide locked-look PPTX is opt-in via pptx-flat. Two steps: the agent calls the export_html MCP tool to dump all frames to one HTML, then scripts/pencil-export.py renders each frame in isolation via chromium and assembles the formats. Use when asked to export/convert a Pencil deck to slides/PDF/PPTX, or to make an editable version of a Pencil deck. Do NOT use for MARP markdown decks (use /marp) or for a brand PPTX authored from scratch (use /pptx-generator)."
argument-hint: "<path-to.pen> [--formats png,pdf,html,pptx,pptx-flat]"
allowed-tools: "Read, Write, Bash(python3:*), mcp__pencil__get_editor_state, mcp__pencil__export_html, mcp__pencil__snapshot_layout"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-heading-orchestration:
  parallel_safe: false
  shared_state: ["outputs/"]
  triggers:
    - "export pencil deck"
    - "export the pen deck"
    - "pencil deck to pdf"
    - "convert .pen to pdf"
    - "render pencil deck"
x-heading-capability:
  what: >
    Exports a Pencil .pen deck to per-slide PNG, a 16:9 PDF, an EDITABLE PPTX (the
    default: text-less background image plus native, editable text boxes at matching
    coordinates, with the brand fonts embedded so it renders identically anywhere),
    and a portable self-contained HTML, working around the broken Pencil MCP
    export_nodes tool on WSL. The locked-look image-per-slide PPTX is opt-in via
    --formats pptx-flat.
  how: >
    Ensure the .pen is the active editor in the Pencil desktop app, then run the
    skill. It calls export_html to dump all frames to one HTML and runs
    scripts/pencil-export.py to render each frame in isolation and build the
    formats into an export/ folder next to the deck.
  when: >
    Use to export or convert a Pencil .pen deck. For MARP markdown decks use
    /marp; for editable brand PPTX authored from scratch use /pptx-generator.
x-heading-routing:
  category: Design
  triggers:
    - export pencil deck
    - export the .pen deck
    - pencil deck to pdf
    - convert .pen to pdf
    - render pencil deck
    - editable version of a pencil deck
    - same-look editable pptx from a .pen
    - shareable flat pptx of a .pen
  exclusions:
    - MARP markdown deck -> /marp
    - editable brand PPTX authored from scratch -> /pptx-generator (a Pencil deck's PPTX is editable by default here, brand fonts embedded
    - the locked-look image deck is opt-in via `--formats pptx-flat`)
    - native Pencil export_nodes on WSL is broken (this is the workaround)
  compound: 'No'
  router: auto
---
# /pencil-export - Export a Pencil deck (WSL-safe)

The Pencil MCP `export_nodes` tool (native PNG/PDF) is broken on this WSL setup:
its bundled path translator prepends `\\wsl.localhost\<distro>\` inconsistently,
so it cannot write per-slide images from a WSL-resident or C:\ `.pen`. The Pencil
CLI would fix it but its headless loader chokes on relative image URLs and its
shell is not pipe-scriptable, and `--app desktop` has no WSL socket. `export_html`
in the same MCP resolves paths correctly, so this skill uses it as the seam.

Background and full diagnosis: auto-memory `pencil-export-nodes-broken-wsl`.

## Phase 0 - Context

- Confirm the target `.pen` is **open and active** in the Pencil desktop app
  (`get_editor_state` returns it as the active editor). `export_html` only exports
  the active editor's document.
- Locate the deck directory. Output lands in `<deck-dir>/export/`.
- Brand fonts: `datastore/brand/fonts/` on the CEO workspace (GT Standard + 31C
  Horizontal). Pass this via `--fonts-dir`.

## Phase 1 - Dump frames to HTML (MCP)

1. `get_editor_state({include_schema:false})` - record the active editor's
   **registered filePath** and the frame node IDs.
2. Determine **deck order** of the slide frames. `get_editor_state` lists top-level
   nodes but not in reading order; use `snapshot_layout({maxDepth:0})` and sort the
   `Slide-*` frames by canvas `y` (then `x`) to recover reading order, or use a
   known ordered ID list.
3. Call `export_html` with the ordered node IDs, writing INTO the deck's `pencil/`
   dir so relative image fills (`images/...`, `../assets/...`) resolve:

   ```
   export_html(
     filePath=<registered active path, e.g. /Ubuntu-24.04/home/.../deck.pen>,
     outputPath=<deck-dir>/pencil/deck.html,
     nodeIds=[<frames in deck order>],
     format="html-css")
   ```

## Phase 2 - Render + assemble (script)

Run the renderer. It resolves/embeds fonts, renders each `Slide-*` frame in
isolation (hiding siblings so overlapping absolutely-positioned frames cannot
bleed - a real failure mode), and builds the formats:

```bash
python scripts/pencil-export.py \
  --html <deck-dir>/pencil/deck.html \
  --out-dir <deck-dir>/export \
  --fonts-dir <path>/datastore/brand/fonts \
  --stem <deck-slug> \
  --formats png,pdf,pptx,html
```

Flags: `--width/--height` (default 1920x1080), `--scale` (default 2),
`--formats` (subset of `png,pdf,html,pptx,pptx-flat`). Requires `playwright`
(chromium) and `python-pptx` in the venv.

**PPTX defaults to editable.** The `pptx` token now builds the editable twin
`<stem>.pptx` (Phase 2b), because editability is the whole point of a PPTX. The
locked-look image-per-slide deck is opt-in via `pptx-flat` (alias `pptx-image`) and
is written as `<stem> (ready to be shared with the world).pptx` - byte-frozen and
portable (needs no fonts installed). `editable` remains an accepted alias of `pptx`.
`pdf` is always image-per-slide.

**Compression (no NXPowerLite needed).** Lossless PNG at 2x makes a heavy deck
(45-slide PPTX ~29 MB, PDF ~45 MB). Add `--image-format jpeg` (with `--quality`,
default 85, and `--scale 1` for 1920x1080) to render slides as JPEG - chromium
encodes JPEG natively. That drops the PPTX and PDF to a few MB in one pass, the
same result NXPowerLite gives by hand. Reference points on the ODUN deck: `--scale 1
--quality 82` -> PPTX ~3.8 MB, PDF ~4.5 MB (vs 29 MB / 45 MB lossless); `--width
1600` or lower `--quality` shrink further. Use lossless PNG for print masters, JPEG
for anything shared or emailed.

## Phase 2b - Editable PPTX (the default `pptx`)

`pptx` builds the editable deck `<stem>.pptx`: each slide is the exact Pencil render
used as a full-bleed **background image with the content text removed**, plus
**native editable text boxes** laid on top at the same coordinates, with matching
brand font, size, colour and alignment. Branding, graphics, images and table grids
stay baked in the background. The brand typefaces used on the runs are **embedded
into the file** so it renders identically on a machine without the fonts installed.

```bash
python scripts/pencil-export.py \
  --html <deck-dir>/pencil/deck.html \
  --out-dir <deck-dir>/export \
  --fonts-dir <path>/datastore/brand/fonts \
  --stem <deck-slug> \
  --formats pptx \
  --image-format jpeg --quality 82 --scale 1
```

How it works and what to know:

- **Decor vs content.** Text inside a component named `Footer`, `Logo`,
  `OrangeCorner`, a watermark, an icon or a number badge stays baked in the
  background (branding, not editable). Everything else becomes a native text box.
  Add deck-specific branding names with `--keep-in-bg <Name>` (repeatable).
- **Coordinate map.** The slide is 1920x1080 px == 13.333x7.5 in, so a text box
  position is `px * 12192000/width` EMU and its font size is `px * (12192000/width)/12700`
  pt. Single-line boxes are set no-wrap (a renderer whose brand-font metrics run
  wider must not break the last word onto a second line); multi-line boxes keep the
  extracted line-height.
- **Overlap-safe.** Each background is rendered with all other slide frames hidden,
  so off-grid / overlapping Pencil frames cannot bleed into a neighbour's background.
- **Fonts embedded automatically.** When `--fonts-dir` is given, `embed_fonts()`
  adds the used typefaces to the .pptx package (the PowerPoint "Embed fonts in the
  file" structures: a fntdata content-type, one font part + relationship per
  typeface, and a schema-ordered `<p:embeddedFontLst>` with `embedTrueTypeFonts`).
  **Only TTF/OTF embed** - PowerPoint cannot use woff/woff2, so a typeface present in
  the fonts dir only as woff is reported as "no TTF/OTF for typeface X" and falls
  back on the opening machine. The script never round-trips through LibreOffice (that
  would drift the layout); it edits the OPC package directly.
- **Locked-look flat deck is opt-in.** For a byte-frozen, portable, needs-no-fonts
  version, add `pptx-flat` (alias `pptx-image`); it writes `<stem> (ready to be
  shared with the world).pptx`, an image-per-slide deck (like the PDF, not editable).
  Editable `<stem>.pptx` and flat `<stem> (ready...).pptx` are siblings.
- **Verify** by opening the .pptx with python-pptx (validates the package) and, when
  a real render is needed, in PowerPoint itself. LibreOffice headless conversion is
  unreliable on this WSL setup (see auto-memory `libreoffice-headless-pdf-fails-wsl`),
  so do not depend on `soffice --convert-to pdf` for the check.

## Phase 3 - Verify

- Slide count == frame count; PDF pages == slides; PPTX slides == slides.
- The self-contained HTML has **zero external refs** (`grep -c 'src="assets\|url(.\(assets\|images\)' <stem>.html` -> 0).
- Spot-check the cover, the closing slide, and any dense/overlap-prone slide by
  reading the PNGs. If a slide shows two slides' content merged, that is the Pencil
  canvas-overlap defect; the script's isolation already handles the export, and the
  `.pen` can be fixed by moving one frame to a free canvas row (`Update` its x/y).
- Report: formats produced, slide count, any overlap warnings the script printed.

## NEVER

- Never rely on Pencil MCP `export_nodes` on WSL - it is broken; use this flow.
- Never place the HTML outside the deck's `pencil/` dir before rendering, or the
  relative image fills will not resolve.
- Never hardcode brand-font paths in the engine script - pass `--fonts-dir`.
- Never overwrite an existing final `export/` set while testing; render to a temp
  dir first when experimenting.
