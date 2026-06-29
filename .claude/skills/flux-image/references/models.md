# Image Generation Model Reference

## Available Models on Replicate

### Nano Banana 2 (Default)
- **CLI flag:** `--model banana`
- **Replicate ID:** `google/nano-banana-2`
- **Speed:** ~5-10 seconds per image
- **Cost:** Flash-level pricing
- **Best for:** General-purpose image generation, text in images, logos, typography, infographics, branded visuals, data visualizations
- **Key advantage:** Combines the visual quality of Nano Banana Pro with Flash-level speed. Best-in-class text rendering, crisp and readable across multiple languages. Supports up to 14 reference images for style transfer and composition. Resolutions from 512px to 4K.
- **Notes:** Built on Google Gemini 3.1 Flash Image. Faster than Nano Banana Pro while maintaining high quality. Strong at world knowledge, creative design, and complex prompts. This is the default model.

### FLUX.2 [max]
- **CLI flag:** `--model flux-max`
- **Replicate ID:** `black-forest-labs/flux-2-max`
- **Speed:** ~15-20 seconds per image
- **Cost:** ~$0.05 per image
- **Best for:** Top-tier photorealism, complex scene composition, multi-reference editing, maximum image fidelity
- **Key advantage:** Highest quality Flux model with superior prompt adherence, editing consistency, and multi-reference support. Best hit/miss ratio for complex edits.
- **Notes:** Black Forest Labs' flagship model. Use when you need maximum photorealism or are doing complex image editing with multiple references.

## Aspect Ratio Options

| Value | Use Case |
|-------|----------|
| `1:1` | Square - profile images, social media thumbnails |
| `16:9` | Widescreen - LinkedIn posts, presentations, banners |
| `21:9` | Ultra-wide - cinematic headers, website banners |
| `9:16` | Vertical - Instagram stories, mobile content |
| `4:5` | Portrait - Instagram feed posts |
| `3:2` | Classic photo - general photography composition |

## Prompt Tips

- Be specific and descriptive - both models respond well to detailed scene descriptions
- Include lighting direction: "golden hour side lighting", "overcast diffused light"
- Include camera references: "shot on 85mm lens", "wide angle perspective"
- Include material/texture descriptions: "weathered oak", "brushed steel", "morning dew"
- For photorealism: include "photorealistic, high resolution, natural lighting"
- To avoid text in images: include "no text, no words, no typography" in the prompt
- For text in images: use **banana** model and include the exact text in quotes within the prompt

## Troubleshooting

**"REPLICATE_API_TOKEN not found"**
Add it to the workspace `.env` file:
```
REPLICATE_API_TOKEN=r8_your_token_here
```

**API timeout or error**
- Check token validity at https://replicate.com/account/api-tokens
- Check Replicate status at https://replicate.com/status
- Try again - transient errors are possible

**Image URL expired**
- Replicate image URLs are temporary. The script downloads immediately.
- If you need the image again, regenerate or use the saved local file.
