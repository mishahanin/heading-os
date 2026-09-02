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
    """X11 fallback: xclip -selection clipboard -t image/png -o > FILE.

    Cleans up after itself on every failure. See `_grab_via_wlpaste` for why
    the zero-byte check `main` used to do instead was not enough.
    """
    if not shutil.which("xclip"):
        return False
    try:
        with out_path.open("wb") as f:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                stdout=f, stderr=subprocess.DEVNULL, timeout=5,
            )
    except (subprocess.SubprocessError, OSError):
        out_path.unlink(missing_ok=True)
        return False
    if result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
        return True
    out_path.unlink(missing_ok=True)
    return False


def _grab_via_wlpaste(out_path: Path) -> bool:
    """Wayland fallback: wl-paste --type image/png > FILE.

    Every failing exit removes the file this call created, because the tool
    writes STRAIGHT into `clip.png`: `open("wb")` truncates before the grabber
    starts, and the grabber is killed at `timeout=5`. A slow grabber on a large
    clipboard image therefore leaves a nonzero, TRUNCATED PNG at the
    well-known path. `main` swept only zero-byte leftovers, so the operator was
    told "No image on clipboard." and exit 1 while a corrupt clip.png sat there,
    indistinguishable from a fresh capture to anything reading that path by
    convention. The timeout is a `SubprocessError`, which is why the handler
    below is a failure path and not a crash.
    """
    if not shutil.which("wl-paste"):
        return False
    try:
        with out_path.open("wb") as f:
            result = subprocess.run(
                ["wl-paste", "--type", "image/png"],
                stdout=f, stderr=subprocess.DEVNULL, timeout=5,
            )
    except (subprocess.SubprocessError, OSError):
        out_path.unlink(missing_ok=True)
        return False
    if result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
        return True
    out_path.unlink(missing_ok=True)
    return False


def main() -> int:
    out = get_outputs_dir() / "clipboard" / "clip.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    # `grabclipboard()` does not return None when the platform has no clipboard
    # grabber. It RAISES, and the raise happens before `img` is ever bound, so
    # every branch below it — including the Linux fallback, the one part of this
    # file that could still find an image — was unreachable BY CONSTRUCTION on
    # such a host. Measured 2026-08-30 on WSL2 with Pillow 12.3.0 and neither
    # binary installed:
    #     NotImplementedError: wl-paste or xclip is required for
    #     ImageGrab.grabclipboard() on Linux
    # The operator saw a raw traceback. Catching it HERE, at the entry, is what
    # lets the rest of the function run at all; patching a later branch could
    # not, because control never reached one.
    #
    # It is not only the "nothing installed" host. PIL takes wl-paste only for a
    # wayland session and xclip only for an x11 one (PIL/ImageGrab.py), so a host
    # with WAYLAND_DISPLAY set and ONLY xclip installed also lands here — and for
    # that host `_grab_via_xclip` below succeeds where PIL refused to try.
    #
    # NotImplementedError alone. Anything else from PIL is genuinely unexpected
    # and must keep its traceback.
    no_grabber = ""
    try:
        img = ImageGrab.grabclipboard()
    except NotImplementedError as exc:
        img = None
        no_grabber = str(exc)
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

    # PIL found nothing, or refused to look. On Linux try the direct CLI
    # fallbacks before giving up.
    if sys.platform.startswith("linux"):
        for grabber in (_grab_via_wlpaste, _grab_via_xclip):
            if grabber(out):
                print(str(out))
                return 0
        # No cleanup here any more. This used to unlink a zero-byte leftover,
        # which is only the half of the mess a killed grabber makes; each
        # grabber now removes whatever it created on every failing exit,
        # truncated-but-nonzero included, which is the only place that
        # distinction is known.

    if no_grabber:
        # PIL's own words for the reason, so this sentence cannot drift away from
        # what the library actually checked.
        print(f"Cannot read the clipboard: {no_grabber}; install wl-clipboard "
              f"or xclip, then copy the image and run this again.",
              file=sys.stderr)
    else:
        print("No image on clipboard.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
