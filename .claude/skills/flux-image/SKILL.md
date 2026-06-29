---
name: flux-image
description: Generate images using AI models via Replicate API. Supports Nano Banana 2 (Google DeepMind, default) and FLUX.2 max (Black Forest Labs). Use when the user asks to generate, create, or produce an actual image from a prompt. This skill creates real image files on disk - not just text descriptions. Trigger when the user says "generate an image", "create an image", "flux-image", or asks to turn a prompt into an actual picture. Works standalone or as a follow-up to the image-prompt skill which produces text prompts.
argument-hint: "[prompt]"
allowed-tools: "Bash(python3:*), Bash(python:*), Bash(cd:*), Bash(git:*)"
model: haiku
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - generate image
    - create image
    - make a picture
    - flux
x-31c-capability:
  what: >
    Generates real image files on disk from a text prompt via the Replicate API,
    using Nano Banana 2 (Google DeepMind, default) or FLUX.2 max (Black Forest
    Labs).
  how: >
    Type /flux-image [prompt], or let it follow /image-prompt. It runs the
    generate_image.py script with flags for aspect ratio, model, and format, and
    saves to outputs/content/images/.
  when: >
    Use when you want an actual picture produced. For crafting the text prompt
    only (no image) use /image-prompt; for a full multi-asset design pipeline
    (mockups, infographics, logos) use /design.
---
# AI Image Generator

Generate images using Nano Banana 2 (Google DeepMind) or FLUX.2 max (Black Forest Labs) via the Replicate API. Runs a Python script that calls the API and saves images locally.

## Prerequisites

The `REPLICATE_API_TOKEN` must be set in the workspace `.env` file (root directory). The script auto-loads it from `.env` using `python-dotenv` -- no manual export needed.

If the token is missing or invalid, instruct the user to add it to `.env`:

```
# In the workspace root .env file:
REPLICATE_API_TOKEN=r8_your_token_here
```

Get a token at: https://replicate.com/account/api-tokens

**Security:** Never hardcode API tokens in skill files, scripts, or documentation. All secrets live in `.env` only.

## Workflow

### 1. Determine the prompt

Either:
- Use the image prompt from a preceding `/image-prompt` skill invocation
- Use a prompt the user provides directly
- Craft a prompt from context (article, post, etc.) using the image prompt guidelines

### 2. Run the generation script

Run from the workspace root (the script path is root-relative). The image is a DATA artifact -- it
must land in the DATA overlay, never the engine tree. `generate_image.py` writes `--output` literally
relative to the current directory (the engine root after the `cd`), so a bare `outputs/...` would
create a stray image inside the engine. Resolve the data outputs dir first and pass an absolute path:

```bash
cd "$(git rev-parse --show-toplevel)"
OUTPUTS_DIR="$(python3 -c "import sys; sys.path.insert(0,'.'); from scripts.utils.workspace import get_outputs_dir; print(get_outputs_dir())")"
python ".claude/skills/flux-image/scripts/generate_image.py" \
  --prompt "your detailed image prompt here" \
  --output "$OUTPUTS_DIR/content/images/my-image.png" \
  --aspect-ratio 16:9 \
  --model banana \
  --format png
```

### 3. Report the result

Tell the user the file path of the generated image. Use the Read tool to display the image to the user (Claude Code can render images).

## Script Parameters

| Parameter | Default | Options | Description |
|-----------|---------|---------|-------------|
| `--prompt` | Required | Any text | The image generation prompt |
| `--output` | `generated_image.png` | Any path | Where to save the image |
| `--aspect-ratio` | `16:9` | `1:1`, `16:9`, `21:9`, `2:3`, `3:2`, `4:5`, `5:4`, `9:16`, `9:21` | Image dimensions |
| `--model` | `banana` | `banana`, `flux-max` | Model: Nano Banana 2 (default) or FLUX.2 max |
| `--num-outputs` | `1` | `1`-`4` | Number of variations |
| `--format` | `png` | `png`, `jpg`, `webp` | Output format |
| `--seed` | Random | Any integer | For reproducible results |

## Model Selection

Choose based on need. See [references/models.md](references/models.md) for full details.

| Model | Speed | Quality | Cost | Best For |
|-------|-------|---------|------|----------|
| **banana** (default) | ~10-15s | Highest | ~$0.05 | Best text rendering, infographics, branded visuals, general-purpose |
| **flux-max** | ~15-20s | Highest | ~$0.05 | Top-tier photorealism, complex scenes, multi-reference editing |

Default to **banana** (Google Nano Banana 2). Use **flux-max** (FLUX.2 max) for maximum photorealism or complex scene editing.

## Sensitive sessions (SENSITIVE_MODE)

Before sending any prompt to the Replicate API, check session sensitivity:

```bash
python3 -c "from scripts.utils.sensitive import is_sensitive, sanitize_prompt_guidance; print(sanitize_prompt_guidance()) if is_sensitive() else None"
```

If it prints guidance (sensitivity not explicitly cleared — the fail-closed
default), strip ALL project-identifying detail from the prompt before the call:
no codenames, company names, people names, deal terms, or strategic specifics.
Use abstract, generic descriptions ("corporate acquisition integration diagram",
not "Phoenix acquisition of CompanyX"). The image file stays local; only the
prompt goes to Replicate, so the prompt must carry nothing identifying. This
replaces the removed `secure-flux` vault wrapper.

## Output Location

Save generated images under the DATA overlay's `content/images/` directory, resolved via
`$OUTPUTS_DIR` (see step 2): `"$OUTPUTS_DIR/content/images/<name>.png"`. ALWAYS use this path - never
save to the overlay's `outputs/` root, and never to a bare cwd-relative `outputs/...` (that resolves
into the engine tree). Use descriptive filenames:
- `$OUTPUTS_DIR/content/images/linkedin-hiring-post-image.png`
- `$OUTPUTS_DIR/content/images/mwc-teaser-visual.png`
- `$OUTPUTS_DIR/content/images/tribe-monday-banner.png`

## Combining with /image-prompt

The typical two-step workflow:
1. `/image-prompt` - analyzes content and produces a text prompt
2. `/flux-image` - takes that prompt and generates the actual image

When used after `/image-prompt`, extract the Image Prompt text (not the parameters or platform suffix) and pass it to the script.
