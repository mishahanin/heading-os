#!/usr/bin/env python3
"""
Image Generator via Replicate HTTP API

Supports Nano Banana 2 (Google DeepMind) and FLUX.2 [max] (Black Forest Labs) models.
Uses direct HTTP requests - no SDK dependency, works with any Python 3.8+.

Usage:
    python generate_image.py --prompt "your prompt here" [options]

Options:
    --prompt TEXT        Image generation prompt (required)
    --output PATH        Output file path (default: the DATA overlay's
                         outputs/content/images/generated_image.png; never a
                         cwd-relative name, which lands in the public engine)
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


def _resolve_outputs_dir():
    """Return the DATA overlay's outputs directory, or raise.

    Kept as its own function so the failure mode is testable without reaching
    the filesystem layout of whatever clone the test runs on.
    """
    from scripts.utils.workspace import get_outputs_dir
    return get_outputs_dir()


def default_output_path():
    """Resolve the default `--output` path, outside the engine clone.

    Until 2026-08-31 the default was the bare relative name
    `generated_image.png`, which resolves against the current directory. Every
    documented invocation runs from the engine clone, and the engine repo is
    PUBLIC while a generated image is DATA. `SKILL.md` carried the workaround in
    prose - resolve the outputs dir first and pass an absolute path - so the
    guard lived in Markdown while the trap lived in the code.

    The engine package IS importable from this script's execution mode: the
    module header already inserts the workspace root on `sys.path` and imports
    `scripts.utils.workspace.load_env` from it. Measured 2026-08-31 under a bare
    `python3`, `get_outputs_dir()` resolves.

    When it does not resolve, REFUSE. A fallback to a cwd-relative name would be
    the original defect wearing a different hat.
    """
    try:
        outputs_dir = _resolve_outputs_dir()
    except Exception as exc:
        print("[ERROR] --output is required here.")
        print(f"        Could not resolve the data outputs directory: {exc}")
        print("        Pass an absolute path, e.g. --output /path/to/images/name.png")
        sys.exit(1)
    return str(outputs_dir / "content" / "images" / "generated_image.png")


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
    """Download a file from URL to local path.

    Assert the scheme rather than suppressing the warning about it. This URL is
    not a constant: `generate_image` reads it out of `prediction.get("output")`,
    the body of the Replicate API response. `urlopen` honours whatever scheme it
    is handed, so a `file:` URL reads a LOCAL path and this function then writes
    it out as a generated image, and `ftp:` reaches a second protocol nobody
    asked for.

    https ONLY, which is one notch tighter than the same guard in
    `.claude/skills/osint-advanced/scripts/osint_api.py`. That one also permits
    `http://`, and its URLs come from a fixed in-code endpoint table. This one
    comes from the response body of the very server being distrusted, and
    Replicate serves its outputs over https, so cleartext is a capability the
    caller never needs.
    """
    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-HTTPS download URL: {url!r}")
    req = urllib.request.Request(url)  # noqa: S310 - scheme asserted above
    with urllib.request.urlopen(req, timeout=60) as resp, open(filepath, "wb") as f:  # noqa: S310 - same guard
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


def build_parser():
    parser = argparse.ArgumentParser(
        description="Generate images via Replicate API"
    )
    parser.add_argument("--prompt", required=True, help="Image generation prompt")
    # The default is described, never spelled. Writing the literal path here put
    # a hardcoded data path in engine code and the leak guard refused the commit,
    # correctly: a path written down in a help string is the same path drifting
    # out of the `get_*_dir()` seam as one written down in code.
    parser.add_argument("--output", default=None,
                        help="Output file path (default: the generated-images "
                             "directory under the DATA overlay, resolved by "
                             "get_outputs_dir())")
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

    return parser


def main():
    args = build_parser().parse_args()

    generate_image(
        prompt=args.prompt,
        output_path=args.output or default_output_path(),
        aspect_ratio=args.aspect_ratio,
        model=args.model,
        num_outputs=args.num_outputs,
        output_format=args.output_format,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
