---
name: pencil-export
description: "Export a Pencil (.pen) deck to PNG + PDF + PPTX + a self-contained HTML on WSL. PNG and PDF come from Pencil natively through the execute tool. PPTX defaults to an EDITABLE twin (identical look, native editable text, brand fonts embedded); the image-per-slide locked-look PPTX is opt-in via pptx-flat. The PPTX and self-contained HTML branches dump the frames to one HTML, then scripts/pencil-export.py renders each frame in isolation via chromium and assembles the formats. Pencil never receives a WSL path for writing: every export goes to a C: path and the result is collected from /mnt/c. Use when asked to export/convert a Pencil deck to slides/PDF/PPTX, or to make an editable version of a Pencil deck. Do NOT use for MARP markdown decks (use /marp) or for a brand PPTX authored from scratch (use /pptx-generator)."
argument-hint: "<path-to.pen> [--formats png,pdf,html,pptx,pptx-flat]"
allowed-tools: "Read, Write, Bash(python3:*), mcp__pencil__execute, mcp__pencil__get_app_state"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.2"
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
    and a portable self-contained HTML. PNG and PDF come from Pencil natively; PPTX
    and the self-contained HTML are assembled from an HTML dump. The locked-look
    image-per-slide PPTX is opt-in via --formats pptx-flat.
  how: >
    Open the .pen as the active editor in the Pencil desktop app, then run the
    skill. It calls the execute tool to run Export() into a C: staging directory,
    and, for the PPTX and HTML branches, runs scripts/pencil-export.py to render
    each frame in isolation and build the formats into an export/ folder next to
    the deck.
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
    - Pencil writes a WSL path to the wrong disk (this skill stages on C:)
  compound: 'No'
  router: auto
---
# /pencil-export - Export a Pencil deck (WSL-safe)

Pencil writes an export to the wrong disk when it receives a WSL path. The app
normalises `\\wsl.localhost\<distro>\...` to `/<distro>/...`, drops the prefix,
and resolves the remainder from the root of `C:`. It then reports success. A
requested write to `/Ubuntu-24.04/home/me/deck.html` lands at
`C:\Ubuntu-24.04\home\me\deck.html`, and nothing warns you.

So this skill stages every export on `C:` and collects the result from `/mnt/c`.
Never pass Pencil a WSL path for writing.

Pencil 42.5.0 exposes four MCP tools: `execute`, `get_app_state`, `browser`, and
`get_guidelines`. Export runs inside `execute`:

```js
Export(nodeIds, "png"|"jpeg"|"webp"|"pdf"|"html-tailwind"|"html-css", outputPath, options)
```

Native PNG and PDF are correct at 2x. Use them. The chromium renderer stays for
the editable PPTX and the self-contained HTML, which Pencil cannot produce.

Background and full diagnosis: auto-memory `pencil-export-nodes-broken-wsl`.

## Phase 0 - Context

- Open the target `.pen` as the active editor in the Pencil desktop app. `execute`
  fails with "wrong .pen file" when no document is active.
- Read the registered path with `get_app_state({include_schema:false,
  include_canvas_design:false, include_scripts_and_shaders:false})`. Pass that exact
  string as `filePath` on every `execute` call.
- The first `get_app_state` after a cold start can return no active editor. Retry
  it before you report a failure.
- Locate the deck directory. Output lands in `<deck-dir>/export/`.
- Brand fonts: `datastore/brand/fonts/` on the CEO workspace (GT Standard + 31C
  Horizontal). Pass this via `--fonts-dir`.

## Phase 1 - Select the slides and export

1. Collect the top-level frames and their names:

   ```js
   Print(JSON.stringify(Get((n,c)=>c.depth===0 && n.type==="frame"
     ? {id:n.id,name:n.name,x:n.x,y:n.y} : undefined)))
   ```

2. Keep the `Slide-*` frames only. A deck also holds component frames such as
   `Atom/Footer` and `Comp/StatCard`. Those are not slides and must not reach the
   export.
3. Sort the slide frames by canvas `y`, then `x`. This recovers reading order.
4. Export to a staging directory on `C:`, never to a WSL path:

   ```js
   Export(ids, "pdf", "C:/Users/<user>/AppData/Local/Temp/pencil-export/<slug>/pdf")
   Export(ids, "png", "C:/Users/<user>/AppData/Local/Temp/pencil-export/<slug>/png")
   ```

   PDF writes one multi-page `export.pdf` in node order. PNG writes one
   `<nodeId>.png` per frame at 2x.
5. The renderer finds each slide by its `data-pencil-id` attribute. `Export` omits
   that attribute unless you ask for it (`includeLayerIds` defaults to false), and
   the renderer then fails with `'NoneType' object has no attribute 'screenshot'`.
   Always pass `{includeLayerIds:true}` for the PPTX and HTML branches:

   ```js
   Export(ids, "html-css",
     "C:/Users/<user>/AppData/Local/Temp/pencil-export/<slug>/deck.html",
     {includeLayerIds:true})
   ```

6. Copy the exported HTML into the deck's `pencil/` dir so relative image fills
   (`images/...`, `../assets/...`) resolve.

   Pencil does not write the `assets/` directory that its HTML references. Supply
   the brand fonts through `--fonts-dir` in Phase 2.
7. Collect the staged files from `/mnt/c/...` and move them into
   `<deck-dir>/export/` with WSL tooling.

## Phase 2 - Render + assemble (script)

Run the renderer. It resolves and embeds fonts, then renders each `Slide-*` frame
in isolation. It hides siblings so overlapping absolutely-positioned frames cannot
bleed, a real failure mode. Last it builds the formats:

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
locked-look image-per-slide deck is opt-in via `pptx-flat` (alias `pptx-image`).
It is written as `<stem> (ready to be shared with the world).pptx`, byte-frozen
and portable, and needs no fonts installed. The `editable` token remains an
accepted alias of `pptx`. The `pdf` token is always image-per-slide.

**Compression (no NXPowerLite needed).** Lossless PNG at 2x makes a heavy deck
(45-slide PPTX ~29 MB, PDF ~45 MB). Add `--image-format jpeg` (with `--quality`,
default 85, and `--scale 1` for 1920x1080) to render slides as JPEG - chromium
encodes JPEG natively. That drops the PPTX and PDF to a few MB in one pass, the
same result NXPowerLite gives by hand. Reference points on the ODUN deck: `--scale 1
--quality 82` -> PPTX ~3.8 MB, PDF ~4.5 MB (vs 29 MB / 45 MB lossless); `--width
1600` or lower `--quality` shrink further. Use lossless PNG for print masters, JPEG
for anything shared or emailed.

## Phase 2b - Editable PPTX (the default `pptx`)

The `pptx` token builds the editable deck `<stem>.pptx`. Each slide is the exact
Pencil render, used as a full-bleed **background image with the content text
removed**. On top of it sit **native editable text boxes** at the same
coordinates, with matching brand font, size, colour and alignment. Branding,
graphics, images and table grids
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
  pt. Single-line boxes are set no-wrap, so a renderer whose brand-font metrics run
  wider cannot break the last word onto a second line. Multi-line boxes keep the
  extracted line-height.
- **Overlap-safe.** Each background is rendered with all other slide frames hidden,
  so off-grid / overlapping Pencil frames cannot bleed into a neighbour's background.
- **Fonts embedded automatically.** When `--fonts-dir` is given, `embed_fonts()`
  adds the used typefaces to the .pptx package. It writes the PowerPoint "Embed
  fonts in the file" structures. Those are a fntdata content-type, one font part
  and relationship per typeface, and a schema-ordered `<p:embeddedFontLst>` with
  `embedTrueTypeFonts`. **Only TTF/OTF embed.** PowerPoint cannot use woff or
  woff2. A typeface present in the fonts dir only as woff is reported as "no
  TTF/OTF for typeface X". It then falls back on the opening machine. The script
  never round-trips through LibreOffice, which would drift the layout; it edits
  the OPC package directly.
- **Locked-look flat deck is opt-in.** For a byte-frozen, portable, needs-no-fonts
  version, add `pptx-flat` (alias `pptx-image`). It writes `<stem> (ready to be
  shared with the world).pptx`, an image-per-slide deck (like the PDF, not editable).
  Editable `<stem>.pptx` and flat `<stem> (ready...).pptx` are siblings.
- **Verify** by opening the .pptx with python-pptx (validates the package) and, when
  a real render is needed, in PowerPoint itself. LibreOffice headless conversion is
  unreliable on this WSL setup (see auto-memory `libreoffice-headless-pdf-fails-wsl`),
  so do not depend on `soffice --convert-to pdf` for the check.

## Phase 3 - Verify

- Confirm each exported file exists on disk before you report success. Pencil
  reports "Exported <path>" even when it wrote the file to another disk. Treat the
  reported path as a claim, not as evidence.
- Slide count == frame count; PDF pages == slides; PPTX slides == slides.
- The self-contained HTML has **zero external refs** (`grep -c 'src="assets\|url(.\(assets\|images\)' <stem>.html` -> 0).
- Spot-check the cover, the closing slide, and any dense/overlap-prone slide by
  reading the PNGs. If a slide shows two slides' content merged, that is the Pencil
  canvas-overlap defect. The script's isolation already handles the export. To fix
  the `.pen`, move one frame to a free canvas row (`Update` its x/y).
- Report: formats produced, slide count, any overlap warnings the script printed.

## NEVER

- Never give Pencil a WSL path to write to. It reports success and writes the file
  to `C:\<distro>\...` instead. Stage on `C:` and collect from `/mnt/c`.
- Never trust Pencil's "Exported <path>" line on its own. Check the disk.
- Never send component frames (`Atom/*`, `Comp/*`) to the export. Slides only.
- Never export the HTML branch without `{includeLayerIds:true}`. The renderer has
  no other handle on a slide.
- Never place the HTML outside the deck's `pencil/` dir before rendering, or the
  relative image fills will not resolve.
- Never hardcode brand-font paths in the engine script - pass `--fonts-dir`.
- Never overwrite an existing final `export/` set while testing; render to a temp
  dir first when experimenting.
