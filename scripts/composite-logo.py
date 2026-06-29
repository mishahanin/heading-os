#!/usr/bin/env python3
"""Composite a logo image onto a base image at the bottom-right corner.

Usage:
    python scripts/composite-logo.py <base_image> <logo_image> <output_path>

Scales the logo to ~15% of the base image width and overlays it using the
logo's alpha channel. Adds ~3% image-width padding from bottom-right corner.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.venv import ensure_venv  # noqa: E402

ensure_venv()

from PIL import Image  # noqa: E402


def main() -> int:
    if len(sys.argv) != 4:
        print("Usage: composite-logo.py <base_image> <logo_image> <output_path>", file=sys.stderr)
        return 1

    img_path = sys.argv[1]
    logo_path = sys.argv[2]
    output_path = sys.argv[3]

    img = Image.open(img_path)
    logo = Image.open(logo_path)

    img_w, img_h = img.size
    logo_w, logo_h = logo.size

    # Scale logo to about 15% of image width
    target_w = int(img_w * 0.15)
    scale = target_w / logo_w
    target_h = int(logo_h * scale)
    logo_resized = logo.resize((target_w, target_h), Image.LANCZOS)

    # Position: bottom-right corner with padding
    padding = int(img_w * 0.03)
    x = img_w - target_w - padding
    y = img_h - target_h - padding

    # Composite using alpha channel
    img.paste(logo_resized, (x, y), logo_resized)
    img.save(output_path)

    print(f"Saved to {output_path}")
    print(f"Image size: {img_w}x{img_h}")
    print(f"Logo placed at: ({x}, {y}), size: {target_w}x{target_h}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
