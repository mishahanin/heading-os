#!/usr/bin/env python3
"""Save clipboard image to outputs/clipboard/clip.png.

Usage:
    python scripts/clip.py

Reads the current clipboard via PIL.ImageGrab. On Linux with older Pillow or
when no X11/Wayland clipboard helper is available via PIL, falls back to
shelling out to xclip / wl-paste directly. Exits with status 1 if no image is
on the clipboard. Prints the absolute path of the saved PNG on success.
"""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.venv_guard import ensure_venv  # noqa: E402

ensure_venv()
# BELOW the guard, which is the only position that does anything. `venv_guard`'s
# own docstring says to call it "before the heavy third-party imports", and this
# import sat above it until 2026-08-24 — the one third-party import in the file,
# placed where the guard could not help it. Launched with a bare system
# interpreter (cron, an absolute path, the wrong shell) the module died with
# ModuleNotFoundError: PIL before `ensure_venv` ever ran, so the re-exec into the
# venv that HAS Pillow never happened.
from PIL import ImageGrab  # noqa: E402
from scripts.utils.workspace import get_outputs_dir  # noqa: E402


def _grab_via_xclip(out_path: Path) -> bool:
    """X11 fallback: xclip -selection clipboard -t image/png -o > FILE."""
    if not shutil.which("xclip"):
        return False
    try:
        with out_path.open("wb") as f:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                stdout=f, stderr=subprocess.DEVNULL, timeout=5,
            )
    except (subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0


def _grab_via_wlpaste(out_path: Path) -> bool:
    """Wayland fallback: wl-paste --type image/png > FILE."""
    if not shutil.which("wl-paste"):
        return False
    try:
        with out_path.open("wb") as f:
            result = subprocess.run(
                ["wl-paste", "--type", "image/png"],
                stdout=f, stderr=subprocess.DEVNULL, timeout=5,
            )
    except (subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0


def main() -> int:
    out = get_outputs_dir() / "clipboard" / "clip.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    img = ImageGrab.grabclipboard()
    # `grabclipboard()` returns THREE shapes: an Image, a LIST of file paths
    # (documented behaviour on Windows and macOS when files rather than image
    # data are on the clipboard), or None. `is not None` accepted the list and
    # `img.save` then raised AttributeError on a traceback. Copying a file in
    # the OS file manager is an entirely ordinary thing to do before running
    # this.
    if isinstance(img, list):
        print(f"Clipboard holds {len(img)} file path(s), not an image. "
              f"Copy the image itself, or open the file directly:",
              file=sys.stderr)
        for entry in img[:5]:
            print(f"  {entry}", file=sys.stderr)
        return 1
    if img is not None:
        img.save(str(out), "PNG")
        print(str(out))
        return 0

    # PIL returned None. On Linux try direct CLI fallbacks before giving up;
    # older Pillow versions (<9.0) lack Linux clipboard support entirely.
    if sys.platform.startswith("linux"):
        for grabber in (_grab_via_wlpaste, _grab_via_xclip):
            if grabber(out):
                print(str(out))
                return 0
        # Clean up any zero-byte file the failed grabbers may have left.
        if out.exists() and out.stat().st_size == 0:
            out.unlink()

    print("No image on clipboard.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
