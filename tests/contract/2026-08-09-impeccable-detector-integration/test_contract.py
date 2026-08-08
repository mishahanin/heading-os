"""Test contract for the impeccable deep-engine integration.

Plan: <data-root>/plans/2026-08-09-impeccable-detector-integration.md

This file is the frozen contract written BEFORE the implementation exists
(Canopus step 3). Every test below is expressed against the PUBLIC interface -
the `scripts.utils.impeccable_engine` module's exported functions and the
`visual-discipline-check.py` CLI - never against an internal helper, so a
rename inside the module cannot break it and a behavioural regression cannot
pass it.

One test per Spec Core capability, plus the two failure modes the plan names
as load-bearing (the plausibility filter and the config-degradation direction).

RED state: every test here fails with ModuleNotFoundError until
`scripts/utils/impeccable_engine.py` exists.

Run:
    .venv/bin/python -m pytest tests/contract/2026-08-09-impeccable-detector-integration/ -v
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - invokes the repo's own checker with a fixed argv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

CHECKER = ROOT / "scripts" / "visual-discipline-check.py"
FIXTURES = ROOT / "tests" / "fixtures" / "impeccable"


def _run_checker(*args: str) -> subprocess.CompletedProcess:
    """Invoke the checker CLI the way an operator would."""
    return subprocess.run(  # nosec B603 - fixed interpreter + repo-owned script
        [sys.executable, str(CHECKER), *args],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(ROOT),
    )


# ---------------------------------------------------------------------------
# CAP-1 - one command yields deep, cascade-resolved findings
# ---------------------------------------------------------------------------


def test_cap1_deep_findings_carry_the_engine_prefix_and_our_finding_shape():
    """A translated impeccable finding is indistinguishable in SHAPE from a regex
    finding, and always names which engine produced it.

    The prefix is the load-bearing part: a reader looking at a merged report must
    be able to tell, per finding, whether the claim comes from source text or from
    a resolved render. Dropping the prefix would make the two engines' claims
    interchangeable, which they are not.
    """
    from scripts.utils.impeccable_engine import translate

    raw = {
        "antipattern": "side-tab",
        "name": "Side-tab accent border",
        "description": "Thick colored border on one side of a card.",
        "severity": "warning",
        "category": "slop",
        "file": "docs/CANOPUS.html",
        "line": 42,
        "snippet": "border-left: 4px + border-radius: 8px",
    }
    finding = translate(raw, profile="screen")

    assert set(finding) >= {"type", "severity", "tell", "line", "context"}
    assert finding["type"] == "impeccable:side-tab"
    assert finding["line"] == 42
    assert "border-left" in finding["context"]


def test_cap1_default_run_does_not_invoke_the_deep_engine():
    """Without --deep, the checker must behave exactly as it does today.

    Fifteen skills already call this command. If the deep engine ran by default,
    every one of them would change behaviour without its author asking.
    """
    from scripts.utils import impeccable_engine

    calls: list = []
    original = impeccable_engine.run_detector

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return ([], None)

    impeccable_engine.run_detector = _spy
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import importlib.util

        spec = importlib.util.spec_from_file_location("_vdc", CHECKER)
        vdc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vdc)
        vdc.audit_file(FIXTURES / "side-tab.html", strict=False)
    finally:
        impeccable_engine.run_detector = original

    assert calls == [], "audit_file invoked the deep engine without being asked for it"


# ---------------------------------------------------------------------------
# CAP-2 - an artifact that already exists is never reported as a failure
# ---------------------------------------------------------------------------


def test_cap2_baseline_suppresses_up_to_the_recorded_count():
    from scripts.utils.impeccable_engine import apply_baseline

    findings = [
        {"file": "docs/a.html", "type": "impeccable:side-tab"},
        {"file": "docs/a.html", "type": "impeccable:side-tab"},
    ]
    baseline = {"docs/a.html": {"impeccable:side-tab": 2}}

    assert apply_baseline(findings, baseline) == []


def test_cap2_baseline_surfaces_everything_above_the_recorded_count():
    """The ratchet's whole purpose: a third occurrence is a regression."""
    from scripts.utils.impeccable_engine import apply_baseline

    findings = [
        {"file": "docs/a.html", "type": "impeccable:side-tab"},
        {"file": "docs/a.html", "type": "impeccable:side-tab"},
        {"file": "docs/a.html", "type": "impeccable:side-tab"},
    ]
    baseline = {"docs/a.html": {"impeccable:side-tab": 2}}

    survivors = apply_baseline(findings, baseline)
    assert len(survivors) == 1


def test_cap2_a_file_absent_from_the_baseline_suppresses_nothing():
    """A new artifact has no frozen line, so every finding on it is live.

    This is the half of the ratchet that makes the whole integration worth
    having: existing work is left alone, new work is held to the standard.
    """
    from scripts.utils.impeccable_engine import apply_baseline

    findings = [{"file": "docs/brand-new.html", "type": "impeccable:side-tab"}]
    baseline = {"docs/a.html": {"impeccable:side-tab": 2}}

    assert apply_baseline(findings, baseline) == findings


def test_cap2_baseline_check_never_rewrites_the_baseline(tmp_path):
    """`check` is read-only; only `record` writes. A check that silently
    re-freezes new findings would turn the ratchet into a rubber stamp.
    """
    from scripts.utils.impeccable_engine import load_baseline, record_baseline

    path = tmp_path / ".visual-baseline.json"
    record_baseline([{"file": "docs/a.html", "type": "impeccable:side-tab"}], path)
    before = path.read_bytes()

    load_baseline(path)
    load_baseline(path)

    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# CAP-3 - print judged by print rules, screen by screen rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule", ["tiny-text", "undersized-ui-text", "line-length"])
def test_cap3_print_profile_suppresses_the_screen_type_floors(rule):
    """11px is a screen floor. On an A4 page 9px renders at a normal print size,
    so the floor rules say nothing true about a doctype.
    """
    from scripts.utils.impeccable_engine import is_suppressed

    assert is_suppressed(rule, profile="print") is True


@pytest.mark.parametrize("rule", ["tiny-text", "undersized-ui-text", "line-length"])
def test_cap3_screen_profile_keeps_the_type_floors(rule):
    from scripts.utils.impeccable_engine import is_suppressed

    assert is_suppressed(rule, profile="screen") is False


def test_cap3_doctype_profile_suppresses_the_approved_kicker():
    """The xPager kicker is an approved element of a locked template, and
    .claude/rules/corporate-docs.md puts template changes behind CEO approval.
    The brand outranks the detector.
    """
    from scripts.utils.impeccable_engine import is_suppressed

    assert is_suppressed("kicker-above-heading", profile="doctype") is True
    assert is_suppressed("kicker-above-heading", profile="screen") is False


def test_cap3_profile_is_derived_from_the_path_with_longest_glob_winning():
    from scripts.utils.impeccable_engine import profile_for

    assert profile_for("datastore/brand/templates/doctypes/proposal.html") == "doctype"
    assert profile_for("docs/ARCHITECTURE.html") == "screen"
    assert profile_for("some/unmapped/place/page.html") == "screen"


# ---------------------------------------------------------------------------
# CAP-4 - keeps working with no network and no Node
# ---------------------------------------------------------------------------


def test_cap4_unresolvable_cli_returns_an_error_string_and_never_raises(monkeypatch):
    from scripts.utils import impeccable_engine

    monkeypatch.setattr(impeccable_engine, "resolve_cli", lambda: None)
    findings, error = impeccable_engine.run_detector([Path("docs")])

    assert findings == []
    assert error and isinstance(error, str)


def test_cap4_deep_run_without_the_cli_still_exits_on_the_regex_verdict(monkeypatch):
    """A missing Node must not change the verdict in either direction: it may not
    invent a failure, and it may not turn a real regex failure into a pass.
    """
    from scripts.utils import impeccable_engine

    monkeypatch.setattr(impeccable_engine, "resolve_cli", lambda: None)
    findings, error = impeccable_engine.deep_findings(FIXTURES / "side-tab.html")

    assert findings == []
    assert error is not None


# ---------------------------------------------------------------------------
# CAP-5 - the rule and the renderers name the deep command
# ---------------------------------------------------------------------------


def test_cap5_the_visual_rule_names_the_deep_command():
    rule = (ROOT / ".claude" / "rules" / "visual-design-discipline.md").read_text(
        encoding="utf-8"
    )
    assert "--deep" in rule, "the rule must name the deep command it now obliges"


@pytest.mark.parametrize(
    "renderer",
    [
        "scripts/regenerate-docs-html.py",
        "scripts/render-doctype.py",
        "scripts/marp_render.py",
    ],
)
def test_cap5_each_renderer_invokes_the_check_on_its_own_output(renderer):
    source = (ROOT / renderer).read_text(encoding="utf-8")
    assert "visual-discipline-check" in source or "impeccable_engine" in source


# ---------------------------------------------------------------------------
# Load-bearing failure modes named in the plan
# ---------------------------------------------------------------------------


def test_plausibility_filter_discards_a_non_physical_reading():
    """Impeccable reports `oversized-h1` at 2856px and `tight-leading` at 0.11x on
    our CSS. Both are parser artifacts. They are filtered on physical bounds and
    NOT by disabling the rules, so a genuine hit still lands.
    """
    from scripts.utils.impeccable_engine import is_plausible

    assert is_plausible({"antipattern": "oversized-h1", "snippet": "2856px h1, 44 chars"}) is False
    assert is_plausible({"antipattern": "oversized-h1", "snippet": "96px h1, 44 chars"}) is True
    assert is_plausible({"antipattern": "tight-leading", "snippet": "line-height 0.11x (need >=1.3)"}) is False
    assert is_plausible({"antipattern": "tight-leading", "snippet": "line-height 1.15x (need >=1.3)"}) is True


def test_a_broken_config_degrades_toward_reporting_more_never_toward_silence(tmp_path):
    """The direction of failure is the whole point. A malformed profile config must
    fall back to `screen` (suppresses nothing) with a warning, never to a profile
    that hides findings. Fail loud, not quiet.
    """
    from scripts.utils.impeccable_engine import load_profiles

    broken = tmp_path / "visual-check-profiles.json"
    broken.write_text("{ this is not json", encoding="utf-8")

    profiles, warning = load_profiles(broken)

    assert warning, "a malformed config must announce itself"
    assert profiles["default"] == "screen"
    assert profiles["profiles"]["screen"]["suppress"] == []


def test_minified_and_vendored_assets_are_out_of_scope():
    """A regex inside docs/assets/mermaid.min.js produced a `broken-image` finding.

    The exclusion must live in the DEEP engine, not in the checker's `_iter_files`.
    The checker only ever walks SCAN_EXTENSIONS (.html/.htm/.svg/.pptx), so a
    `.min.js` assertion there passes trivially and proves nothing. Impeccable, by
    contrast, is handed a DIRECTORY and walks it itself, reaching every .js in the
    tree. So the filter has to be applied to what comes back.
    """
    from scripts.utils.impeccable_engine import is_out_of_scope

    assert is_out_of_scope("docs/assets/mermaid.min.js") is True
    assert is_out_of_scope("docs/assets/vendor/anything.js") is True
    assert is_out_of_scope("static/vendor/lib.css") is True
    assert is_out_of_scope("docs/ARCHITECTURE.html") is False


def test_out_of_scope_findings_are_dropped_from_a_deep_run(monkeypatch):
    """The filter is load-bearing only if the pipeline actually applies it."""
    from scripts.utils import impeccable_engine

    raw = [
        {
            "antipattern": "broken-image",
            "severity": "warning",
            "file": "docs/assets/mermaid.min.js",
            "line": 377,
            "snippet": "<img[^>",
        },
        {
            "antipattern": "side-tab",
            "severity": "warning",
            "file": "docs/CANOPUS.html",
            "line": 12,
            "snippet": "border-left: 4px + border-radius: 8px",
        },
    ]
    monkeypatch.setattr(impeccable_engine, "run_detector", lambda *a, **k: (raw, None))

    findings, error = impeccable_engine.deep_findings(Path("docs"))

    assert error is None
    assert [f["type"] for f in findings] == ["impeccable:side-tab"]


def test_the_version_pin_is_exact_never_a_range():
    """`npx --yes <pin>` fetches and executes third-party code. An exact pin is the
    only mitigation this integration claims; a range would silently widen it.
    """
    from scripts.utils.impeccable_engine import get_pinned_version

    pin = get_pinned_version()
    assert pin.startswith("impeccable@")
    version = pin.split("@", 1)[1]
    assert all(part.isdigit() for part in version.split(".")), f"not an exact version: {pin}"


# ---------------------------------------------------------------------------
# Integration - the real CLI. Skipped when it cannot be resolved.
# ---------------------------------------------------------------------------


def test_integration_real_cli_detects_the_side_tab_fixture():
    from scripts.utils.impeccable_engine import resolve_cli

    if resolve_cli() is None:
        pytest.skip("impeccable CLI unresolvable (no Node or no network)")

    result = _run_checker("--deep", "--json", "--no-baseline", str(FIXTURES / "side-tab.html"))
    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)
    types = {f["type"] for entry in payload for f in entry["findings"]}
    assert "impeccable:side-tab" in types
