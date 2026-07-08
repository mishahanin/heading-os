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


def test_corpus_issues_shape(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_cases(4, 2)), encoding="utf-8")
    assert chk.corpus_issues(good) == []

    thin = tmp_path / "thin.json"
    thin.write_text(json.dumps(_cases(3, 1)), encoding="utf-8")
    assert chk.corpus_issues(thin)  # non-empty
