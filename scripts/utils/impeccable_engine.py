#!/usr/bin/env python3
"""Deep design-detection engine: the impeccable CLI behind our own facade.

`scripts/visual-discipline-check.py` matches regexes against file contents. It
sees `font-family: Inter` written down; it cannot see what a colour resolved to
against the surface behind it, whether a heading hierarchy holds, or whether an
accent stripe sits on a rounded corner. This module adds an engine that can:
the impeccable CLI (github.com/pbakaus/impeccable, Apache 2.0), which parses the
HTML, resolves the CSS cascade, and computes real values.

It is deliberately a translator, not a second opinion. Findings come back in the
exact shape the regex engine produces, prefixed `impeccable:` so a reader can
always tell which engine made a claim, and merge into the existing report with
no special-casing downstream.

Three things stand between the CLI's output and a finding we act on:

  profiles      An A4 document is judged by print rules, a screen surface by
                screen rules, and a locked corporate template by neither where
                the brand has already decided. See config/visual-check-profiles.json.
  plausibility  The parser emits physically impossible readings on some of our
                CSS (an h1 at 2856px). Those are filtered on VALUE, never by
                disabling the rule, so a genuine hit on the same rule still lands.
  baseline      A per-(file, rule) ratchet, same shape as .lint-baseline.json.
                What exists on the integration date is frozen; the gate fires on
                what appears above the line.

Every failure path here degrades toward reporting MORE, never toward silence: an
unresolvable CLI, a timeout, malformed JSON and a broken config all return a
warning string and an empty finding list rather than raising, and none of them
may change a verdict the regex engine reached on its own.

Usage (as a library; the CLI surface is visual-discipline-check.py):
    from scripts.utils.impeccable_engine import deep_findings, apply_baseline
    findings, error = deep_findings(Path("docs"))

Consumed by: scripts/visual-discipline-check.py, scripts/regenerate-docs-html.py,
scripts/render-doctype.py, scripts/marp_render.py.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import subprocess  # nosec B404 - runs a pinned CLI with a fixed argument vector
import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.workspace import get_workspace_root  # noqa: E402

# ============================================================
# Configuration
# ============================================================

FALLBACK_PIN = "impeccable@3.5.0"
VERSION_PIN_FILE = "scripts/.impeccable-version"
PROFILES_FILE = "config/visual-check-profiles.json"
BASELINE_FILE = ".visual-baseline.json"

DEFAULT_TIMEOUT = 300

# Used when config/visual-check-profiles.json is missing or unreadable. Suppresses
# nothing and maps nothing, so a broken config cannot hide a finding.
_SAFE_PROFILES = {
    "default": "screen",
    "profiles": {"screen": {"description": "fallback", "suppress": {}}},
    "path_profiles": [],
    "plausibility": {},
    "out_of_scope": {"suffixes": [], "path_fragments": []},
}

_NUMBER = re.compile(r"(\d+(?:\.\d+)?)")


# ============================================================
# Version pin
# ============================================================


def get_pinned_version() -> str:
    """Return the exact pinned CLI spec, e.g. `impeccable@3.5.0`.

    `npx --yes <spec>` fetches and executes third-party code at call time. An
    exact pin is the only mitigation this integration claims, so a range must
    never appear here; `tests/contract/.../test_contract.py` asserts it.
    """
    pin_path = get_workspace_root() / VERSION_PIN_FILE
    if pin_path.exists():
        pinned = pin_path.read_text(encoding="utf-8").strip()
        if pinned:
            return pinned
    return FALLBACK_PIN


def resolve_cli() -> list[str] | None:
    """Resolve the argv prefix that invokes the detector, or None.

    Prefers an installed `impeccable` whose version matches the pin, and falls
    back to `npx --yes <pin>`. Returns None when neither is available, which is
    a supported state, not an error: the regex engine still runs.
    """
    pin = get_pinned_version()
    wanted = pin.rsplit("@", 1)[-1] if "@" in pin else pin

    local = shutil.which("impeccable")
    if local:
        try:
            probe = subprocess.run(  # nosec B603 - resolved absolute path, fixed args
                [local, "--version"], capture_output=True, text=True, timeout=30
            )
            if probe.returncode == 0 and wanted in probe.stdout:
                return [local]
        except (OSError, subprocess.SubprocessError):
            pass

    if shutil.which("npx"):
        return ["npx", "--yes", pin]

    return None


# ============================================================
# Profiles
# ============================================================


def load_profiles(path: Path | None = None) -> tuple[dict, str | None]:
    """Load the calibration config. Returns (profiles, warning).

    A missing or malformed file falls back to a screen-only config that
    suppresses nothing, and says so. The direction matters: a config we cannot
    read must make the check noisier, never quieter, or an accidental syntax
    error silently disables the gate.
    """
    path = path or (get_workspace_root() / PROFILES_FILE)
    if not path.exists():
        return dict(_SAFE_PROFILES), f"profile config not found at {path}; falling back to screen (suppresses nothing)"
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return dict(_SAFE_PROFILES), f"profile config unreadable ({exc}); falling back to screen (suppresses nothing)"

    if not isinstance(loaded.get("profiles"), dict) or "screen" not in loaded.get("profiles", {}):
        return dict(_SAFE_PROFILES), "profile config has no `screen` profile; falling back to screen (suppresses nothing)"

    loaded.setdefault("default", "screen")
    loaded.setdefault("path_profiles", [])
    loaded.setdefault("plausibility", {})
    loaded.setdefault("out_of_scope", {"suffixes": [], "path_fragments": []})
    return loaded, None


def _suppressed_rules(profile: str, profiles: dict) -> dict:
    """Resolve a profile's suppression map, following `extends` once per level."""
    defined = profiles.get("profiles", {})
    seen: set[str] = set()
    merged: dict = {}
    current = profile
    while current and current in defined and current not in seen:
        seen.add(current)
        entry = defined[current]
        for rule, reason in (entry.get("suppress") or {}).items():
            merged.setdefault(rule, reason)
        current = entry.get("extends")
    return merged


def is_suppressed(rule: str, profile: str = "screen", profiles: dict | None = None) -> bool:
    """True when `rule` is calibrated off for `profile`."""
    if profiles is None:
        profiles, _ = load_profiles()
    return rule in _suppressed_rules(profile, profiles)


def suppression_reason(rule: str, profile: str, profiles: dict | None = None) -> str | None:
    if profiles is None:
        profiles, _ = load_profiles()
    return _suppressed_rules(profile, profiles).get(rule)


def profile_for(file_path: str | Path, profiles: dict | None = None) -> str:
    """Pick the profile for a path. Longest matching glob wins; default otherwise."""
    if profiles is None:
        profiles, _ = load_profiles()

    rel = relative_path(file_path)
    best_len = -1
    best = profiles.get("default", "screen")
    for entry in profiles.get("path_profiles", []):
        glob = entry.get("glob", "")
        if fnmatch.fnmatch(rel, glob) and len(glob) > best_len:
            best_len = len(glob)
            best = entry.get("profile", best)
    return best


# ============================================================
# Scope and plausibility
# ============================================================


def is_out_of_scope(file_path: str | Path, profiles: dict | None = None) -> bool:
    """True for files that are not our design surface.

    Impeccable is handed a DIRECTORY and walks it itself, so it reaches files our
    own checker never visits - minified bundles above all. A regex inside
    `docs/assets/mermaid.min.js` produced a `broken-image` finding on the first
    real run; a minified vendor bundle is not something anyone designed.
    """
    if profiles is None:
        profiles, _ = load_profiles()
    scope = profiles.get("out_of_scope", {})
    rel = "/" + relative_path(file_path).strip("/")

    for suffix in scope.get("suffixes", []):
        if rel.lower().endswith(suffix.lower()):
            return True
    return any(fragment in rel for fragment in scope.get("path_fragments", []))


def is_plausible(finding: dict, profiles: dict | None = None) -> bool:
    """False when a finding claims a physically impossible value.

    The parser reports an h1 at 2856px and a line-height of 0.11x on our CSS.
    Neither is a defect; both are the engine misreading something. Filtering on
    the VALUE rather than disabling the rule keeps the rule live for real hits -
    a genuine 96px h1 on a long headline still lands.
    """
    if profiles is None:
        profiles, _ = load_profiles()
    bounds = profiles.get("plausibility", {}).get(finding.get("antipattern", ""))
    if not bounds:
        return True

    match = _NUMBER.search(finding.get("snippet", "") or "")
    if not match:
        return True

    value = float(match.group(1))
    if "max" in bounds and value > bounds["max"]:
        return False
    return not ("min" in bounds and value < bounds["min"])


# ============================================================
# Detector invocation
# ============================================================


def run_detector(paths, timeout: int = DEFAULT_TIMEOUT) -> tuple[list[dict], str | None]:
    """Run `impeccable detect --json` over `paths`. Returns (raw_findings, error).

    Never raises. Every failure - no CLI, non-zero exit without parseable JSON,
    timeout, malformed output - comes back as a human-readable string that the
    caller reports and moves past. A design check that crashes a renderer because
    Node is missing would be a worse tool than no design check.
    """
    argv_prefix = resolve_cli()
    if argv_prefix is None:
        return [], (
            "impeccable CLI unresolvable (no matching binary and no npx on PATH); "
            "deep design checks skipped, regex engine unaffected"
        )

    targets = [str(p) for p in (paths if isinstance(paths, (list, tuple)) else [paths])]
    argv = [*argv_prefix, "detect", "--json", *targets]

    # stdout goes to a FILE, not to a pipe, and this is not a style choice.
    #
    # The upstream CLI exits without waiting for its asynchronous stdout to
    # drain. Writing to a pipe, Node buffers 64 KiB and the process dies with the
    # rest unflushed: a scan of docs/ returns exactly 65536 bytes of a 168409-byte
    # document, so `json.loads` fails mid-string and every finding is lost. The
    # same run redirected to a file returns all 350 findings, because Node writes
    # to a regular file synchronously.
    #
    # Measured 2026-08-09 against impeccable@3.5.0, identical through `npx` and a
    # local checkout, so it is the CLI and not the invocation. A pipe would have
    # silently degraded every directory-sized scan to nothing while reporting
    # "deep design checks skipped" as though Node were missing.
    try:
        with tempfile.TemporaryDirectory(prefix="impeccable-") as tmpdir:
            out_path = Path(tmpdir) / "detect.json"
            with out_path.open("w", encoding="utf-8") as sink:
                proc = subprocess.run(  # nosec B603 - fixed argv, no shell, pinned spec
                    argv, stdout=sink, stderr=subprocess.PIPE, text=True, timeout=timeout
                )
            stdout = out_path.read_text(encoding="utf-8").strip()
    except subprocess.TimeoutExpired:
        return [], f"impeccable timed out after {timeout}s; deep design checks skipped"
    except OSError as exc:
        return [], f"impeccable could not be started ({exc}); deep design checks skipped"
    if not stdout:
        detail = (proc.stderr or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit {proc.returncode}"
        return [], f"impeccable produced no output ({tail}); deep design checks skipped"

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return [], f"impeccable output was not JSON ({exc}); deep design checks skipped"

    if not isinstance(payload, list):
        return [], "impeccable output was not a finding list; deep design checks skipped"

    return payload, None


# ============================================================
# Translation
# ============================================================


def translate(raw: dict, profile: str = "screen") -> dict:
    """Map one impeccable finding into the shape the regex engine produces.

    The `impeccable:` prefix on `type` is load-bearing: in a merged report a
    reader must be able to tell, per finding, whether the claim came from source
    text or from a resolved render. The two are not interchangeable.
    """
    rule = raw.get("antipattern", "unknown")
    advisory = raw.get("advisory") is True or raw.get("severity") == "advisory"
    line = raw.get("line") or None

    return {
        "type": f"impeccable:{rule}",
        "severity": "warning" if advisory else "error",
        "tell": raw.get("name") or rule,
        "line": line,
        "context": raw.get("snippet") or raw.get("description") or "",
        "file": relative_path(raw.get("file", "")),
        "profile": profile,
        "description": raw.get("description", ""),
    }


def deep_findings(
    target,
    timeout: int = DEFAULT_TIMEOUT,
    profile_override: str | None = None,
) -> tuple[list[dict], str | None]:
    """Run the detector over `target` and return calibrated findings.

    Applies, in order: out-of-scope drop, plausibility filter, profile
    suppression. The profile is resolved per finding from that finding's OWN
    path, so one directory scan spanning print documents and screen surfaces
    calibrates each correctly rather than picking one profile for the batch.

    `profile_override` forces a single profile for every finding, which is what
    `--profile` and the renderers use: a freshly rendered doctype is judged by
    doctype rules even though it lands in a directory the path map has never
    seen. The override participates in SUPPRESSION, not just in the label -
    stamping the name on afterwards would have been a lie the report told itself.
    """
    profiles, config_warning = load_profiles()
    raw, error = run_detector(target, timeout=timeout)
    if error:
        return [], (f"{error} ({config_warning})" if config_warning else error)

    findings = []
    for item in raw:
        path = item.get("file", "")
        if is_out_of_scope(path, profiles):
            continue
        if not is_plausible(item, profiles):
            continue
        profile = profile_override or profile_for(path, profiles)
        if is_suppressed(item.get("antipattern", ""), profile, profiles):
            continue
        findings.append(translate(item, profile))

    return findings, config_warning


# ============================================================
# Baseline (the ratchet)
# ============================================================


def relative_path(file_path: str | Path) -> str:
    """Path relative to the workspace root, forward-slashed, for stable keys."""
    text = str(file_path).replace("\\", "/")
    root = str(get_workspace_root()).replace("\\", "/").rstrip("/") + "/"
    if text.startswith(root):
        text = text[len(root):]
    return text.lstrip("./")


def load_baseline(path: Path | None = None) -> dict:
    """Read the frozen per-(file, rule) counts. Missing file means an empty freeze.

    Read-only by construction: nothing in this module writes the baseline except
    `record_baseline`, so a `check` run can never re-freeze a new finding.
    """
    path = path or (get_workspace_root() / BASELINE_FILE)
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return loaded.get("files", loaded) if isinstance(loaded, dict) else {}


def record_baseline(findings: list[dict], path: Path | None = None) -> dict:
    """Freeze the current findings as the baseline and write it. Returns the map."""
    path = path or (get_workspace_root() / BASELINE_FILE)
    counts: dict[str, dict[str, int]] = {}
    for finding in findings:
        key = relative_path(finding.get("file", ""))
        rule = finding.get("type", "unknown")
        counts.setdefault(key, {})
        counts[key][rule] = counts[key].get(rule, 0) + 1

    ordered = {f: dict(sorted(rules.items())) for f, rules in sorted(counts.items())}
    payload = {
        "_comment": (
            "Frozen design findings for artifacts that existed when the deep engine "
            "was integrated. The gate fires on findings ABOVE these counts, never on "
            "the counts themselves - existing artifacts are not remediated. Regenerate "
            "with `visual-discipline-check.py baseline record --deep <path>` only after "
            "an intentional fix, so the numbers only ever fall."
        ),
        "files": ordered,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return ordered


def apply_baseline(findings: list[dict], baseline: dict | None = None) -> list[dict]:
    """Drop findings covered by the frozen counts; return what stands above them.

    A file absent from the baseline is a NEW file: nothing is suppressed for it.
    That asymmetry is the whole point - existing work is left alone, new work is
    held to the standard.
    """
    if baseline is None:
        baseline = load_baseline()

    remaining = {f: dict(rules) for f, rules in baseline.items()}
    survivors = []
    for finding in findings:
        key = relative_path(finding.get("file", ""))
        rule = finding.get("type", "unknown")
        allowance = remaining.get(key, {}).get(rule, 0)
        if allowance > 0:
            remaining[key][rule] = allowance - 1
            continue
        survivors.append(finding)
    return survivors


# ============================================================
# Renderer-facing report
# ============================================================


def report_for_artifact(path, profile: str | None = None, stream=None) -> int:
    """Print a one-line design verdict for a freshly produced artifact.

    Used by the renderers (docs site, doctype, Marp) on their own output. It
    REPORTS and returns a count; it never raises and never fails the render.
    A renderer that refused to render because a heading level was skipped, or
    because Node was missing, would be a worse tool than no check at all.

    No baseline is applied: the artifact was just created, so there is nothing
    frozen for it and every finding is live.
    """
    stream = stream or sys.stderr
    try:
        findings, note = deep_findings(path, profile_override=profile)
    except Exception as exc:  # noqa: BLE001 - a design check may never break a render
        print(f"[design] check unavailable ({exc})", file=stream)
        return 0

    if note:
        print(f"[design] {note}", file=stream)
        return 0

    if not findings:
        print(f"[design] clean - no deep design findings ({profile or 'auto'} profile).", file=stream)
        return 0

    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["type"]] = counts.get(finding["type"], 0) + 1
    summary = ", ".join(f"{rule.removeprefix('impeccable:')} x{n}" for rule, n in sorted(counts.items()))
    print(f"[design] {len(findings)} finding(s) ({profile or 'auto'} profile): {summary}", file=stream)
    print(f"[design] detail: python scripts/visual-discipline-check.py --deep --no-baseline {path}", file=stream)
    return len(findings)
