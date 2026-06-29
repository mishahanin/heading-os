#!/usr/bin/env python3
"""Export Antigravity IDE configuration for sharing with another user.

Produces a zip containing:
- settings.json (sensitive keys auto-masked)
- snippets/ folder
- extensions.txt (list of extension IDs)
- README.md (install instructions for the recipient)

Usage:
    python scripts/export-antigravity-config.py
    python scripts/export-antigravity-config.py --output path/to/out.zip
    python scripts/export-antigravity-config.py --no-mask      # don't mask (dangerous)

Supports Windows, macOS, Linux Antigravity installs.
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import get_outputs_dir, get_workspace_root


SENSITIVE_KEY_PATTERNS = [
    "apikey", "api_key", "api-key",
    "token", "auth",
    "password", "passwd",
    "secret",
    "credential",
    "bearer",
    "accesskey", "access_key", "access-key",
    "privatekey", "private_key", "private-key",
]


def detect_paths():
    """Return (user_data_dir, antigravity_cli_path) for the current platform."""
    system = platform.system()
    if system == "Windows":
        user_data = Path(os.environ["APPDATA"]) / "Antigravity" / "User"
        cli = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Antigravity" / "bin" / "antigravity.cmd"
        if not cli.exists():
            cli = None
    elif system == "Darwin":
        user_data = Path.home() / "Library" / "Application Support" / "Antigravity" / "User"
        cli = Path("/Applications/Antigravity.app/Contents/Resources/app/bin/antigravity")
        if not cli.exists():
            resolved = shutil.which("antigravity")
            cli = Path(resolved) if resolved else None
    else:
        user_data = Path.home() / ".config" / "Antigravity" / "User"
        resolved = shutil.which("antigravity")
        cli = Path(resolved) if resolved else None
    return user_data, cli


def is_sensitive_key(key: str) -> bool:
    """Return True if the key name contains a sensitive-looking substring."""
    lowered = key.lower()
    return any(pat in lowered for pat in SENSITIVE_KEY_PATTERNS)


def mask_sensitive(obj, path: str = "", masked_keys=None):
    """Walk a JSON-like structure and mask string values under sensitive keys."""
    if masked_keys is None:
        masked_keys = []
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            key_path = f"{path}.{k}" if path else k
            if is_sensitive_key(k) and isinstance(v, str) and v:
                obj[k] = "***MASKED***"
                masked_keys.append(key_path)
            else:
                mask_sensitive(v, key_path, masked_keys)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            mask_sensitive(v, f"{path}[{i}]", masked_keys)
    return obj, masked_keys


def strip_jsonc(text: str) -> str:
    """Strip // and /* */ comments and trailing commas from JSONC text.

    VS Code (and forks like Antigravity) store settings as JSONC. Python's
    json.loads can't parse that, so we preprocess. This is a simple scanner
    that respects string literals - imperfect but adequate for real settings.
    """
    out = []
    in_string = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if escape:
            out.append(c)
            escape = False
            i += 1
            continue
        if in_string:
            if c == "\\":
                out.append(c)
                escape = True
                i += 1
                continue
            if c == '"':
                in_string = False
            out.append(c)
            i += 1
            continue
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    stripped = "".join(out)
    stripped = re.sub(r",(\s*[}\]])", r"\1", stripped)
    return stripped


def build_readme(date_str: str, masked_count: int) -> str:
    mask_note = ""
    if masked_count:
        mask_note = (
            f"\n{masked_count} sensitive-looking keys in settings.json were "
            f"auto-masked to `***MASKED***`. Set your own values via the "
            f"relevant extension's UI or command palette after import - do "
            f"not hand-edit settings.json for credentials.\n"
        )
    return f"""# Antigravity Config Bundle

Exported on {date_str}.

## Prerequisites

Antigravity must be installed AND launched at least once on your machine
before applying this bundle (so the User folder exists).

## Windows

```
copy settings.json "%APPDATA%\\Antigravity\\User\\settings.json"
xcopy /E /I snippets "%APPDATA%\\Antigravity\\User\\snippets"
for /F "tokens=*" %i in (extensions.txt) do "%LOCALAPPDATA%\\Programs\\Antigravity\\bin\\antigravity.cmd" --install-extension %i
```

## macOS

```
cp settings.json "$HOME/Library/Application Support/Antigravity/User/settings.json"
cp -r snippets/* "$HOME/Library/Application Support/Antigravity/User/snippets/"
cat extensions.txt | xargs -L 1 /Applications/Antigravity.app/Contents/Resources/app/bin/antigravity --install-extension
```

## Linux

```
cp settings.json "$HOME/.config/Antigravity/User/settings.json"
cp -r snippets/* "$HOME/.config/Antigravity/User/snippets/"
cat extensions.txt | xargs -L 1 antigravity --install-extension
```

Restart Antigravity after applying.
{mask_note}"""


def main():
    parser = argparse.ArgumentParser(
        description="Export Antigravity configuration for sharing."
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output zip path. Default: outputs/antigravity-config/antigravity-config-YYYY-MM-DD.zip",
    )
    parser.add_argument(
        "--no-mask", action="store_true",
        help="Do not mask sensitive-looking keys in settings.json (NOT recommended)",
    )
    args = parser.parse_args()

    user_data, cli = detect_paths()

    print(f"{BOLD}{CYAN}Antigravity Config Export{RESET}")
    print(f"  Platform:  {platform.system()}")
    print(f"  User data: {user_data}")
    print(f"  CLI:       {cli if cli else '(not found)'}")
    print()

    if not user_data.exists():
        print(f"{RED}[error]{RESET} Antigravity user data not found at {user_data}")
        print(f"        Is Antigravity installed and launched at least once?")
        sys.exit(1)

    date_str = datetime.now().strftime("%Y-%m-%d")
    if args.output:
        out_zip = args.output.resolve()
        out_zip.parent.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = get_outputs_dir() / "antigravity-config"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_zip = out_dir / f"antigravity-config-{date_str}.zip"

    settings_src = user_data / "settings.json"
    snippets_src = user_data / "snippets"
    masked_keys = []

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        if settings_src.exists():
            raw = settings_src.read_text(encoding="utf-8")
            out_text = raw
            try:
                data = json.loads(strip_jsonc(raw))
                if not args.no_mask:
                    data, masked_keys = mask_sensitive(data)
                out_text = json.dumps(data, indent=2)
            except json.JSONDecodeError as e:
                print(f"  {YELLOW}[warn]{RESET} settings.json would not parse as JSON after JSONC strip ({e}). "
                      f"Copying raw content without masking.")
            zf.writestr("settings.json", out_text)
            size = len(out_text.encode("utf-8"))
            print(f"  {GREEN}[ok]{RESET} settings.json ({size} bytes, {len(masked_keys)} keys masked)")
            for k in masked_keys:
                print(f"         - masked: {k}")
        else:
            print(f"  {YELLOW}[skip]{RESET} no settings.json at {settings_src}")

        snippet_count = 0
        if snippets_src.exists():
            for f in snippets_src.rglob("*"):
                if f.is_file():
                    arcname = f"snippets/{f.relative_to(snippets_src).as_posix()}"
                    zf.write(f, arcname)
                    snippet_count += 1
            print(f"  {GREEN}[ok]{RESET} snippets/ ({snippet_count} files)")
        else:
            print(f"  {GRAY}[skip]{RESET} no snippets directory")

        ext_count = 0
        if cli:
            try:
                result = subprocess.run(
                    [str(cli), "--list-extensions"],
                    capture_output=True, text=True, check=False,
                )
                if result.returncode == 0:
                    ext_list = result.stdout.strip()
                    ext_count = len([line for line in ext_list.splitlines() if line.strip()])
                    zf.writestr("extensions.txt", ext_list + "\n" if ext_list else "")
                    print(f"  {GREEN}[ok]{RESET} extensions.txt ({ext_count} extensions)")
                else:
                    print(f"  {YELLOW}[warn]{RESET} antigravity --list-extensions failed: {result.stderr.strip()}")
            except (OSError, subprocess.SubprocessError) as e:
                print(f"  {RED}[error]{RESET} CLI invocation failed: {e}")
        else:
            print(f"  {YELLOW}[warn]{RESET} Antigravity CLI not found - extensions.txt skipped")

        zf.writestr("README.md", build_readme(date_str, len(masked_keys)))
        print(f"  {GREEN}[ok]{RESET} README.md (install instructions)")

    size_mb = out_zip.stat().st_size / (1024 * 1024)
    print()
    print(f"{BOLD}{GREEN}Export complete:{RESET} {out_zip}")
    print(f"  Size: {size_mb:.2f} MB")
    if masked_keys:
        print()
        print(f"{YELLOW}Review before sending:{RESET}")
        print(f"  The auto-masker replaced values for keys whose names contained one of:")
        print(f"  {', '.join(SENSITIVE_KEY_PATTERNS)}.")
        print(f"  Extract the zip and eyeball settings.json for anything else that looks")
        print(f"  like a credential (long hex strings, tokens starting with sk-/ghp_/xoxb-,")
        print(f"  etc.) before sharing. Extension-written values with innocent key names")
        print(f"  will not be caught by the automatic scan.")


if __name__ == "__main__":
    main()
