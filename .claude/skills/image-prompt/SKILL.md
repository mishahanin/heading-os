---
name: image-prompt
description: Generate AI text-to-image prompts from written content. Use after creating any content (LinkedIn post, blog article, email, presentation copy, announcement) to produce a photorealistic image prompt for Midjourney, DALL-E, Stable Diffusion, or similar platforms. Trigger when the user asks for an image, visual, illustration, or accompanying graphic for content just produced. Also use when the user says "image-prompt" or asks to visualize a post or article.
argument-hint: "[content or description to visualize]"
allowed-tools: "Read"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-heading-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - image prompt
    - visualize this
    - generate image prompt
x-heading-capability:
  what: >
    Turns content you just produced (post, article, email, slide copy) into a 50-150 word photorealistic text-to-image prompt for Midjourney, DALL-E, or Stable Diffusion, with 31C visual language and brand palette.
  how: >
    Run /image-prompt after producing content - it reads the preceding text and returns the core theme, the prompt, parameters, and a Midjourney suffix. Text only, no image file.
  when: >
    Use to get a prompt for a visual companion to content. To actually render the image use /flux-image; for full design work (mockups, infographics, logos) use /design.
x-heading-routing:
  category: Content
  triggers:
    - image prompt
    - visualize this
    - generate image prompt
  exclusions:
    - Actual image generation -> /flux-image
  compound: 'No'
  router: auto
---
# Image Prompt Generator

Analyze the preceding text, article, or post and generate a detailed, platform-ready image prompt for AI text-to-image tools.

## Process

1. **Identify the Central Idea** - Distill the main concept or narrative into one sentence
2. **Extract Visual Elements** - Find symbols, metaphors, settings, or scenarios that represent the content's essence
3. **Define the Mood** - Match the emotional atmosphere: inspirational, urgent, contemplative, dynamic, warm, authoritative
4. **Construct the Prompt** - Compose a 50-150 word description with subject, composition, lighting, color palette, atmosphere

## Visual Element Selection for 31C Content

Match content type to visual language:

| Content Type | Visual Direction |
|-------------|-----------------|
| Maritime/leadership metaphors | Sailing, navigation, open water, helm, compass, horizon |
| Product/technical | Networks, data flows, light particles, fiber optics, server rooms |
| Platform/building narrative | Architecture, bridges, foundations, construction, engineering |
| Expansion/market | Landscapes, coastlines, cities at dawn, maps, new horizons |
| Tribe/culture | Warm light, human silhouettes, collaborative spaces, depth |
| Sovereignty/independence | National landmarks, flags in wind, fortified structures, vaults |
| Speed/growth | Motion blur, launch trajectories, acceleration, time-lapse |

Default brand palette: deep blues, teals, warm amber accents.

## Output Format

Deliver exactly this structure:

```
**Core Theme Identified:** [One sentence - the central idea of the source content]

**Image Prompt:**
[50-150 words. Specific, descriptive, platform-ready. Include: subject matter, composition,
lighting, color palette, atmosphere. Only tangible, photographable elements.]

**Parameters:**
- Aspect Ratio: 16:9
- Style: Photorealistic, high-resolution, natural lighting, authentic textures
- No text, typography, words, letters, numbers, or written elements

**Platform Suffix (Midjourney):** --ar 16:9 --v 6.1 --style raw --s 250
```

For other platforms, see [references/platforms.md](references/platforms.md).

## Rules

- The image must feel like a natural visual companion to the source text
- Specific over vague - "golden hour light casting long shadows across cracked salt flats" beats "dramatic lighting"
- Focus on tangible subjects that photograph well in reality
- Cinematic composition suited to 16:9 widescreen format
- One clear focal point per image - avoid cluttered scenes

## Never Include

- Text, logos, typography, numbers, or written elements of any kind
- Stock photo clichés (handshakes, pointing at screens, generic team meetings)
- Military imagery (maritime only for 31C content)
- Dystopian or fear-based imagery (31C narrative is constructive)
- Abstract geometric patterns with no connection to the content
- Multiple competing focal points
