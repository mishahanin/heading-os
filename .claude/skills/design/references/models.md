# Design Engine - Replicate Model Reference

Consumed by: `.claude/skills/design/SKILL.md`
Last Updated: 2026-03-28

---

## Generation Models

### Recraft V4 Family (Design-First)

| Alias | Replicate ID | Cost | Output | Best For |
|---|---|---|---|---|
| `recraft-v4` | `recraft-ai/recraft-v4` | ~$0.04 | Raster (PNG) | Illustrations, brand assets, editorial imagery |
| `recraft-v4-svg` | `recraft-ai/recraft-v4-svg` | ~$0.08 | Native SVG | Logos, icons, vector graphics |
| `recraft-v4-pro` | `recraft-ai/recraft-v4-pro` | ~$0.25 | Raster (2048x2048) | Production-quality, high-resolution |
| `recraft-v4-pro-svg` | `recraft-ai/recraft-v4-pro-svg` | ~$0.30 | Native SVG (high-res) | Production logos, detailed vector |

- Topped HuggingFace Text-to-Image Arena leaderboard (outranked Midjourney V8, DALL-E 3)
- Only model family producing native SVG with real paths and clean geometry
- Supports exact RGB color specification for brand-aligned output
- Input: `prompt`, `size` (WxH), `style` (optional)

### FLUX Family (Black Forest Labs)

| Alias | Replicate ID | Cost | Speed | Best For |
|---|---|---|---|---|
| `flux-2-pro` | `black-forest-labs/flux-2-pro` | ~$0.055 | ~6s | Photorealism, multi-reference, hex-color accuracy |
| `flux-schnell` | `black-forest-labs/flux-schnell` | ~$0.003 | <1s | Fast drafts, concept iteration, previews |

- 32-billion parameter architecture (FLUX.2)
- Hex-code color accuracy for brand guidelines
- `flux-schnell` at $0.003 is ideal for rapid iteration before committing to final model
- Input: `prompt`, `aspect_ratio`, `num_outputs`, `output_format`, `seed`

### Ideogram V3 Family (Text Specialist)

| Alias | Replicate ID | Cost | Speed | Best For |
|---|---|---|---|---|
| `ideogram-v3` | `ideogram-ai/ideogram-v3-quality` | ~$0.09 | Moderate | Text-heavy graphics, posters, banners |
| `ideogram-v3-turbo` | `ideogram-ai/ideogram-v3-turbo` | ~$0.03 | Fast | Quick text-in-image drafts |

- 95%+ text rendering accuracy (industry leader)
- Handles 10+ word phrases reliably
- Use whenever text must appear in the image
- Input: `prompt`, `width`, `height`, `style_type`, `magic_prompt_option`

### Nano Banana Family (Google)

| Alias | Replicate ID | Cost | Speed | Best For |
|---|---|---|---|---|
| `banana` | `google/nano-banana-2` | ~$0.04 | ~5-10s | General purpose, text rendering, infographics |
| `banana-pro` | `google/nano-banana-pro` | ~$0.134 | Moderate | Multi-image compositing (up to 14 refs) |

- Built on Google Gemini architecture
- Strong text rendering and world knowledge
- `banana-pro` supports up to 14 reference images for style consistency
- Input: `prompt`, `aspect_ratio`, `num_outputs`, `output_format`, `seed`

---

## Editing Models

| Alias | Replicate ID | Cost | Best For |
|---|---|---|---|
| `kontext` | `black-forest-labs/flux-kontext-pro` | per-compute | Natural language image editing ("remove the text", "change color to blue") |
| `fill` | `black-forest-labs/flux-fill-pro` | per-compute | Inpainting (fill masked areas) and outpainting (extend image borders) |
| `depth` | `black-forest-labs/flux-depth-pro` | per-compute | Structure-preserving edits (retexture while maintaining depth) |
| `canny` | `black-forest-labs/flux-canny-pro` | per-compute | Edge-guided generation (maintain layout, change content) |

- All editing models require an input image (uploaded via Replicate file API)
- `kontext` is the most versatile - handles most editing tasks via natural language
- `fill` requires a mask image in addition to the source image
- Input: `image` (URL), `prompt`, optional `mask` (for fill)

---

## Post-Processing Models

| Alias | Replicate ID | Cost | Best For |
|---|---|---|---|
| `crisp-upscale` | `recraft-ai/recraft-crisp-upscale` | per-compute | Sharp print-quality upscaling (web and print) |
| `esrgan` | `nightmareai/real-esrgan` | per-compute | Fast bulk upscaling (87M+ runs on Replicate) |
| `eraser` | `bria/eraser` | per-compute | Background and object removal with visual continuity |

- `crisp-upscale` designed specifically for design assets (sharper than ESRGAN for graphics)
- `esrgan` better for photographs and natural images
- `eraser` produces clean cutouts suitable for compositing
- Input: `image` (URL), optional `scale` (for upscalers)

---

## Model Selection Matrix

| Task | Recommended Model | Why | Cost |
|---|---|---|---|
| Illustration, brand asset | `recraft-v4` | Design-first, #1 leaderboard | $0.04 |
| Logo, icon (vector) | `recraft-v4-svg` | Only native SVG model | $0.08 |
| Photorealistic image | `flux-2-pro` | Best photorealism, 32B params | $0.055 |
| Quick draft / concept | `flux-schnell` | Sub-1s, cheapest | $0.003 |
| Graphic with text | `ideogram-v3` | 95% text accuracy | $0.09 |
| Fast text draft | `ideogram-v3-turbo` | Good text, 3x faster | $0.03 |
| General purpose | `banana` | All-around quality | $0.04 |
| Multi-reference composite | `banana-pro` | Up to 14 ref images | $0.134 |
| Edit existing image | `kontext` | Natural language editing | per-compute |
| Inpaint / outpaint | `fill` | Mask-based fill | per-compute |
| Upscale for print | `crisp-upscale` | Sharp design upscaling | per-compute |
| Remove background | `eraser` | Clean cutouts | per-compute |

---

## Aspect Ratio Reference

| Value | Dimensions | Use Case |
|---|---|---|
| `1:1` | 1024x1024 | Square - social media, profile images |
| `16:9` | 1365x768 | Widescreen - LinkedIn, presentations, banners |
| `21:9` | 1536x640 | Ultra-wide - cinematic headers, website banners |
| `9:16` | 768x1365 | Vertical - stories, mobile content |
| `4:5` | 896x1120 | Portrait - Instagram feed |
| `3:2` | 1216x832 | Classic photo - general photography |
| `2:3` | 832x1216 | Tall portrait - posters |
| `5:4` | 1120x896 | Slightly wide - product shots |
| `9:21` | 640x1536 | Ultra-tall - mobile banners |

Note: Exact pixel dimensions vary by model. Aspect ratio is the reliable parameter.

---

## Prompt Tips by Model Family

**Recraft V4:**
- Specify exact RGB colors: "using #5B5FFF blue-purple and #FF8C00 orange accents"
- Mention style: "editorial illustration", "flat design", "isometric"
- For SVG: keep prompts focused on shape and composition, not photorealistic detail

**FLUX.2:**
- Include camera references: "shot on 85mm lens", "wide angle perspective"
- Include lighting: "golden hour side lighting", "studio lighting with soft box"
- Include materials: "brushed steel", "matte finish", "natural wood grain"

**Ideogram V3:**
- Put exact text in quotes within the prompt: 'Text reads "ODUN.ONE"'
- Specify text placement: "centered headline", "bottom caption"
- Specify style: "modern poster design", "clean typography"

**General (all models):**
- More detail = better results. 50-150 words is the sweet spot.
- For 31C brand work: "dark background, professional, sovereign, bold, blue-purple (#5B5FFF) and orange (#FF8C00) accent colors"
- To avoid text: "no text, no words, no typography, no letters"

---

## Troubleshooting

**"REPLICATE_API_TOKEN not found"**
Add to workspace `.env` file: `REPLICATE_API_TOKEN=r8_your_token_here`
Get a token at: https://replicate.com/account/api-tokens

**Model not found or deprecated**
Replicate occasionally renames or removes models. Update the model ID in the `MODELS` dict in `scripts/design-engine.py`. The user-facing alias stays stable.

**Generation timeout**
Default timeout is 120 seconds. Some models (especially pro variants and upscalers) may need longer for large images. The script polls every 2 seconds.

**SVG output is raster-traced**
Only `recraft-v4-svg` and `recraft-v4-pro-svg` produce native vector SVG. Other models output raster images regardless of file extension.
