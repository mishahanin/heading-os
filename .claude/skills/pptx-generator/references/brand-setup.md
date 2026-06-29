# Brand Setup Guide

How to create a new brand configuration for the PPTX generator.

## Step 1: Read the Template

```
Read: .claude/skills/pptx-generator/brands/template/README.md
Read: .claude/skills/pptx-generator/brands/template/brand.json
Read: .claude/skills/pptx-generator/brands/template/config.json
```

## Step 2: Gather Brand Information

Ask the user for (or extract from provided materials):

| Required | Description |
|----------|-------------|
| **Brand name** | Folder name (lowercase, no spaces) |
| **Colors** | Background, text, accent colors (hex codes) |
| **Fonts** | Heading font, body font, code font |

| Optional | Description |
|----------|-------------|
| Output directory | Where to save generated files (default: `content/decks/{brand}`, resolved under the DATA overlay via `$DECK_DIR`) |
| Logo | Path to logo file (PNG/SVG) |
| Brand guidelines | Existing style guide or website to reference |
| Tone of voice | Writing style, vocabulary preferences |

## Step 3: Create Brand Files

1. **Create the brand folder:**
   ```bash
   mkdir -p .claude/skills/pptx-generator/brands/{brand-name}
   ```

2. **Create brand.json** with the gathered values:
   ```json
   {
     "name": "Brand Name",
     "description": "One-line description",
     "colors": {
       "background": "hex-without-hash",
       "background_alt": "hex-without-hash",
       "text": "hex-without-hash",
       "text_secondary": "hex-without-hash",
       "accent": "hex-without-hash",
       "accent_secondary": "hex-without-hash",
       "accent_tertiary": "hex-without-hash",
       "code_bg": "hex-without-hash",
       "card_bg": "hex-without-hash",
       "card_bg_alt": "hex-without-hash"
     },
     "fonts": {
       "heading": "Font Name",
       "body": "Font Name",
       "code": "Monospace Font"
     },
     "assets": {
       "logo": "assets/logo.png",
       "logo_dark": null,
       "icon": null
     }
   }
   ```

3. **Create config.json** with output settings. The default `directory` is a marker resolved at
   generation time under the DATA overlay (`get_outputs_dir()/content/decks/{brand}` via `$DECK_DIR`);
   set an absolute path only to override. A bare relative `output/...` would write into the engine.
   ```json
   {
     "output": {
       "directory": "content/decks/{brand}",
       "naming": "{name}-{date}",
       "keep_parts": false
     },
     "generation": {
       "slides_per_batch": 5,
       "auto_combine": true,
       "open_after_generate": false
     },
     "defaults": {
       "slide_width_inches": 13.333,
       "slide_height_inches": 7.5
     }
   }
   ```

4. **Create brand-system.md** - Copy from template and fill in brand guidelines

5. **Create tone-of-voice.md** - Copy from template and fill in voice guidelines

6. **Add assets** - Copy logo/images to `brands/{brand-name}/assets/`

## Step 4: Verify

After creating the brand, verify with:
```
Glob: .claude/skills/pptx-generator/brands/{brand-name}/*
```

Then proceed to slide generation.
