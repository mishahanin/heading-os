#!/usr/bin/env python3
"""
claude_models.py - resolve a Claude model FAMILY to the newest release in it.

Callers ask for a family ("opus", "sonnet", "haiku", "fable"), never a version.
`latest("sonnet")` returns whatever the newest Sonnet is on the day it runs, so
a new flagship reaches every caller with no code edit and no config bump.

Why a resolver rather than a constant. Every Claude model id is a PINNED
snapshot, including the dateless ones: `claude-sonnet-5` is a specific release,
not an evergreen pointer to "current Sonnet". So there is no literal anyone can
type that stays correct. The only thing that stays correct is a lookup, and the
Models API is the lookup Anthropic ships for exactly this: it returns models
newest-first with a `created_at`, so "newest in this family" is a filter and a
max, not a guess.

Resolution order, first hit wins:

  1. `config/claude-models.json` - an explicit operator override. Normally
     ABSENT. It exists to pin a family deliberately (reproducing an old eval,
     dodging a bad release), never as the routine way to stay current.
  2. A fresh entry in the local cache (TTL below), so the common path costs no
     network call.
  3. A live Models API call, which refills the cache.
  4. A stale cache entry, when the API is unreachable.
  5. `BASELINE` below - the floor, and the ONLY version literal in the engine
     outside the version-history files.

The chain never raises on a network, key, or parse failure: an offline machine
or a public clone with no API key resolves to BASELINE and keeps working.
`tests/test_no_claude_model_pins.py` fails if a Claude version literal appears
anywhere else under `scripts/`.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.workspace import get_workspace_root  # noqa: E402

# The families a caller may ask for. A family is a capability tier, not a
# release: "opus" means heaviest reasoning, "haiku" means fastest, and which
# concrete model serves that tier is this module's problem, not the caller's.
FAMILIES = ("opus", "sonnet", "haiku", "fable")

# Floor, not a pin. Used only when the override, the cache, and the API have all
# failed to answer. Correct on 2026-08-09; going stale here degrades a caller to
# an older model, it never breaks one. Refresh when a family's newest release
# changes AND the machine is expected to run offline.
BASELINE = {
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5-20251001",
    "fable": "claude-fable-5",
}

CONFIG_RELPATH = "config/claude-models.json"
CACHE_RELPATH = ".cache/claude-models.json"
CACHE_TTL_SECONDS = 24 * 60 * 60
API_URL = "https://api.anthropic.com/v1/models?limit=100"
API_TIMEOUT_SECONDS = 8


def config_path() -> Path:
    return get_workspace_root() / CONFIG_RELPATH


def cache_path() -> Path:
    return get_workspace_root() / CACHE_RELPATH


def _read_json(path: Path) -> dict:
    """Read a JSON object, returning {} on any failure. Never raises."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        print(f"Warning: could not read {path} ({e}); ignoring it.", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def _api_key() -> str | None:
    """The Anthropic key from the environment or `.env`, or None if absent.

    A missing key is a normal state, not an error: a public clone has none, and
    the caller still gets a working model id from BASELINE.
    """
    # `key.strip()`, not `key`, decides whether the environment answered. A
    # variable set to whitespace - the ordinary result of
    # `export ANTHROPIC_API_KEY="$VAR"` in a wrapper where `$VAR` is empty-but-
    # spaced - is truthy, so this returned None immediately and the `.env`
    # fallback below never ran, even with a valid key sitting in the file.
    # MEASURED 2026-08-30: `ANTHROPIC_API_KEY=" "` yielded None while
    # `ANTHROPIC_API_KEY=""` correctly read the key out of `.env`. The failure
    # is silent - `fetch_from_api` returns `{}` before printing anything when
    # there is no key - so every resolution degrades to cache or BASELINE with
    # nothing said.
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key and key.strip():
        return key.strip()
    try:
        from scripts.utils.paths import load_env

        load_env()
    except Exception:  # noqa: BLE001 - env loading is best-effort by design
        return None
    key = os.environ.get("ANTHROPIC_API_KEY")
    return key.strip() if key else None


def _family_of(model_id: str) -> str | None:
    """Which family a model id belongs to, or None if it is not one of ours."""
    for family in FAMILIES:
        if model_id.startswith(f"claude-{family}-"):
            return family
    return None


def fetch_from_api() -> dict:
    """Newest model per family from the live Models API. {} on any failure.

    The API returns models newest-first and carries `created_at`, so the newest
    member of a family is a max over that field rather than a naming heuristic.
    Sorting explicitly instead of trusting list order keeps the answer right if
    the ordering contract ever changes.
    """
    key = _api_key()
    if not key:
        return {}
    request = urllib.request.Request(
        API_URL,
        headers={"anthropic-version": "2023-06-01", "x-api-key": key},
    )
    try:
        # noqa S310: API_URL is a module constant, https, never caller-supplied.
        with urllib.request.urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError,
            json.JSONDecodeError, UnicodeDecodeError, TimeoutError) as e:
        print(f"Warning: Models API unreachable ({e}); using cached or baseline "
              f"model pins.", file=sys.stderr)
        return {}

    newest: dict[str, tuple[str, str]] = {}
    for entry in payload.get("data") or []:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        created = entry.get("created_at") or ""
        if not isinstance(model_id, str) or not isinstance(created, str):
            continue
        family = _family_of(model_id)
        if family is None:
            continue
        if family not in newest or created > newest[family][1]:
            newest[family] = (model_id, created)
    return {family: model_id for family, (model_id, _) in newest.items()}


# Per-process memo. A resolution costs a file read at best and an 8-second API
# timeout at worst, and callers ask repeatedly inside loops (email-intelligence
# resolves once per batch of five conversations, on a 5-minute tick). A process
# is short enough that a model shipped mid-run can wait for the next one.
_RESOLVED: dict[str, str] = {}

# One failed fetch per process, not one per call. Without this a degraded API
# turns every later call into another full timeout: measured 21.9 seconds for
# three calls before this existed.
_FETCH_FAILED = False


def _announce_moves(previous: dict, current: dict) -> None:
    """Log a family whose newest release changed since the last cache write.

    Adoption here is automatic, unlike the council pins next door, which move
    only on an explicit `--set`. Automatic is right for a family resolver, but
    silent is not: `draft_critique` reviews outbound email and
    `skill-trigger-test` gates `/push-updates`, and a judge that changes model
    with nothing in the record is a verdict nobody can explain later. One
    stderr line per move is the whole audit trail, and it lands in the daemon
    logs that already capture stderr.
    """
    for family, model in sorted(current.items()):
        was = previous.get(family)
        if was and was != model:
            print(f"claude_models: {family} moved {was} -> {model}", file=sys.stderr)


def _write_cache(models: dict) -> None:
    """Persist the resolved map with a timestamp. A failed write is not fatal."""
    path = cache_path()
    _announce_moves(_cached(allow_stale=True), models)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": time.time(), "models": models}, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except OSError as e:
        print(f"Warning: could not cache model list ({e}).", file=sys.stderr)


def _cached(allow_stale: bool) -> dict:
    """The cached family map, or {} when absent or (unless allowed) expired."""
    data = _read_json(cache_path())
    models = data.get("models")
    if not isinstance(models, dict):
        return {}
    if not allow_stale:
        fetched_at = data.get("fetched_at")
        if not isinstance(fetched_at, (int, float)):
            return {}
        if time.time() - fetched_at > CACHE_TTL_SECONDS:
            return {}
    return {k: v for k, v in models.items() if isinstance(v, str) and v.strip()}


def latest(family: str, *, refresh: bool = False) -> str:
    """The newest model id in `family`. Raises ValueError on an unknown family.

    `refresh=True` skips the TTL and forces one API call, for a caller that
    wants today's answer rather than yesterday's cached one.
    """
    global _FETCH_FAILED

    if family not in BASELINE:
        raise ValueError(
            f"Unknown model family: {family!r}. Known: {', '.join(FAMILIES)}"
        )

    override = _read_json(config_path()).get(family)
    if isinstance(override, str) and override.strip():
        return override.strip()

    if refresh:
        _RESOLVED.clear()
        _FETCH_FAILED = False
    else:
        memo = _RESOLVED.get(family)
        if memo:
            return memo
        hit = _cached(allow_stale=False).get(family)
        if hit:
            _RESOLVED[family] = hit
            return hit

    if not _FETCH_FAILED:
        fetched = fetch_from_api()
        if fetched:
            _write_cache({**_cached(allow_stale=True), **fetched})
            _RESOLVED.update(fetched)
            if family in fetched:
                return fetched[family]
        else:
            _FETCH_FAILED = True

    resolved = _cached(allow_stale=True).get(family) or BASELINE[family]
    _RESOLVED[family] = resolved
    return resolved


def load_all(*, refresh: bool = False) -> dict:
    """Resolved {family: model id} for every family.

    One fetch, not one per family: a single Models API response already carries
    every family, so asking four times is four round-trips for one answer.

    "One fetch" holds on the DEGRADED path too, which is where it was breaking.
    This cleared the memo and fetched, but never touched `_FETCH_FAILED` either
    way - so when the fetch came back empty the comprehension below called
    `latest()`, which found no memo, saw `_FETCH_FAILED` still False, and fired
    a SECOND `fetch_from_api()` with its own up-to-8-second timeout. MEASURED
    2026-08-30 with a counter around `fetch_from_api` and an unreachable API:
    `load_all(refresh=True)` invoked it twice, `latest("opus", refresh=True)`
    once. The flag handling now mirrors `latest(refresh=True)` exactly - reset
    on entry, raised when the direct fetch yields nothing - because two refresh
    entry points managing one shared flag differently is what let them disagree.
    """
    global _FETCH_FAILED

    if refresh:
        _RESOLVED.clear()
        _FETCH_FAILED = False
        fetched = fetch_from_api()
        if fetched:
            _write_cache({**_cached(allow_stale=True), **fetched})
            _RESOLVED.update(fetched)
        else:
            _FETCH_FAILED = True
    return {family: latest(family) for family in FAMILIES}


def resolve(name: str | None, *, default_family: str = "sonnet") -> str:
    """Resolve a caller-supplied value that may be a family OR a literal id.

    A family name goes through `latest`. Anything else is passed through
    untouched, so an operator can still hand a specific id to a CLI flag for a
    one-off reproduction without the resolver second-guessing them. `None`
    resolves the default family.
    """
    if name is None or not str(name).strip():
        return latest(default_family)
    value = str(name).strip()
    return latest(value) if value in BASELINE else value


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Resolve Claude model families to the newest release in each."
    )
    parser.add_argument("family", nargs="?", choices=list(FAMILIES),
                        help="One family; omit to print the whole table.")
    parser.add_argument("--refresh", action="store_true",
                        help="Ignore the cache TTL and query the Models API now.")
    args = parser.parse_args()

    if args.family:
        print(latest(args.family, refresh=args.refresh))
        return 0
    for family, model in load_all(refresh=args.refresh).items():
        print(f"{family:8} {model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
