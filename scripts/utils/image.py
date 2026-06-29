"""Image loading utilities."""

import base64
from pathlib import Path

from .workspace import get_datastore_dir


def load_logo_base64(logo_path: Path = None) -> str:
    """Load an image and return as base64 data URI.

    Args:
        logo_path: Path to image file. Defaults to standard 31C logo location.

    Returns:
        Base64 data URI string, or empty string if file not found.
    """
    if logo_path is None:
        logo_path = get_datastore_dir() / "brand" / "assets" / "email-signature" / "logo-email-signature.png"
    if logo_path.exists():
        data = logo_path.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        ext = logo_path.suffix.lstrip(".").lower()
        mime = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "svg": "image/svg+xml",
        }.get(ext, "image/png")
        return f"data:{mime};base64,{b64}"
    return ""
