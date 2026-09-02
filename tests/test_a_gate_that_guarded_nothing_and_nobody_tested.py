"""`scripts/check-gate-integrity.py` -- the enforcement layer, checked.

Two ways a declared gate stops being a gate without anyone noticing.

A `files:` regex that matches nothing scopes the hook to an empty set. It then
passes every commit and prints the same green line a working hook prints. It is
the empty-corpus defect one level up: the corpus is the set of files the hook was
aimed at.

A gate with no test has never been observed refusing. Its correct behaviour is
invisible on the happy path, so a wall that exits 0 unconditionally looks exactly
like a wall that found nothing. The campaign that ended 2026-09-02 found two such
walls, disarmed by an `or` short-circuit, and the reason nobody caught them is
that nothing had ever asked them to refuse.

This file names `scripts/check-gate-integrity.py` so the gate satisfies its own
second rule, and that is not a trick: the rule is that a gate must appear in the
test tree, and this is the file that drives it.

Every rule case here runs on a synthetic hook dict rather than on the real
config, so the rule has negative cases. The two tree-level tests at the bottom
assert the CURRENT verdict, so the gate cannot go green by reading nothing.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    path = ROOT / "scripts" / "check-gate-integrity.py"
    spec = importlib.util.spec_from_file_location("check_gate_integrity", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_gate_integrity"] = module
    spec.loader.exec_module(module)
    return module


gi = _load_module()

TRACKED = ["scripts/a.py", "scripts/b.py", "docs/c.md", "tests/test_a.py"]


# ============================================================
# Rule 1: a scope that matches nothing
# ============================================================

def test_a_pattern_matching_no_tracked_path_is_a_finding():
    hook = {"id": "h", "files": r"^scripts/.*\.rb$", "entry": "x scripts/a.py"}
    assert gi.hook_matches_nothing(hook, TRACKED) is True


def test_a_pattern_that_matches_is_not_a_finding():
    """The anchor. A rule that flagged every pattern would satisfy the test
    above and break all 24 hooks in the repository."""
    hook = {"id": "h", "files": r"^scripts/.*\.py$", "entry": "x scripts/a.py"}
    assert gi.hook_matches_nothing(hook, TRACKED) is False


def test_always_run_declares_no_scope_so_it_cannot_get_one_wrong():
    hook = {"id": "h", "always_run": True, "files": r"^nothing/$"}
    assert gi.hook_matches_nothing(hook, TRACKED) is False


def test_a_hook_with_no_files_key_is_not_a_finding():
    assert gi.hook_matches_nothing({"id": "h"}, TRACKED) is False


def test_an_unparseable_regex_is_reported_rather_than_crashing():
    """A regex that will not compile matches nothing at run time too, so the
    honest verdict is the same one, not an exception that skips the hook."""
    hook = {"id": "h", "files": r"^scripts/([\.py$"}
    assert gi.hook_matches_nothing(hook, TRACKED) is True


# ============================================================
# Rule 2: a gate no test names
# ============================================================

def test_a_script_named_by_no_test_is_a_finding():
    hooks = [{"id": "h", "entry": ".venv/bin/python scripts/b.py --check"}]
    found = gi.findings(hooks, TRACKED, tests="nothing in here")
    assert found == {"hook:h": "scripts/b.py is named by no file under tests/"}


def test_a_script_a_test_names_is_not_a_finding():
    hooks = [{"id": "h", "entry": ".venv/bin/python scripts/b.py --check"}]
    assert gi.findings(hooks, TRACKED, tests="see scripts/b.py for why") == {}


def test_an_inline_entry_is_reported_rather_than_skipped():
    """A skipped case is a claim that decays. An entry with no script cannot
    satisfy rule 2, and saying so puts it in BASELINE with a reason instead of
    leaving it invisible."""
    hooks = [{"id": "h", "entry": 'python -c "import sys; sys.exit(0)"'}]
    found = gi.findings(hooks, TRACKED, tests="")
    assert found["hook:h"].startswith("entry names no scripts/ path")


def test_the_scope_finding_wins_over_the_test_finding():
    """A hook scoped to nothing is broken whether or not a test names it, and
    reporting both would double-count one hook."""
    hooks = [{"id": "h", "files": r"^zzz$", "entry": "python scripts/b.py"}]
    found = gi.findings(hooks, TRACKED, tests="")
    assert "matches no tracked path" in found["hook:h"]


# ============================================================
# Refusals: this gate must not be green over nothing
# ============================================================

def _repo(tmp_path: Path, *, hooks: list[dict], tracked_extra: tuple[str, ...] = ()):
    (tmp_path / ".pre-commit-config.yaml").write_text(
        yaml.safe_dump({"repos": [{"repo": "local", "hooks": hooks}]}),
        encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_x.py").write_text("# names nothing\n", encoding="utf-8")
    for rel in tracked_extra:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    return tmp_path


def test_a_config_with_too_few_hooks_refuses(tmp_path, monkeypatch, capsys):
    """A parse that finds three hooks did not find this repository's config, and
    a verdict over it would be a claim about a file that was never read."""
    root = _repo(tmp_path, hooks=[{"id": "a", "entry": "python scripts/a.py"}])
    monkeypatch.setattr(gi, "ROOT", root)
    monkeypatch.setattr(gi, "tracked_paths", lambda _root: list(TRACKED))
    monkeypatch.setattr(gi, "test_corpus", lambda _root, _tracked: "scripts/a.py")
    monkeypatch.setattr(sys, "argv", ["check-gate-integrity.py", "--check"])

    assert gi.main() == 2
    assert "below the floor" in capsys.readouterr().err


def test_an_empty_git_listing_refuses_rather_than_reading_as_no_files(tmp_path):
    """`git ls-files` returning nothing is not 'no paths to check'.

    That reading is precisely the defect shape that made a real secret scanner
    report clean over a tree it never enumerated. A real repository with an
    empty index is the only way to reach this branch: an ordinary directory
    fails git outright, which is the OTHER branch and is asserted separately
    below. Absent and empty are different states and a guard that cannot tell
    them apart is not a guard.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    with pytest.raises(gi.Unreadable, match="returned nothing"):
        gi.tracked_paths(tmp_path)


def test_a_directory_that_is_not_a_repository_refuses_on_the_other_branch(tmp_path):
    """The mirror. git exits non-zero, and that must not be reported with the
    empty-index wording, or the two states collapse into one message."""
    with pytest.raises(gi.Unreadable, match="git ls-files failed"):
        gi.tracked_paths(tmp_path)


def test_a_tree_with_no_test_files_refuses(tmp_path, monkeypatch):
    with pytest.raises(gi.Unreadable, match="no tracked test files"):
        gi.test_corpus(tmp_path, ["scripts/a.py"])


def test_an_unreadable_config_refuses(tmp_path, monkeypatch):
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: [oops\n", encoding="utf-8")
    with pytest.raises(gi.Unreadable, match="unreadable"):
        gi.local_hooks(tmp_path)


def test_a_config_with_no_repos_list_refuses(tmp_path):
    (tmp_path / ".pre-commit-config.yaml").write_text("{}\n", encoding="utf-8")
    with pytest.raises(gi.Unreadable, match="no repos list"):
        gi.local_hooks(tmp_path)


# ============================================================
# The CLI, on its exit code
# ============================================================

def _cli(monkeypatch, root, hooks, tests_text, argv=("--check",)):
    monkeypatch.setattr(gi, "ROOT", root)
    monkeypatch.setattr(gi, "MIN_HOOKS", 1)
    monkeypatch.setattr(gi, "tracked_paths", lambda _root: list(TRACKED))
    monkeypatch.setattr(gi, "local_hooks", lambda _root: hooks)
    monkeypatch.setattr(gi, "test_corpus", lambda _root, _tracked: tests_text)
    monkeypatch.setattr(sys, "argv", ["check-gate-integrity.py", *argv])
    return gi.main()


def test_the_command_exits_one_on_a_new_finding(tmp_path, monkeypatch, capsys):
    code = _cli(monkeypatch, tmp_path,
                [{"id": "brand-new", "entry": "python scripts/b.py"}], "")
    assert code == 1
    assert "brand-new" in capsys.readouterr().out


def test_the_command_exits_zero_when_the_finding_is_frozen(tmp_path, monkeypatch, capsys):
    """The anchor for the CLI. A gate that exits 1 unconditionally passes the
    test above and blocks every commit."""
    monkeypatch.setitem(gi.BASELINE, "hook:brand-new", "frozen for this test")
    code = _cli(monkeypatch, tmp_path,
                [{"id": "brand-new", "entry": "python scripts/b.py"}], "")
    assert code == 0
    assert "OK" in capsys.readouterr().out


def test_a_stale_baseline_entry_is_reported_not_silently_kept(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(gi.BASELINE, "hook:long-gone", "no longer exists")
    _cli(monkeypatch, tmp_path,
         [{"id": "ok", "entry": "python scripts/b.py"}], "scripts/b.py")
    assert "hook:long-gone" in capsys.readouterr().out


def test_json_output_carries_the_new_findings(tmp_path, monkeypatch, capsys):
    _cli(monkeypatch, tmp_path,
         [{"id": "brand-new", "entry": "python scripts/b.py"}], "", argv=("--json",))
    payload = json.loads(capsys.readouterr().out)
    assert "hook:brand-new" in payload["new"]


# ============================================================
# The live configuration
# ============================================================

def test_every_declared_gate_has_a_scope_and_a_test_that_names_it():
    tracked = gi.tracked_paths(ROOT)
    hooks = gi.local_hooks(ROOT)
    assert len(hooks) >= gi.MIN_HOOKS, (
        f"read {len(hooks)} local hooks, below the floor of {gi.MIN_HOOKS}")

    found = gi.findings(hooks, tracked, gi.test_corpus(ROOT, tracked))
    new = {k: v for k, v in found.items() if k not in gi.BASELINE}
    assert new == {}, f"gate(s) guarding nothing or named by no test: {new}"


def test_no_baseline_entry_has_stopped_firing():
    """A BASELINE carrying a finding that no longer exists overstates the debt,
    and the next reader trusts the whole file less."""
    tracked = gi.tracked_paths(ROOT)
    found = gi.findings(gi.local_hooks(ROOT), tracked, gi.test_corpus(ROOT, tracked))
    stale = sorted(k for k in gi.BASELINE if k not in found)
    assert stale == [], f"BASELINE entries that no longer fire: {stale}"


def test_every_baseline_entry_carries_a_reason():
    """A frozen finding with an empty reason is a finding somebody hid."""
    empty = sorted(k for k, v in gi.BASELINE.items() if not v.strip())
    assert empty == [], f"BASELINE entries with no reason: {empty}"
