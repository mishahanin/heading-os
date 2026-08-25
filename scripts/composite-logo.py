#!/usr/bin/env python3
"""Composite a logo image onto a base image at the bottom-right corner.

Usage:
    python scripts/composite-logo.py <base_image> <logo_image> <output_path>

Scales the logo to ~15% of the base image width and overlays it using the
logo's alpha channel. Adds ~3% image-width padding from bottom-right corner.

Tests: tests/test_a_gate_that_shipped_what_it_never_read.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.venv_guard import ensure_venv  # noqa: E402

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
    # `max(1, ...)`: `int()` truncates toward zero, so a base narrower than
    # 7 px gives target_w 0, and a wide banner logo on a small base gives
    # target_h 0. `Image.resize` refuses a zero dimension with ValueError, so a
    # valid pair of images exited on a traceback instead of producing output.
    target_w = max(1, int(img_w * 0.15))
    scale = target_w / logo_w
    target_h = max(1, int(logo_h * scale))
    logo_resized = logo.resize((target_w, target_h), Image.LANCZOS)

    # Position: bottom-right corner with padding
    padding = int(img_w * 0.03)
    x = img_w - target_w - padding
    y = img_h - target_h - padding

    # Composite using the logo's alpha channel. `paste` needs a mask in mode
    # "1", "L", "LA" or "RGBA", and the logo was passed as its own mask with
    # nothing checking it had one — so any JPEG or plain-RGB logo raised
    # `ValueError: bad transparency mask` and exited on a traceback, bypassing
    # the usage-error path. A JPEG is an ordinary input for an untyped
    # `<logo_image>` argument. Converting is free when the alpha is already
    # there and gives an opaque logo when it is not, which is what pasting an
    # image without transparency should mean.
    if logo_resized.mode not in ("LA", "RGBA"):
        logo_resized = logo_resized.convert("RGBA")
    img.paste(logo_resized, (x, y), logo_resized)
    # Every mode JPEG cannot write, not only RGBA. A GIF base opens in mode "P"
    # and a grayscale-with-alpha PNG in mode "LA"; both reached `save()` and
    # raised `OSError: cannot write mode P as JPEG`. A GIF is as ordinary an
    # input as the JPEG logo the comment further up already handles, so the
    # same reasoning applied and only half the work was done.
    if (Path(output_path).suffix.lower() in (".jpg", ".jpeg")
            and img.mode not in ("L", "RGB", "CMYK")):
        img = img.convert("RGB")
    img.save(output_path)

    print(f"Saved to {output_path}")
    print(f"Image size: {img_w}x{img_h}")
    print(f"Logo placed at: ({x}, {y}), size: {target_w}x{target_h}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
