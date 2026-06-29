# Platform-Specific Parameters

## Midjourney

Append to prompt:
```
--ar 16:9 --v 6.1 --style raw --s 250
```

Additional flags:
- `--q 2` for higher quality (slower)
- `--chaos 10-30` for more variation between outputs
- `--no text, words, letters` to reinforce no-text rule

Midjourney responds well to: camera lens references (85mm, wide angle), film stock references (Kodak Portra, Fuji Velvia), photographer style references.

## DALL-E (OpenAI)

Include in the prompt body:
- "Photorealistic, high-resolution photograph"
- "16:9 aspect ratio, widescreen cinematic composition"
- "No text, no typography, no written elements"
- "Natural lighting, authentic textures"

DALL-E responds well to: explicit scene description, clear subject placement (foreground/midground/background), named lighting conditions (golden hour, blue hour, overcast).

## Stable Diffusion

Positive prompt: Include full image description.

Negative prompt:
```
text, typography, words, letters, numbers, watermark, signature, logo,
blurry, low quality, deformed, illustration, cartoon, painting, drawing,
anime, CGI, 3D render
```

Recommended settings:
- Steps: 30-50
- CFG Scale: 7-9
- Sampler: DPM++ 2M Karras

## Ideogram

Include in prompt:
- "Photograph, photorealistic"
- Specify 16:9 in the aspect ratio selector
- Ideogram handles text well but we never want text - explicitly state "no text"

## Flux (Black Forest Labs)

Similar approach to Stable Diffusion. Include:
- "Photorealistic photograph, cinematic 16:9"
- "No text, no words, no typography"
- Flux handles complex scenes well - can be more ambitious with composition
