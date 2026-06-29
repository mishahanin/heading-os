#!/usr/bin/env python3
"""
Image Generator via Replicate HTTP API

Supports Nano Banana 2 (Google DeepMind) and FLUX.2 [max] (Black Forest Labs) models.
Uses direct HTTP requests - no SDK dependency, works with any Python 3.8+.

Usage:
    python generate_image.py --prompt "your prompt here" [options]

Options:
    --prompt TEXT        Image generation prompt (required)
    --output PATH        Output file path (default: generated_image.png)
    --aspect-ratio STR   Aspect ratio (default: 16:9)
    --model STR          Model: banana|flux-max (default: banana)
    --num-outputs INT    Number of images 1-4 (default: 1)
    --format STR         Output format: png|jpg|webp (default: png)
    --seed INT           Random seed for reproducibility (optional)

Environment:
    REPLICATE_API_TOKEN  Loaded automatically from workspace .env file.
                         If missing, get one at: https://replicate.com/account/api-tokens
                         and add to .env: REPLICATE_API_TOKEN=r8_your_token_here
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

# Auto-load .env via workspace central loader
# .claude/skills/flux-image/scripts/generate_image.py -> workspace root is 4 levels up
_script_dir = os.path.dirname(os.path.abspath(__file__))
_workspace_root = os.path.abspath(os.path.join(_script_dir, '..', '..', '..', '..'))
sys.path.insert(0, _workspace_root)
try:
    from pathlib import Path
    from scripts.utils.workspace import load_env
    load_env(Path(_workspace_root))
except ImportError:
    pass  # Fall back to environment variable if loader not available


MODELS = {
    "banana": "google/nano-banana-2",
    "flux-max": "black-forest-labs/flux-2-max",
}

# Models that use different input parameter schemas
BANANA_MODELS = {"banana"}

REPLICATE_API = "https://api.replicate.com/v1"


def api_request(method, path, token, data=None):
    """Make an authenticated request to Replicate API."""
    url = f"{REPLICATE_API}{path}" if path.startswith("/") else path
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }

    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.readable() else str(e)
        print(f"[ERROR] HTTP {e.code}: {error_body}")
        sys.exit(1)


def download_file(url, filepath):
    """Download a file from URL to local path."""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as resp:
        with open(filepath, "wb") as f:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                f.write(chunk)


def generate_image(prompt, output_path, aspect_ratio="16:9", model="banana",
                   num_outputs=1, output_format="png", seed=None):
    """Generate an image via Replicate HTTP API."""

    token = os.environ.get("REPLICATE_API_TOKEN")
    if not token:
        print("[ERROR] REPLICATE_API_TOKEN not found.")
        print("        Add it to the workspace .env file:")
        print("        REPLICATE_API_TOKEN=r8_your_token_here")
        print("        Get a token at: https://replicate.com/account/api-tokens")
        sys.exit(1)

    model_key = model.lower()
    model_id = MODELS.get(model_key, MODELS["banana"])
    is_banana = model_key in BANANA_MODELS
    print(f"[INFO] Model: {model_id}")
    print(f"[INFO] Aspect ratio: {aspect_ratio}")
    print(f"[INFO] Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
    print(f"[INFO] Generating {num_outputs} image(s)...")

    # Build input parameters (schema differs per model family)
    if is_banana:
        input_params = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "num_outputs": num_outputs,
            "output_format": output_format,
        }
        if seed is not None:
            input_params["seed"] = seed
    else:
        input_params = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "num_outputs": num_outputs,
            "output_format": output_format,
            "output_quality": 100 if output_format == "png" else 90,
        }
        if seed is not None:
            input_params["seed"] = seed

    # Create prediction
    # Use the models endpoint format: /models/{owner}/{name}/predictions
    owner, name = model_id.split("/")
    prediction = api_request(
        "POST",
        f"/models/{owner}/{name}/predictions",
        token,
        {"input": input_params},
    )

    pred_id = prediction.get("id")
    status = prediction.get("status")
    print(f"[INFO] Prediction ID: {pred_id}")
    print(f"[INFO] Status: {status}")

    # Poll until complete (if not already done via Prefer: wait)
    max_wait = 120  # seconds
    elapsed = 0
    while status not in ("succeeded", "failed", "canceled"):
        time.sleep(2)
        elapsed += 2
        if elapsed > max_wait:
            print(f"[ERROR] Timed out after {max_wait}s waiting for generation.")
            sys.exit(1)

        prediction = api_request(
            "GET",
            f"/predictions/{pred_id}",
            token,
        )
        status = prediction.get("status")
        print(f"[INFO] Status: {status} ({elapsed}s)")

    if status != "succeeded":
        error = prediction.get("error", "Unknown error")
        print(f"[ERROR] Generation failed: {error}")
        sys.exit(1)

    # Get output URLs
    output_urls = prediction.get("output", [])
    if isinstance(output_urls, str):
        output_urls = [output_urls]

    if not output_urls:
        print("[ERROR] No output images returned.")
        sys.exit(1)

    # Ensure output directory exists
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # Download and save images
    saved_files = []
    for idx, url in enumerate(output_urls):
        if num_outputs > 1 or len(output_urls) > 1:
            base, ext = os.path.splitext(output_path)
            filepath = f"{base}_{idx + 1}{ext}"
        else:
            filepath = output_path

        try:
            download_file(url, filepath)
            saved_files.append(filepath)
            print(f"[OK] Saved: {filepath}")
        except Exception as e:
            print(f"[ERROR] Failed to download {filepath}: {e}")

    if saved_files:
        print(f"\n[DONE] Generated {len(saved_files)} image(s):")
        for f in saved_files:
            abs_path = os.path.abspath(f)
            print(f"  {abs_path}")
        return saved_files
    else:
        print("[ERROR] No images were saved.")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Generate images via Replicate API"
    )
    parser.add_argument("--prompt", required=True, help="Image generation prompt")
    parser.add_argument("--output", default="generated_image.png",
                        help="Output file path (default: generated_image.png)")
    parser.add_argument("--aspect-ratio", default="16:9",
                        choices=["1:1", "16:9", "21:9", "2:3", "3:2",
                                 "4:5", "5:4", "9:16", "9:21"],
                        help="Aspect ratio (default: 16:9)")
    parser.add_argument("--model", default="banana",
                        choices=["banana", "flux-max"],
                        help="Model: banana (Google Nano Banana 2, default) or flux-max (FLUX.2 max)")
    parser.add_argument("--num-outputs", type=int, default=1,
                        choices=[1, 2, 3, 4],
                        help="Number of images to generate (default: 1)")
    parser.add_argument("--format", default="png",
                        choices=["png", "jpg", "webp"],
                        dest="output_format",
                        help="Image format (default: png)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")

    args = parser.parse_args()

    generate_image(
        prompt=args.prompt,
        output_path=args.output,
        aspect_ratio=args.aspect_ratio,
        model=args.model,
        num_outputs=args.num_outputs,
        output_format=args.output_format,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
