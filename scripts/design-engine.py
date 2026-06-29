#!/usr/bin/env python3
"""Unified Replicate API gateway for image generation, editing, upscaling, and background removal.

Usage:
    python scripts/design-engine.py generate --model flux-schnell --prompt "A mountain at sunset"
    python scripts/design-engine.py generate --model recraft-v4 --prompt "Brand logo" --width 1024 --height 1024
    python scripts/design-engine.py edit --image input.png --prompt "Make the sky purple"
    python scripts/design-engine.py upscale --image photo.png --model crisp-upscale
    python scripts/design-engine.py remove-bg --image product.png
    python scripts/design-engine.py models --type generate

Environment:
    REPLICATE_API_TOKEN  Loaded from .env via workspace utils.
                         Get one at: https://replicate.com/account/api-tokens
"""

import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from uuid import uuid4

# Workspace imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.api import load_api_key
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET
from scripts.utils.workspace import get_outputs_dir

# ============================================================
# Configuration
# ============================================================

MODELS = {
    # Generation - Recraft V4
    "recraft-v4": {"id": "recraft-ai/recraft-v4", "type": "generate", "cost": 0.04, "description": "Illustrations, brand assets, editorial imagery", "family": "recraft"},
    "recraft-v4-svg": {"id": "recraft-ai/recraft-v4-svg", "type": "generate", "cost": 0.08, "description": "Logos, icons, native vector SVG", "family": "recraft"},
    "recraft-v4-pro": {"id": "recraft-ai/recraft-v4-pro", "type": "generate", "cost": 0.25, "description": "High-res raster (2048x2048)", "family": "recraft"},
    "recraft-v4-pro-svg": {"id": "recraft-ai/recraft-v4-pro-svg", "type": "generate", "cost": 0.30, "description": "High-res vector, production logos", "family": "recraft"},
    # Generation - FLUX
    "flux-2-pro": {"id": "black-forest-labs/flux-2-pro", "type": "generate", "cost": 0.055, "description": "Photorealism, multi-reference", "family": "flux"},
    "flux-schnell": {"id": "black-forest-labs/flux-schnell", "type": "generate", "cost": 0.003, "description": "Fast drafts, iteration, previews", "family": "flux"},
    # Generation - Ideogram
    "ideogram-v3": {"id": "ideogram-ai/ideogram-v3-quality", "type": "generate", "cost": 0.09, "description": "Text in images, posters (95% accuracy)", "family": "ideogram"},
    "ideogram-v3-turbo": {"id": "ideogram-ai/ideogram-v3-turbo", "type": "generate", "cost": 0.03, "description": "Fast text-in-image drafts", "family": "ideogram"},
    # Generation - Banana
    "banana": {"id": "google/nano-banana-2", "type": "generate", "cost": 0.04, "description": "General purpose, text rendering", "family": "banana"},
    "banana-pro": {"id": "google/nano-banana-pro", "type": "generate", "cost": 0.134, "description": "Multi-image compositing (14 refs)", "family": "banana"},
    # Editing
    "kontext": {"id": "black-forest-labs/flux-kontext-pro", "type": "edit", "cost": 0.0, "description": "Natural language image editing", "family": "edit"},
    "fill": {"id": "black-forest-labs/flux-fill-pro", "type": "edit", "cost": 0.0, "description": "Inpainting and outpainting", "family": "edit"},
    "depth": {"id": "black-forest-labs/flux-depth-pro", "type": "edit", "cost": 0.0, "description": "Structure-preserving edits", "family": "edit"},
    "canny": {"id": "black-forest-labs/flux-canny-pro", "type": "edit", "cost": 0.0, "description": "Edge-guided generation", "family": "edit"},
    # Post-processing
    "crisp-upscale": {"id": "recraft-ai/recraft-crisp-upscale", "type": "upscale", "cost": 0.0, "description": "Sharp print-quality upscaling", "family": "postprocess"},
    "esrgan": {"id": "nightmareai/real-esrgan", "type": "upscale", "cost": 0.0, "description": "Fast bulk upscaling", "family": "postprocess"},
    "eraser": {"id": "bria/eraser", "type": "remove-bg", "cost": 0.0, "description": "Background and object removal", "family": "postprocess"},
}

REPLICATE_API = "https://api.replicate.com/v1"
POLL_INTERVAL = 2
POLL_TIMEOUT = 120

# ============================================================
# Helpers
# ============================================================


def info(msg: str) -> None:
    print(f"{CYAN}[INFO]{RESET} {msg}")


def ok(msg: str) -> None:
    print(f"{GREEN}[OK]{RESET} {msg}")


def error(msg: str) -> None:
    print(f"{RED}[ERROR]{RESET} {msg}", file=sys.stderr)


def cost(msg: str) -> None:
    print(f"{YELLOW}[COST]{RESET} {msg}")


def _default_output_dir() -> Path:
    return get_outputs_dir() / "content" / "images"


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _api_request(method: str, url: str, token: str, data: dict = None) -> dict:
    """Authenticated JSON request to Replicate API."""
    if not url.startswith("http"):
        url = f"{REPLICATE_API}{url}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=POLL_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.readable() else str(e)
        error(f"HTTP {e.code}: {error_body}")
        sys.exit(1)
    except urllib.error.URLError as e:
        error(f"Network error: {e.reason}")
        sys.exit(1)


def _upload_file(file_path: Path, token: str) -> str:
    """Upload a local file to Replicate and return the serving URL."""
    info(f"Uploading {file_path.name} to Replicate...")
    file_bytes = file_path.read_bytes()
    filename = file_path.name
    content_type = "image/png"
    if filename.lower().endswith(".jpg") or filename.lower().endswith(".jpeg"):
        content_type = "image/jpeg"
    elif filename.lower().endswith(".webp"):
        content_type = "image/webp"

    boundary = uuid4().hex
    body = io.BytesIO()
    # Content-Disposition part
    body.write(f"--{boundary}\r\n".encode())
    body.write(f'Content-Disposition: form-data; name="content"; filename="{filename}"\r\n'.encode())
    body.write(f"Content-Type: {content_type}\r\n\r\n".encode())
    body.write(file_bytes)
    body.write(f"\r\n--{boundary}--\r\n".encode())

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    req = urllib.request.Request(
        f"{REPLICATE_API}/files",
        data=body.getvalue(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            serving_url = result.get("urls", {}).get("get", "")
            if not serving_url:
                error("File upload succeeded but no serving URL returned.")
                sys.exit(1)
            ok(f"Uploaded: {serving_url}")
            return serving_url
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.readable() else str(e)
        error(f"Upload failed - HTTP {e.code}: {error_body}")
        sys.exit(1)
    except urllib.error.URLError as e:
        error(f"Upload network error: {e.reason}")
        sys.exit(1)


def _download(url: str, dest: Path) -> None:
    """Download a URL to a local file."""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as resp:
        dest.write_bytes(resp.read())


def _create_prediction(token: str, model_id: str, input_params: dict) -> dict:
    """Create a prediction and poll until completion."""
    owner, name = model_id.split("/", 1)
    prediction = _api_request("POST", f"/models/{owner}/{name}/predictions", token, {"input": input_params})

    pred_id = prediction.get("id")
    status = prediction.get("status")
    info(f"Prediction {pred_id} - status: {status}")

    elapsed = 0
    while status not in ("succeeded", "failed", "canceled"):
        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        if elapsed > POLL_TIMEOUT:
            error(f"Timed out after {POLL_TIMEOUT}s waiting for prediction {pred_id}.")
            sys.exit(1)
        prediction = _api_request("GET", f"/predictions/{pred_id}", token)
        status = prediction.get("status")
        info(f"Status: {status} ({elapsed}s)")

    if status != "succeeded":
        err_msg = prediction.get("error", "Unknown error")
        error(f"Prediction failed: {err_msg}")
        sys.exit(1)

    return prediction


def _normalize_outputs(output) -> list:
    """Normalize prediction output to a list of URLs."""
    if isinstance(output, str):
        return [output]
    if isinstance(output, list):
        return output
    return []


def _save_outputs(urls: list, output_path: Path, multi: bool, is_svg: bool) -> list:
    """Download output URLs to local files. Returns list of saved Paths."""
    os.makedirs(output_path.parent, exist_ok=True)
    saved = []
    for idx, url in enumerate(urls):
        if multi and len(urls) > 1:
            dest = output_path.parent / f"{output_path.stem}_{idx + 1}{output_path.suffix}"
        else:
            dest = output_path
        # For SVG models the API may return text content in a URL
        _download(url, dest)
        saved.append(dest)
        ok(f"Saved: {dest.resolve()}")
    return saved


# ============================================================
# Pipeline Routing
# ============================================================


def _build_generate_input(family: str, prompt: str, width: int, height: int,
                          aspect: str, count: int, fmt: str, seed: int = None) -> dict:
    """Build input dict based on model family."""
    if family == "recraft":
        return {"prompt": prompt, "size": f"{width}x{height}"}

    if family == "flux":
        params = {"prompt": prompt, "aspect_ratio": aspect, "num_outputs": count, "output_format": fmt}
        if seed is not None:
            params["seed"] = seed
        return params

    if family == "ideogram":
        return {"prompt": prompt, "width": width, "height": height}

    if family == "banana":
        params = {"prompt": prompt, "aspect_ratio": aspect, "num_outputs": count, "output_format": fmt}
        if seed is not None:
            params["seed"] = seed
        return params

    # Fallback
    return {"prompt": prompt}


# ============================================================
# Generation Pipeline
# ============================================================


def cmd_generate(args) -> None:
    """Generate images from a text prompt."""
    token = load_api_key("REPLICATE_API_TOKEN")
    alias = args.model
    model = MODELS.get(alias)
    if not model or model["type"] != "generate":
        error(f"Unknown generation model: {alias}")
        error(f"Available: {', '.join(k for k, v in MODELS.items() if v['type'] == 'generate')}")
        sys.exit(1)

    is_svg = "svg" in alias
    ext = ".svg" if is_svg else f".{args.format}"
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = _default_output_dir() / f"design-{_timestamp()}{ext}"

    width = args.width or 1024
    height = args.height or 1024
    aspect = args.aspect or "16:9"
    count = args.count or 1
    fmt = args.format or "png"

    info(f"Model: {alias} ({model['id']})")
    info(f"Prompt: {args.prompt[:120]}{'...' if len(args.prompt) > 120 else ''}")
    info(f"Output: {output_path.resolve()}")

    input_params = _build_generate_input(
        family=model["family"], prompt=args.prompt,
        width=width, height=height, aspect=aspect,
        count=count, fmt=fmt, seed=args.seed,
    )

    prediction = _create_prediction(token, model["id"], input_params)
    urls = _normalize_outputs(prediction.get("output", []))
    if not urls:
        error("No output URLs returned.")
        sys.exit(1)

    saved = _save_outputs(urls, output_path, multi=(count > 1), is_svg=is_svg)
    unit_cost = model["cost"]
    total = unit_cost * len(saved)
    cost(f"Estimated: ${total:.3f} ({len(saved)} image(s) x ${unit_cost:.3f})")


# ============================================================
# Edit Pipeline
# ============================================================


def cmd_edit(args) -> None:
    """Edit an existing image with a text prompt."""
    token = load_api_key("REPLICATE_API_TOKEN")
    alias = args.model or "kontext"
    model = MODELS.get(alias)
    if not model or model["type"] != "edit":
        error(f"Unknown edit model: {alias}")
        error(f"Available: {', '.join(k for k, v in MODELS.items() if v['type'] == 'edit')}")
        sys.exit(1)

    image_path = Path(args.image)
    if not image_path.exists():
        error(f"Image not found: {image_path}")
        sys.exit(1)

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = _default_output_dir() / f"edit-{_timestamp()}{image_path.suffix}"

    info(f"Model: {alias} ({model['id']})")
    info(f"Image: {image_path.resolve()}")
    info(f"Prompt: {args.prompt[:120]}{'...' if len(args.prompt) > 120 else ''}")

    uploaded_url = _upload_file(image_path, token)
    input_params = {"image": uploaded_url, "prompt": args.prompt}
    prediction = _create_prediction(token, model["id"], input_params)

    urls = _normalize_outputs(prediction.get("output", []))
    if not urls:
        error("No output URLs returned.")
        sys.exit(1)

    saved = _save_outputs(urls, output_path, multi=False, is_svg=False)
    unit_cost = model["cost"]
    total = unit_cost * len(saved)
    cost(f"Estimated: ${total:.3f} ({len(saved)} image(s))")


# ============================================================
# Upscale Pipeline
# ============================================================


def cmd_upscale(args) -> None:
    """Upscale an image."""
    token = load_api_key("REPLICATE_API_TOKEN")
    alias = args.model or "crisp-upscale"
    model = MODELS.get(alias)
    if not model or model["type"] != "upscale":
        error(f"Unknown upscale model: {alias}")
        error(f"Available: {', '.join(k for k, v in MODELS.items() if v['type'] == 'upscale')}")
        sys.exit(1)

    image_path = Path(args.image)
    if not image_path.exists():
        error(f"Image not found: {image_path}")
        sys.exit(1)

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = _default_output_dir() / f"upscale-{_timestamp()}{image_path.suffix}"

    info(f"Model: {alias} ({model['id']})")
    info(f"Image: {image_path.resolve()}")

    uploaded_url = _upload_file(image_path, token)
    input_params = {"image": uploaded_url}
    if alias == "esrgan":
        input_params["scale"] = args.scale if hasattr(args, "scale") and args.scale else 4

    prediction = _create_prediction(token, model["id"], input_params)
    urls = _normalize_outputs(prediction.get("output", []))
    if not urls:
        error("No output URLs returned.")
        sys.exit(1)

    saved = _save_outputs(urls, output_path, multi=False, is_svg=False)
    unit_cost = model["cost"]
    total = unit_cost * len(saved)
    cost(f"Estimated: ${total:.3f} ({len(saved)} image(s))")


# ============================================================
# Background Removal Pipeline
# ============================================================


def cmd_remove_bg(args) -> None:
    """Remove background from an image."""
    token = load_api_key("REPLICATE_API_TOKEN")
    model = MODELS["eraser"]

    image_path = Path(args.image)
    if not image_path.exists():
        error(f"Image not found: {image_path}")
        sys.exit(1)

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = _default_output_dir() / f"nobg-{_timestamp()}.png"

    info(f"Model: eraser ({model['id']})")
    info(f"Image: {image_path.resolve()}")

    uploaded_url = _upload_file(image_path, token)
    input_params = {"image": uploaded_url}
    prediction = _create_prediction(token, model["id"], input_params)

    urls = _normalize_outputs(prediction.get("output", []))
    if not urls:
        error("No output URLs returned.")
        sys.exit(1)

    saved = _save_outputs(urls, output_path, multi=False, is_svg=False)
    cost(f"Estimated: $0.000 ({len(saved)} image(s))")


# ============================================================
# Model Registry Display
# ============================================================


def cmd_models(args) -> None:
    """Print the model registry as a formatted table."""
    type_filter = args.type if hasattr(args, "type") else None
    entries = []
    for alias, m in MODELS.items():
        if type_filter and m["type"] != type_filter:
            continue
        entries.append((alias, m["type"], f"${m['cost']:.3f}", m["description"]))

    if not entries:
        info("No models match the filter.")
        return

    # Column widths
    headers = ("Alias", "Type", "Cost", "Description")
    widths = [max(len(headers[i]), max(len(row[i]) for row in entries)) for i in range(4)]
    fmt_str = f"  {{:<{widths[0]}}}  {{:<{widths[1]}}}  {{:<{widths[2]}}}  {{:<{widths[3]}}}"

    print(f"\n{BOLD}Available Models{RESET}" + (f" (type: {type_filter})" if type_filter else ""))
    print(f"  {GRAY}{'-' * (sum(widths) + 6)}{RESET}")
    print(f"{BOLD}{fmt_str.format(*headers)}{RESET}")
    print(f"  {GRAY}{'-' * (sum(widths) + 6)}{RESET}")
    for row in entries:
        alias_col = f"{CYAN}{row[0]}{RESET}"
        # Pad with invisible chars accounted for
        pad = widths[0] - len(row[0])
        print(f"  {alias_col}{' ' * pad}  {row[1]:<{widths[1]}}  {YELLOW}{row[2]}{RESET}{'  ' + ' ' * (widths[2] - len(row[2]))}{row[3]}")
    print(f"  {GRAY}{'-' * (sum(widths) + 6)}{RESET}")
    print(f"  {GRAY}{len(entries)} model(s){RESET}\n")


# ============================================================
# Main / CLI
# ============================================================


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="design-engine",
        description="Unified Replicate API gateway for image generation, editing, upscaling, and background removal.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # -- generate --
    gen_parser = subparsers.add_parser("generate", help="Generate images from a text prompt")
    gen_parser.add_argument("--model", required=True, help="Model alias (e.g. flux-schnell, recraft-v4)")
    gen_parser.add_argument("--prompt", required=True, help="Text prompt")
    gen_parser.add_argument("--width", type=int, default=None, help="Width in pixels (recraft/ideogram, default 1024)")
    gen_parser.add_argument("--height", type=int, default=None, help="Height in pixels (recraft/ideogram, default 1024)")
    gen_parser.add_argument("--aspect", default=None, help="Aspect ratio (flux/banana, default 16:9)")
    gen_parser.add_argument("--count", type=int, default=None, help="Number of images (default 1)")
    gen_parser.add_argument("--format", default="png", choices=["png", "jpg", "webp"], help="Output format (default png)")
    gen_parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    gen_parser.add_argument("-o", "--output", default=None, help="Output file path")
    gen_parser.set_defaults(func=cmd_generate)

    # -- edit --
    edit_parser = subparsers.add_parser("edit", help="Edit an image with a text prompt")
    edit_parser.add_argument("--model", default="kontext", help="Edit model alias (default: kontext)")
    edit_parser.add_argument("--image", required=True, help="Input image path")
    edit_parser.add_argument("--prompt", required=True, help="Edit instruction")
    edit_parser.add_argument("-o", "--output", default=None, help="Output file path")
    edit_parser.set_defaults(func=cmd_edit)

    # -- upscale --
    up_parser = subparsers.add_parser("upscale", help="Upscale an image")
    up_parser.add_argument("--model", default="crisp-upscale", choices=["crisp-upscale", "esrgan"], help="Upscale model (default: crisp-upscale)")
    up_parser.add_argument("--image", required=True, help="Input image path")
    up_parser.add_argument("--scale", type=int, default=4, help="Scale factor for esrgan (default 4)")
    up_parser.add_argument("-o", "--output", default=None, help="Output file path")
    up_parser.set_defaults(func=cmd_upscale)

    # -- remove-bg --
    bg_parser = subparsers.add_parser("remove-bg", help="Remove background from an image")
    bg_parser.add_argument("--image", required=True, help="Input image path")
    bg_parser.add_argument("-o", "--output", default=None, help="Output file path")
    bg_parser.set_defaults(func=cmd_remove_bg)

    # -- models --
    mod_parser = subparsers.add_parser("models", help="List available models")
    mod_parser.add_argument("--type", default=None, choices=["generate", "edit", "upscale", "remove-bg"], help="Filter by model type")
    mod_parser.set_defaults(func=cmd_models)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
