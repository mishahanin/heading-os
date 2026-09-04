"""Tests for the impeccable deep-engine integration (graduated test contract).

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

import ast
import json
import os
import subprocess  # nosec B404 - invokes the repo's own checker with a fixed argv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CHECKER = ROOT / "scripts" / "visual-discipline-check.py"
FIXTURES = ROOT / "tests" / "fixtures" / "impeccable"


def _run_checker(*args: str, tmpdir: str | None = None) -> subprocess.CompletedProcess:
    """Invoke the checker CLI the way an operator would.

    `tmpdir` pins TMPDIR for the child. `run_detector` in
    `scripts/utils/impeccable_engine.py` makes its scratch with
    `TemporaryDirectory(prefix="impeccable-")`, which cleans itself up on every
    path Python gets to run -- and on none where it does not. This helper's own
    `timeout=300` and that call's `timeout=300` start at different moments, the
    outer one first, so a slow `npx` fetch has the outer timeout SIGKILL the
    child while it is inside the `with`. Nothing then removes the directory.
    MEASURED 2026-09-04: one surviving `/tmp/impeccable-*` after a full run.

    The child's environment is the only seam that survives a kill, since the
    directory is chosen before Python can be told to clean it up. A `TMPDIR`
    under `tmp_path` puts the orphan inside the tree pytest reclaims.
    """
    env = dict(os.environ)
    if tmpdir is not None:
        env["TMPDIR"] = tmpdir
    return subprocess.run(  # nosec B603 - fixed interpreter + repo-owned script
        [sys.executable, str(CHECKER), *args],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(ROOT),
        env=env,
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
        # The `sys.path.insert(0, ROOT/"scripts")` that stood here was never
        # removed: no `finally`, no fixture. It leaked `<repo>/scripts` onto the
        # path for the REST OF THE XDIST WORKER, where `scripts/firecrawl.py`
        # shadows the installed `firecrawl` SDK for every test that ran after.
        # Measured with a `pytest_sessionfinish` probe: the entry was still
        # there at session end. It was also unnecessary -
        # `scripts/visual-discipline-check.py` puts the repo root on `sys.path`
        # itself before it imports anything.
        import importlib.util

        spec = importlib.util.spec_from_file_location("_vdc", CHECKER)
        vdc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vdc)
        vdc.audit_file(FIXTURES / "side-tab.html", strict=False)
    finally:
        impeccable_engine.run_detector = original

    assert str(ROOT / "scripts") not in sys.path, (
        "this test put the scripts directory on sys.path and left it there; "
        "scripts/firecrawl.py then shadows the firecrawl SDK for every later "
        "test in this worker")

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


def test_cap3_the_longest_glob_wins_when_two_globs_both_match():
    """The tie-break the test above NAMES and never exercised.

    No two globs in the shipped config overlap, so every path there matches at
    most one entry and the `len(glob) > best_len` comparison never decides
    anything. Measured 2026-09-01: replacing it with "first match wins" left
    the whole suite and every neighbouring impeccable test green, while the
    test asserting longest-glob-winning sat one function above.

    Injected profiles rather than the shipped file, so the property is bound to
    the resolver instead of to a config that happens not to overlap today. The
    entries are ordered SHORTEST FIRST, which is the order under which a
    first-match implementation returns the wrong answer.
    """
    from scripts.utils.impeccable_engine import profile_for

    profiles = {
        "default": "screen",
        "profiles": {"screen": {"suppress": {}}, "print": {"suppress": {}},
                     "doctype": {"suppress": {}}},
        "path_profiles": [
            {"glob": "outputs/**", "profile": "print"},
            {"glob": "outputs/documents/locked/**", "profile": "doctype"},
        ],
    }

    assert profile_for("outputs/documents/locked/a.html", profiles) == "doctype"
    assert profile_for("outputs/elsewhere/b.html", profiles) == "print"


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


# The engine entry points a renderer may legitimately reach for. `deep_findings`
# is the raw pipeline (the docs site pairs it with `apply_baseline`);
# `report_for_artifact` is the one-line verdict the other two print.
_RENDERER_ENTRY_POINTS = {"deep_findings", "report_for_artifact"}


def _calls_the_engine(source: str) -> set[str]:
    """Engine entry points this module actually CALLS, asked of the AST.

    Never a substring test. Until 2026-09-01 this was
    `"visual-discipline-check" in source or "impeccable_engine" in source`, and
    a whole-file substring is satisfied by an import line, a comment or a
    docstring. Measured that day: replacing the live
    `impeccable_engine.report_for_artifact(html_path, profile="doctype")` in
    scripts/render-doctype.py with `pass` left all 45 tests over this module
    and tests/test_visual_discipline_check.py green, because the now-unused
    `from scripts.utils import impeccable_engine` at the top still spelled the
    string. CAP-5 was asserted by a word.
    """
    called: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute)
                and func.attr in _RENDERER_ENTRY_POINTS
                and isinstance(func.value, ast.Name)
                and func.value.id == "impeccable_engine"):
            called.add(func.attr)
    return called


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
    assert _calls_the_engine(source), (
        f"{renderer} names the deep engine but calls none of "
        f"{sorted(_RENDERER_ENTRY_POINTS)}; CAP-5 says each renderer runs the "
        f"check on its own output, and mentioning the module is not running it")


def test_the_cap5_detector_is_not_satisfied_by_a_mention():
    """The negative control for the AST rule above.

    Without it the rule could narrow back to a substring test and all three
    renderers would still pass. Both samples spell every string the old test
    looked for; neither runs the check.
    """
    assert _calls_the_engine(
        "from scripts.utils import impeccable_engine\n"
        "# visual-discipline-check runs over this output elsewhere\n"
        '"""impeccable_engine.report_for_artifact used to live here."""\n'
    ) == set()
    assert _calls_the_engine(
        "import impeccable_engine\nreport_for_artifact = None\n"
    ) == set()
    # And the shape it must still see.
    assert _calls_the_engine(
        "from scripts.utils import impeccable_engine\n"
        'impeccable_engine.report_for_artifact(out, profile="screen")\n'
    ) == {"report_for_artifact"}


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

    from scripts.utils.impeccable_engine import is_suppressed

    profiles, warning = load_profiles(broken)

    assert warning, "a malformed config must announce itself"
    assert profiles["default"] == "screen"
    # Asserted as BEHAVIOUR, not as the shape of the suppress container: what
    # matters is that no rule is silenced, whatever structure holds them.
    for rule in ("tiny-text", "kicker-above-heading", "side-tab", "low-contrast"):
        assert is_suppressed(rule, "screen", profiles) is False


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


# ---------------------------------------------------------------------------
# The other two calibration filters, asserted through the pipeline
#
# `test_out_of_scope_findings_are_dropped_from_a_deep_run` above states the
# principle for the scope filter: "the filter is load-bearing only if the
# pipeline actually applies it." Its two siblings had no such test. Measured
# 2026-09-01, deleting each of these two lines from `deep_findings`
#
#     if not is_plausible(item, profiles): continue
#     if is_suppressed(item.get("antipattern", ""), profile, profiles): continue
#
# left this file and seven neighbouring impeccable test modules green - 26 and
# 169 passed respectively - while the predicate tests above went on asserting
# that the predicates themselves answer correctly.
#
# `load_profiles` is stubbed rather than the shipped config read, so these bind
# the WIRING and cannot drift when a rule is recalibrated. CAP-3 above already
# holds the shipped config to its own contract.
# ---------------------------------------------------------------------------


_PIPELINE_PROFILES = {
    "default": "screen",
    "profiles": {
        "screen": {"suppress": {}},
        "print": {"suppress": {"tiny-text": "a print floor, not a screen one"}},
    },
    "path_profiles": [{"glob": "outputs/paged/**", "profile": "print"}],
    "plausibility": {"oversized-h1": {"unit": "px", "max": 500}},
    "out_of_scope": {"suffixes": [], "path_fragments": []},
}


def _stub_pipeline(monkeypatch, raw):
    from scripts.utils import impeccable_engine

    monkeypatch.setattr(impeccable_engine, "load_profiles",
                        lambda *a, **k: (_PIPELINE_PROFILES, None))
    monkeypatch.setattr(impeccable_engine, "run_detector", lambda *a, **k: (raw, None))
    return impeccable_engine


def test_an_implausible_finding_is_dropped_from_a_deep_run(monkeypatch):
    """The plan names the plausibility filter as a load-bearing failure mode.

    Both entries below are the same rule on the same page; only the measured
    value differs. The 2856px reading is the parser artifact the filter exists
    for, and the 96px one is the genuine hit it must not take with it.
    """
    engine = _stub_pipeline(monkeypatch, [
        {"antipattern": "oversized-h1", "severity": "warning",
         "file": "docs/CANOPUS.html", "line": 4, "snippet": "2856px h1, 44 chars"},
        {"antipattern": "oversized-h1", "severity": "warning",
         "file": "docs/CANOPUS.html", "line": 9, "snippet": "96px h1, 44 chars"},
    ])

    findings, error = engine.deep_findings(Path("docs"))

    assert error is None
    assert [f["line"] for f in findings] == [9], (
        "the implausible reading reached the report, so the filter the plan "
        "calls load-bearing is not applied by the pipeline that uses it")


def test_a_profile_suppressed_rule_is_dropped_from_a_deep_run(monkeypatch):
    """Calibration only calibrates if the run consults it.

    `tiny-text` is suppressed for `print` and live for `screen`, so one raw
    batch spanning both surfaces must come back carrying exactly the screen one.
    That also pins the per-finding profile resolution the docstring promises:
    one directory scan must not pick a single profile for the batch.
    """
    engine = _stub_pipeline(monkeypatch, [
        {"antipattern": "tiny-text", "severity": "warning",
         "file": "outputs/paged/brief.html", "line": 1, "snippet": "9px"},
        {"antipattern": "tiny-text", "severity": "warning",
         "file": "docs/CANOPUS.html", "line": 2, "snippet": "9px"},
    ])

    findings, error = engine.deep_findings(Path("."))

    assert error is None
    assert [f["file"] for f in findings] == ["docs/CANOPUS.html"]
    assert findings[0]["profile"] == "screen"


def test_the_profile_override_participates_in_suppression(monkeypatch):
    """`deep_findings`'s own docstring: the override participates in
    SUPPRESSION, "not just in the label - stamping the name on afterwards would
    have been a lie the report told itself." Nothing measured it.

    A renderer passes `profile_override="print"` for a freshly produced paged
    document that lands in a directory the path map has never seen. If the
    override reached only the label, the finding would still be reported and
    merely be MARKED print.
    """
    raw = [{"antipattern": "tiny-text", "severity": "warning",
            "file": "docs/fresh-render.html", "line": 3, "snippet": "9px"}]

    engine = _stub_pipeline(monkeypatch, raw)
    without, _ = engine.deep_findings(Path("docs"))
    assert [f["profile"] for f in without] == ["screen"], (
        "the fixture no longer reaches the suppression branch it is meant to test")

    engine = _stub_pipeline(monkeypatch, raw)
    overridden, error = engine.deep_findings(Path("docs"), profile_override="print")

    assert error is None
    assert overridden == [], (
        "the override was stamped on the label but never consulted for "
        "suppression, which is the lie the docstring forbids")


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


def test_integration_real_cli_detects_the_side_tab_fixture(tmp_path):
    from scripts.utils.impeccable_engine import resolve_cli

    if resolve_cli() is None:
        pytest.skip("impeccable CLI unresolvable (no Node or no network)")

    result = _run_checker("--deep", "--json", "--no-baseline",
                          str(FIXTURES / "side-tab.html"), tmpdir=str(tmp_path))
    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)
    types = {f["type"] for entry in payload for f in entry["findings"]}
    assert "impeccable:side-tab" in types
