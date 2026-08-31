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

Note: each model family accepts a different set of the generate flags. Pass one
the family does not take and `generate` prints a [WARN] naming it; nothing is
translated on your behalf. See `_build_generate_input`.

Note: `-o` OVERRIDES the `_default_output_dir()` seam and is resolved LITERALLY
against the current directory. Omitting it lands the file under
`get_outputs_dir()`, in the DATA overlay. Passing a relative `outputs/...`
instead writes a private artifact into the engine clone, where `.gitignore`
hides it from the push wall. Pass an absolute path, or omit the flag.

Tests: tests/test_a_retry_that_promised_a_longer_timeout.py
       tests/test_the_flags_a_tool_accepted_and_never_sent.py
       tests/test_a_budget_one_hung_request_could_spend.py
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
from scripts.utils.workspace import get_default_tz, get_outputs_dir

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
    "kontext": {"id": "black-forest-labs/flux-kontext-pro", "type": "edit", "cost": None, "description": "Natural language image editing", "family": "edit"},
    "fill": {"id": "black-forest-labs/flux-fill-pro", "type": "edit", "cost": None, "description": "Inpainting and outpainting", "family": "edit"},
    "depth": {"id": "black-forest-labs/flux-depth-pro", "type": "edit", "cost": None, "description": "Structure-preserving edits", "family": "edit"},
    "canny": {"id": "black-forest-labs/flux-canny-pro", "type": "edit", "cost": None, "description": "Edge-guided generation", "family": "edit"},
    # Post-processing
    "crisp-upscale": {"id": "recraft-ai/recraft-crisp-upscale", "type": "upscale", "cost": None, "description": "Sharp print-quality upscaling", "family": "postprocess"},
    "esrgan": {"id": "nightmareai/real-esrgan", "type": "upscale", "cost": None, "description": "Fast bulk upscaling", "family": "postprocess"},
    "eraser": {"id": "bria/eraser", "type": "remove-bg", "cost": None, "description": "Background and object removal", "family": "postprocess"},
}
# `cost: None` means the per-run price was never recorded for that model, and it
# is NOT the same statement as free. Every one of these carried `0.0`, and the
# nine generation models beside them carry a real figure — the split falls
# exactly on edit/post-processing, which is what an unfilled field looks like,
# not a measured zero. The tool then multiplied it out and printed
# "Estimated: $0.000" over four paid Replicate models, and `cmd_remove_bg` did
# not even read the registry: it had the literal `$0.000` in its format string.
# A price the operator is charged is the last figure a tool should invent.
# Fill one in from the model's Replicate page and it starts being reported.

REPLICATE_API = "https://api.replicate.com/v1"
POLL_INTERVAL = 2

# A socket timeout answers "how long may ONE call block?". A budget answers
# "how long may ALL of them together?". Those are different questions, and
# `POLL_TIMEOUT` used to be the answer to both: it was handed to `urlopen` AND
# compared against total elapsed. So one connection that hung spent the entire
# budget inside a single call, and the loop's first check (which only runs once
# that call has returned) found nothing left. The poll made exactly one
# attempt and reported a timeout, with one socket hung and the service
# perfectly healthy. Two roles, two numbers, and one function tying them.

# The whole prediction's wall-clock budget, measured from before the POST.
POLL_TIMEOUT = 120
# The creating POST carries `Prefer: wait`, which Replicate holds open for up
# to a minute. It gets the longer socket timeout: that documented minute plus
# 30s of margin for connection setup and the response body.
CREATE_TIMEOUT = 90
# A status GET answers from a lookup and returns at once, so a poll that blocks
# is a poll that has hung. Derived rather than chosen, so the property outlives
# an edit to any one number: at most a quarter of the budget, which leaves a
# hung poll three further attempts inside the same run.
HUNG_POLLS_PER_BUDGET = 4
POLL_REQUEST_TIMEOUT = POLL_TIMEOUT // HUNG_POLLS_PER_BUDGET


def _validated_budget(budget: int, create: int, interval: int) -> int:
    """Return `budget`, or refuse a pair one request could spend on its own.

    The relationship between the two timeouts, written down where it can be
    executed instead of remembered. The budget has to outlast the longest
    single request by at least one poll interval; below that line the slowest
    legitimate POST is the whole budget again and the loop never reaches its
    first GET. Raised rather than asserted, so `python -O` cannot switch the
    check off.
    """
    if budget <= create + interval:
        raise RuntimeError(
            f"polling budget ({budget}s) must exceed the longest single "
            f"request ({create}s) plus one poll interval ({interval}s): a "
            f"budget one request can spend leaves the poll loop no attempts."
        )
    return budget


POLL_TIMEOUT = _validated_budget(POLL_TIMEOUT, CREATE_TIMEOUT, POLL_INTERVAL)

# Which socket timeout each call gets. Selected by method rather than passed at
# the call site, so the two callers in `_create_prediction` keep the signature
# every test and reader already knows, and the mapping lives in one place.
_REQUEST_TIMEOUTS = {"POST": CREATE_TIMEOUT, "GET": POLL_REQUEST_TIMEOUT}

# A generated image is single-digit MB; this is a runaway guard, not a fit.
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024

# ============================================================
# Helpers
# ============================================================


def info(msg: str) -> None:
    print(f"{CYAN}[INFO]{RESET} {msg}")


def ok(msg: str) -> None:
    print(f"{GREEN}[OK]{RESET} {msg}")


def error(msg: str) -> None:
    print(f"{RED}[ERROR]{RESET} {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"{YELLOW}[WARN]{RESET} {msg}")


def cost(msg: str) -> None:
    print(f"{YELLOW}[COST]{RESET} {msg}")


def _report_cost(alias: str, model: dict, count: int) -> None:
    """Print the run's estimated cost, or say the price is not on file.

    Silence is not an option here (the operator needs to know a paid call just
    ran), and neither is `$0.000` (it reads as free). Naming the gap is.
    """
    unit = model.get("cost")
    if unit is None:
        cost(f"Not estimated: no per-run price recorded for '{alias}' "
             f"({count} image(s)). Replicate still charges for this model.")
        return
    cost(f"Estimated: ${unit * count:.3f} ({count} image(s) x ${unit:.3f})")


def _cost_cell(model: dict) -> str:
    """The Cost column for `models`. '?' where no price was ever recorded."""
    unit = model.get("cost")
    return "?" if unit is None else f"${unit:.3f}"


def _default_output_dir() -> Path:
    return get_outputs_dir() / "content" / "images"


def _timestamp() -> str:
    return datetime.now(get_default_tz()).strftime("%Y%m%d-%H%M%S")


def _unique_path(path: Path) -> Path:
    """A free path near `path`, so a name the TOOL chose never destroys a file.

    `_timestamp()` has one-second resolution, so two runs inside the same second
    produced the same default name and the second silently overwrote the first
    while printing "Saved" over a path whose earlier bytes were gone. Only the
    tool's own default names go through here; a path the operator typed with
    `-o` is theirs to overwrite.
    """
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.parent / f"{path.stem}-{n}{path.suffix}"
        if not candidate.exists():
            return candidate
    return path.parent / f"{path.stem}-{uuid4().hex[:8]}{path.suffix}"


def _sniff_ext(data: bytes) -> str | None:
    """The extension the BYTES say, or None when nothing recognisable leads.

    The filename used to be built from `--format`, a flag two of the four
    generation families are never told (see `_build_generate_input`). Asking
    for webp from recraft produced a PNG named `.webp`: a name that lies about
    its contents, which the next tool in the chain then reads as fact.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    head = data[:512].lstrip()
    if head.startswith((b"<?xml", b"<svg")):
        return ".svg" if b"<svg" in data[:2048] else None
    return None


def _api_request(method: str, url: str, token: str, data: dict = None,
                 timeout: float | None = None) -> dict:
    """Authenticated JSON request to Replicate API.

    `timeout` is this ONE call's socket timeout, never the polling budget. Left
    at None it comes from `_REQUEST_TIMEOUTS`, which gives the creating POST the
    minute Replicate may hold `Prefer: wait` open and gives a status GET a
    quarter of the budget.
    """
    if timeout is None:
        timeout = _REQUEST_TIMEOUTS.get(method, POLL_REQUEST_TIMEOUT)
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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
    # The name is interpolated into a header inside a quoted string. A `"` in it
    # closes that string early and a CR or LF ends the header line, so a file
    # named `evil".png` rewrites the part this tool believes it is sending.
    # The name is a label to the receiving end, so neutering the three
    # characters that carry structure costs nothing.
    safe_name = filename.replace("\\", "_").replace('"', "_").replace("\r", "_").replace("\n", "_")
    body.write(f'Content-Disposition: form-data; name="content"; filename="{safe_name}"\r\n'.encode())
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


def _download(url: str, dest: Path) -> bytes:
    """Download a URL to a local file. Returns the bytes written.

    The scheme check and the size cap are the ones its sibling
    `scripts/updaters/cliproxyapi_update.py:_download` already carries, with the
    same reason: this URL is NOT a literal. It arrives inside the prediction
    response as `output`, so the scheme is remote data, and `urlopen` honours
    `file:` -- which would turn a tampered response into a local-file read saved
    as the operator's image. The cap replaces an uncapped `resp.read()` that
    held the whole body in memory before anything was written.

    Errors are reported in this tool's own voice. It had none, so a network
    blip surfaced as a raw traceback while every sibling call printed [ERROR]
    and exited 1.

    Note for whoever reads `pyproject.toml` next: the `[tool.bandit]` skip of
    B310 is justified there by "our scripts call hardcoded https API endpoints
    ... never user-controlled schemes". That was true of every urlopen in this
    workspace EXCEPT this one. The check below is what makes the claim true.
    """
    if not url.startswith("https://"):
        error(f"Refusing a non-https download URL: {url!r}")
        sys.exit(1)
    req = urllib.request.Request(url)  # noqa: S310 - scheme checked above
    written = 0
    chunks = []
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - scheme checked above
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_DOWNLOAD_BYTES:
                    error(f"Download exceeded {MAX_DOWNLOAD_BYTES} bytes: {url}")
                    sys.exit(1)
                chunks.append(chunk)
    except urllib.error.HTTPError as e:
        error(f"Download failed - HTTP {e.code}: {url}")
        sys.exit(1)
    except urllib.error.URLError as e:
        error(f"Download network error: {e.reason}")
        sys.exit(1)
    data = b"".join(chunks)
    dest.write_bytes(data)
    return data


def _create_prediction(token: str, model_id: str, input_params: dict) -> dict:
    """Create a prediction and poll until completion."""
    owner, name = model_id.split("/", 1)
    # The clock starts BEFORE the POST. That request carries `Prefer: wait`,
    # which Replicate holds open for up to a minute, so starting the clock
    # after it returned put the single longest wait outside the budget the
    # timeout message advertises, and "budget 120s" could be printed after 240s
    # of real blocking. It now runs under `CREATE_TIMEOUT`, which the budget is
    # required to outlast, so the worst case is a POST that spends 90 of the
    # 120 seconds and a loop that still gets its remaining fifteen attempts.
    started = time.monotonic()
    prediction = _api_request("POST", f"/models/{owner}/{name}/predictions", token, {"input": input_params})

    pred_id = prediction.get("id")
    status = prediction.get("status")
    info(f"Prediction {pred_id} - status: {status} ({time.monotonic() - started:.0f}s)")

    # Measured, not counted. `elapsed += POLL_INTERVAL` summed the SLEEPS and
    # nothing else, so every poll request's own duration was invisible to it.
    while status not in ("succeeded", "failed", "canceled"):
        time.sleep(POLL_INTERVAL)
        elapsed = time.monotonic() - started
        if elapsed > POLL_TIMEOUT:
            error(f"Timed out after {elapsed:.0f}s (budget {POLL_TIMEOUT}s) "
                  f"waiting for prediction {pred_id}.")
            sys.exit(1)
        prediction = _api_request("GET", f"/predictions/{pred_id}", token)
        status = prediction.get("status")
        info(f"Status: {status} ({time.monotonic() - started:.0f}s)")

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


def _save_outputs(urls: list, output_path: Path, name_from_bytes: bool) -> list:
    """Download output URLs to local files. Returns the list of saved Paths.

    Two things this used to get wrong, both of the same shape: it reported more
    than it did.

    Numbering was gated on a `multi` flag the caller derived from `--count`, so
    the three callers that hardcoded `multi=False` wrote every URL to ONE path.
    MEASURED with three URLs: three "Saved" lines naming the same file, three
    identical paths returned, one file on disk holding the last body, and the
    cost line then billing three images against it. Numbering now follows the
    only fact that decides it - how many URLs came back.

    `is_svg` was a parameter this function never read. It is gone; the format
    is now taken from the bytes, which is the only place it is knowable.
    """
    os.makedirs(output_path.parent, exist_ok=True)
    saved = []
    numbered = len(urls) > 1
    for idx, url in enumerate(urls):
        dest = (output_path.parent / f"{output_path.stem}_{idx + 1}{output_path.suffix}"
                if numbered else output_path)
        data = _download(url, dest)
        actual = _sniff_ext(data)
        if actual and actual != dest.suffix.lower():
            if name_from_bytes:
                renamed = _unique_path(dest.with_suffix(actual))
                dest.rename(renamed)
                dest = renamed
            else:
                warn(f"{dest.name} is named {dest.suffix} and the bytes are {actual}. "
                     f"Kept the name you gave with -o; the contents are {actual}.")
        saved.append(dest)
        ok(f"Saved: {dest.resolve()}")
    return saved


# ============================================================
# Pipeline Routing
# ============================================================


# Which payload key would carry each CLI flag, if the family accepts it at all.
# `dropped` is derived by checking the payload this builder ACTUALLY produced
# against this map, rather than from a second hand-written per-family table.
# A second table is the thing that drifts: the builder gets a new family and the
# table does not, and the tool goes back to reporting a flag as sent because a
# list said so. Here a new family reports correctly with no edit here at all.
_FLAG_CARRIERS = {
    "width": ("size", "width"),
    "height": ("size", "height"),
    "aspect": ("aspect_ratio",),
    "count": ("num_outputs",),
    "format": ("output_format",),
    "seed": ("seed",),
}


def _build_generate_input(family: str, prompt: str, width: int, height: int,
                          aspect: str, count: int, fmt: str, seed: int = None,
                          explicit: set = None) -> tuple:
    """Build the model input, and name the operator's flags that never reach it.

    Returns `(params, dropped)`. `dropped` lists the flags the operator TYPED
    that no key of `params` carries, sorted, without the leading dashes.

    Each family takes a different set, and the four disagree. MEASURED:

        recraft   -> prompt, size                 (no --count, --seed, --format)
        ideogram  -> prompt, width, height        (no --count, --seed, --format)
        flux      -> prompt, aspect_ratio, num_outputs, output_format, seed
        banana    -> the same as flux             (both: no --width, --height)

    That last row is the one that bites. `.claude/skills/design/SKILL.md` gives
    ONE documented generate command and it always passes `--width` and
    `--height`, while the same file's model table routes "photorealistic image"
    to flux-2-pro and "fast concept draft" to flux-schnell. Both dropped the
    two flags on the floor and said nothing, so an operator asking for
    1024x1024 got whatever 16:9 gave and had no way to learn otherwise.

    No flag is TRANSLATED here. Guessing a mapping onto an API this tool cannot
    reach without spending the operator's money would replace a silent drop
    with a confident wrong parameter, which is worse. It reports instead.
    """
    explicit = explicit or set()
    if family == "recraft":
        params = {"prompt": prompt, "size": f"{width}x{height}"}
    elif family in ("flux", "banana"):
        params = {"prompt": prompt, "aspect_ratio": aspect, "num_outputs": count, "output_format": fmt}
        if seed is not None:
            params["seed"] = seed
    elif family == "ideogram":
        params = {"prompt": prompt, "width": width, "height": height}
    else:
        params = {"prompt": prompt}

    dropped = sorted(
        flag for flag in explicit
        if not any(key in params for key in _FLAG_CARRIERS.get(flag, ()))
    )
    return params, dropped


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

    # Every one of these defaults to None in argparse precisely so that "the
    # operator typed it" and "the operator left it alone" stay distinguishable
    # here. `--format` used to default to "png", which made every run look like
    # a run that had asked for a format.
    explicit = {name for name in _FLAG_CARRIERS if getattr(args, name, None) is not None}

    width = args.width or 1024
    height = args.height or 1024
    aspect = args.aspect or "16:9"
    count = args.count or 1
    fmt = args.format or "png"

    is_svg = "svg" in alias
    ext = ".svg" if is_svg else f".{fmt}"
    # The tool's own name is provisional: `_save_outputs` corrects it from the
    # returned bytes. A name the operator gave with -o is kept as given.
    output_path = Path(args.output) if args.output else _unique_path(
        _default_output_dir() / f"design-{_timestamp()}{ext}")

    info(f"Model: {alias} ({model['id']})")
    info(f"Prompt: {args.prompt[:120]}{'...' if len(args.prompt) > 120 else ''}")
    if args.output:
        info(f"Output: {output_path.resolve()}")
    else:
        # Not the filename. The extension is decided by the returned bytes and
        # the number of files by how many URLs come back, so naming one path
        # here would be a promise this command cannot keep. The "Saved" lines
        # below carry the paths that exist.
        info(f"Output directory: {output_path.parent.resolve()}")

    input_params, dropped = _build_generate_input(
        family=model["family"], prompt=args.prompt,
        width=width, height=height, aspect=aspect,
        count=count, fmt=fmt, seed=args.seed, explicit=explicit,
    )
    if dropped:
        warn(f"Not sent to '{alias}' ({model['family']} family): "
             f"{', '.join('--' + flag for flag in dropped)}. "
             f"The model does not accept them. The run continues without them.")

    prediction = _create_prediction(token, model["id"], input_params)
    urls = _normalize_outputs(prediction.get("output", []))
    if not urls:
        error("No output URLs returned.")
        sys.exit(1)

    saved = _save_outputs(urls, output_path, name_from_bytes=not args.output)
    _report_cost(alias, model, len(saved))


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

    output_path = Path(args.output) if args.output else _unique_path(
        _default_output_dir() / f"edit-{_timestamp()}{image_path.suffix}")

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

    saved = _save_outputs(urls, output_path, name_from_bytes=not args.output)
    _report_cost(alias, model, len(saved))


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

    output_path = Path(args.output) if args.output else _unique_path(
        _default_output_dir() / f"upscale-{_timestamp()}{image_path.suffix}")

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

    saved = _save_outputs(urls, output_path, name_from_bytes=not args.output)
    _report_cost(alias, model, len(saved))


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

    output_path = Path(args.output) if args.output else _unique_path(
        _default_output_dir() / f"nobg-{_timestamp()}.png")

    info(f"Model: eraser ({model['id']})")
    info(f"Image: {image_path.resolve()}")

    uploaded_url = _upload_file(image_path, token)
    input_params = {"image": uploaded_url}
    prediction = _create_prediction(token, model["id"], input_params)

    urls = _normalize_outputs(prediction.get("output", []))
    if not urls:
        error("No output URLs returned.")
        sys.exit(1)

    saved = _save_outputs(urls, output_path, name_from_bytes=not args.output)
    _report_cost("eraser", model, len(saved))


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
        entries.append((alias, m["type"], _cost_cell(m), m["description"]))

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
    gen_parser.add_argument("--count", type=int, default=None, help="Number of images (flux/banana, default 1)")
    # default=None, not "png". The tool has to be able to tell a format the
    # operator asked for from one it assumed, because the two families that
    # never receive the flag are reported differently in each case.
    gen_parser.add_argument("--format", default=None, choices=["png", "jpg", "webp"], help="Output format (flux/banana, default png)")
    gen_parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility (flux/banana)")
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
