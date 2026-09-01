"""Tests for the F-6.1 trigger-coverage gate in scripts/skill-metadata-check.py.

The coverage gate is UNCONDITIONAL: a MISSING corpus (an auto-routable skill with no
valid triggers.json that is not grandfathered), a thin/malformed present corpus, or a
stale baseline entry (a baselined skill that now has a valid corpus) makes main() exit 1
regardless of --fail-on-missing, so the flagless CI invocation enforces it. "Auto-routable"
= x-heading-routing.router == auto AND NOT disable-model-invocation: true; a router: manual
OR disable-model-invocation skill is EXEMPT. Grandfathering is the committed, only-shrinks
config/triggers-coverage-baseline.json; --write-baseline is shrink-only (it removes
now-covered skills, never adds a newly-shipped uncovered one). All tests run against a tmp
workspace root (get_workspace_root monkeypatched) so none depends on the real catalog. The
script filename is kebab-case, so it is loaded via importlib.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECK_PATH = ROOT / "scripts" / "skill-metadata-check.py"


def _load_check():
    spec = importlib.util.spec_from_file_location("skill_metadata_check", CHECK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


chk = _load_check()


def _fm(name: str, router: str = "auto", disable: bool = False) -> str:
    disable_line = "disable-model-invocation: true\n" if disable else ""
    return (
        "---\n"
        f"name: {name}\n"
        f"{disable_line}"
        f'description: "test skill {name}"\n'
        "metadata:\n"
        "  author: Misha Hanin\n"
        "  email: misha.hanin@odinix.com\n"
        '  version: "1.0"\n'
        "x-heading-orchestration:\n"
        "  parallel_safe: false\n"
        "  shared_state: []\n"
        "  triggers: []\n"
        "x-heading-routing:\n"
        "  category: Operations\n"
        "  triggers: []\n"
        "  exclusions:\n"
        "    - N/A\n"
        "  compound: 'No'\n"
        f"  router: {router}\n"
        "---\n"
        f"# {name}\n"
    )


def _cases(pos: int, neg: int) -> list[dict]:
    return (
        [{"query": f"positive query {i}", "should_trigger": True} for i in range(pos)]
        + [{"query": f"negative query {i}", "should_trigger": False} for i in range(neg)]
    )


def _mk(skills_dir: Path, name: str, router: str = "auto", disable: bool = False,
        corpus=None) -> None:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(_fm(name, router, disable), encoding="utf-8")
    if corpus is not None:
        text = corpus if isinstance(corpus, str) else json.dumps(corpus)
        (d / "triggers.json").write_text(text, encoding="utf-8")


def _write_baseline(root: Path, names) -> None:
    p = root / "config" / "triggers-coverage-baseline.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted(names)), encoding="utf-8")


@pytest.fixture
def fixture_root(tmp_path, monkeypatch):
    """A tmp workspace root with .claude/skills; get_workspace_root patched to it."""
    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    monkeypatch.setattr(chk, "get_workspace_root", lambda: tmp_path)
    return tmp_path, skills_dir


def _run(monkeypatch, *extra) -> int:
    monkeypatch.setattr(sys, "argv", ["skill-metadata-check.py", *extra])
    return chk.main()


# --- classification / gate ------------------------------------------------------


def test_missing_auto_routable_fails_and_names_skill(fixture_root, monkeypatch, capsys):
    _, skills_dir = fixture_root
    _mk(skills_dir, "lonely", router="auto")  # no corpus, not baselined

    rc = _run(monkeypatch)
    out = capsys.readouterr().out
    assert rc == 1
    assert "MISSING" in out
    assert "lonely" in out


def test_router_manual_is_exempt(fixture_root, monkeypatch):
    _, skills_dir = fixture_root
    _mk(skills_dir, "manualskill", router="manual")  # no corpus
    assert _run(monkeypatch) == 0


def test_disable_model_invocation_is_exempt(fixture_root, monkeypatch):
    """Pins M2: disable-model-invocation + router:auto never auto-routes -> EXEMPT."""
    _, skills_dir = fixture_root
    _mk(skills_dir, "disabledskill", router="auto", disable=True)  # no corpus
    assert _run(monkeypatch) == 0


def test_grandfathered_skill_is_ok(fixture_root, monkeypatch):
    root, skills_dir = fixture_root
    _mk(skills_dir, "oldskill", router="auto")  # no corpus
    _write_baseline(root, ["oldskill"])
    assert _run(monkeypatch) == 0


def test_valid_corpus_is_covered_ok(fixture_root, monkeypatch, capsys):
    _, skills_dir = fixture_root
    _mk(skills_dir, "coveredskill", router="auto", corpus=_cases(4, 2))
    rc = _run(monkeypatch)
    out = capsys.readouterr().out
    assert rc == 0
    assert "COVERED:" in out


def test_thin_corpus_fails(fixture_root, monkeypatch, capsys):
    _, skills_dir = fixture_root
    _mk(skills_dir, "thinskill", router="auto", corpus=_cases(3, 1))  # 4 cases
    rc = _run(monkeypatch)
    out = capsys.readouterr().out
    assert rc == 1
    assert "thinskill" in out


def test_corpus_without_negatives_fails(fixture_root, monkeypatch):
    _, skills_dir = fixture_root
    _mk(skills_dir, "nonegskill", router="auto", corpus=_cases(6, 0))  # 6 pos, 0 neg
    assert _run(monkeypatch) == 1


def test_malformed_json_corpus_fails(fixture_root, monkeypatch):
    _, skills_dir = fixture_root
    _mk(skills_dir, "badjsonskill", router="auto", corpus="{ this is not json")
    assert _run(monkeypatch) == 1


def test_stale_baseline_entry_fails(fixture_root, monkeypatch, capsys):
    """A baselined skill that now HAS a valid corpus fails (forces the baseline to shrink)."""
    root, skills_dir = fixture_root
    _mk(skills_dir, "nowcovered", router="auto", corpus=_cases(4, 2))
    _write_baseline(root, ["nowcovered"])
    rc = _run(monkeypatch)
    out = capsys.readouterr().out
    assert rc == 1
    assert "STALE-BASELINE" in out
    assert "nowcovered" in out


def test_coverage_gate_fires_without_fail_on_missing(fixture_root, monkeypatch):
    """The coverage gate is unconditional: MISSING exits 1 even flagless."""
    _, skills_dir = fixture_root
    _mk(skills_dir, "lonely", router="auto")
    assert _run(monkeypatch) == 1


# --- the corpus_issues clause, which had no witness of its own ------------------
#
# `coverage_fail` in main() is three OR'd clauses. MEASURED 2026-09-01 by
# deleting the middle one (`any(r.get("corpus_issues") ...)`): this file stayed
# GREEN at 15 passed. `test_thin_corpus_fails` above does not reach it, because a
# thin corpus on a NON-baselined auto-routable skill also classifies MISSING, and
# the first clause catches that. The clause only ever fires alone on a skill whose
# status is something other than MISSING - GRANDFATHERED or EXEMPT - and neither
# had a case. Both are shapes a real repo produces: a baselined skill starting to
# ship a corpus, and a `router: manual` skill that carries one anyway.


def test_a_grandfathered_skill_with_a_thin_corpus_still_fails(fixture_root, monkeypatch, capsys):
    """GRANDFATHERED, so not MISSING; invalid, so not stale. Only THIN catches it."""
    root, skills_dir = fixture_root
    _mk(skills_dir, "oldskill", router="auto", corpus=_cases(3, 1))  # 4 cases
    _write_baseline(root, ["oldskill"])

    rc = _run(monkeypatch)
    out = capsys.readouterr().out
    assert "GRANDFATHERED:[0m 1" in out or "GRANDFATHERED" in out
    assert "MISSING" in out and "STALE-BASELINE" not in out
    assert "THIN" in out and "oldskill" in out
    assert rc == 1, "a grandfathered skill may lack a corpus; it may not ship a broken one"


def test_an_exempt_skill_with_a_malformed_corpus_still_fails(fixture_root, monkeypatch, capsys):
    """`router: manual` is EXEMPT from NEEDING a corpus, not from the shape rule."""
    _, skills_dir = fixture_root
    _mk(skills_dir, "manualskill", router="manual", corpus="{ this is not json")

    rc = _run(monkeypatch)
    out = capsys.readouterr().out
    assert "THIN" in out and "manualskill" in out
    assert rc == 1


# --- the walk itself ------------------------------------------------------------


def test_an_empty_skills_tree_is_refused_not_passed(fixture_root, monkeypatch, capsys):
    """Every gate in the script is a loop over the skills it walked.

    MEASURED 2026-09-01 before the fix: a scratch root whose `.claude/skills`
    existed but held nothing printed "Total skills: 0", four zeroes under the
    coverage heading, and returned 0. The directory-exists check ahead of it does
    not reach this: the directory was there, only its contents were gone. That is
    the shape a shrink-only ratchet fails in - it can only ever get quieter.
    """
    _, _skills_dir = fixture_root  # created, deliberately left empty

    rc = _run(monkeypatch)
    out = capsys.readouterr().out
    assert rc == 2, "an empty skills walk must refuse, not report clean"
    assert "no skills found" in out


def test_write_baseline_cannot_wipe_the_committed_set_from_an_empty_walk(
        fixture_root, monkeypatch):
    """The sharper edge of the same hole: `existing & {}` is `{}`.

    One `--write-baseline` run over a collapsed walk would have rewritten the
    committed grandfather set to `[]` and exited 0.
    """
    root, _skills_dir = fixture_root
    _write_baseline(root, ["alpha", "beta"])

    assert _run(monkeypatch, "--write-baseline") == 2
    written = json.loads((root / "config" / "triggers-coverage-baseline.json").read_text())
    assert written == ["alpha", "beta"], "the committed baseline was rewritten"


# --- shrink-only --write-baseline (pins H1) -------------------------------------


def test_write_baseline_is_shrink_only(fixture_root, monkeypatch):
    """--write-baseline must NOT add a new uncovered skill to the frozen seed; that skill
    stays MISSING and the gate fails. It may only remove now-covered skills."""
    root, skills_dir = fixture_root
    _mk(skills_dir, "old_uncovered", router="auto")   # in seed, still uncovered
    _mk(skills_dir, "new_uncovered", router="auto")   # NOT in seed
    _write_baseline(root, ["old_uncovered"])

    assert _run(monkeypatch, "--write-baseline") == 0

    written = json.loads((root / "config" / "triggers-coverage-baseline.json").read_text())
    assert "new_uncovered" not in written   # refused to add the new skill
    assert "old_uncovered" in written       # still-uncovered seed entry retained

    # The new skill is therefore still MISSING -> the flagless gate fails.
    assert _run(monkeypatch) == 1


def test_write_baseline_removes_now_covered(fixture_root, monkeypatch):
    """A baselined skill that has since gained a valid corpus is dropped on --write-baseline."""
    root, skills_dir = fixture_root
    _mk(skills_dir, "still_bare", router="auto")
    _mk(skills_dir, "now_has_corpus", router="auto", corpus=_cases(4, 2))
    _write_baseline(root, ["still_bare", "now_has_corpus"])

    assert _run(monkeypatch, "--write-baseline") == 0

    written = json.loads((root / "config" / "triggers-coverage-baseline.json").read_text())
    assert "now_has_corpus" not in written
    assert "still_bare" in written


# --- direct unit checks ---------------------------------------------------------


def test_is_auto_routable_predicate():
    assert chk.is_auto_routable({"x-heading-routing": {"router": "auto"}}) is True
    assert chk.is_auto_routable({"x-heading-routing": {"router": "manual"}}) is False
    assert chk.is_auto_routable(
        {"disable-model-invocation": True, "x-heading-routing": {"router": "auto"}}
    ) is False
    assert chk.is_auto_routable({}) is False  # no routing block


def test_the_case_floor_is_at_least_the_two_it_sums(tmp_path):
    """`TRIGGERS_MIN_CASES` has no negative case of its own, and cannot have one.

    MEASURED 2026-09-01: lowering it from 6 to 1 left this file and
    `test_skill_triggers_json.py` green across 495 tests. That is not a gap in
    the tests, it is arithmetic - the constant is exactly MIN_POS + MIN_NEG, so a
    corpus that clears the positive and negative floors has already cleared the
    total, and no input can separate them. Recorded as the invariant rather than
    chased with a fixture that cannot exist.

    What it protects: someone lowering MIN_POS or MIN_NEG later, and assuming the
    total floor still holds the line. If the sum ever exceeds the total, the
    total stops being reachable and this fails instead of going quiet.
    """
    assert chk.TRIGGERS_MIN_CASES >= chk.TRIGGERS_MIN_POS + chk.TRIGGERS_MIN_NEG, (
        f"MIN_CASES={chk.TRIGGERS_MIN_CASES} is below MIN_POS+MIN_NEG="
        f"{chk.TRIGGERS_MIN_POS + chk.TRIGGERS_MIN_NEG}, so the case floor is dead"
    )
    # And the count check is still live where the pos/neg pair cannot see: a case
    # whose `should_trigger` is neither True nor False counts toward neither.
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps(
        [{"query": f"q{i}", "should_trigger": "maybe"} for i in range(3)]
    ), encoding="utf-8")
    issues = chk.corpus_issues(odd)
    assert any(f"< {chk.TRIGGERS_MIN_CASES} required" in issue for issue in issues), issues


def test_corpus_issues_shape(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_cases(4, 2)), encoding="utf-8")
    assert chk.corpus_issues(good) == []

    thin = tmp_path / "thin.json"
    thin.write_text(json.dumps(_cases(3, 1)), encoding="utf-8")
    assert chk.corpus_issues(thin)  # non-empty
