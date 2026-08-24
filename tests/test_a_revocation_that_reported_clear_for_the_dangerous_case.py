"""Kimi k3's cross-check of shards this audit had already passed, and closed.

k3 came back mid-audit and was pointed at scripts/ from the top. These are the
findings it raised in files I had already read and signed off, plus the one
judgement of mine it overturned.

1. THE 404 THAT MEANT THE OPPOSITE OF WHAT IT PRINTED.
   `DELETE /repos/{repo}/collaborators/{user}` removes a DIRECT collaborator.
   Access granted through an organization TEAM leaves no per-repo collaborator
   record, so that endpoint answers 404 for such a member -- and
   `revoke_all_github_access` printed "[no access]" on a 404. The one case where
   a revoked exec keeps full push rights rendered as the reassuring one, on a
   step headed "REVOKING ALL GITHUB ACCESS".

2. THE USERNAME IT GUESSED. `exec_info.get("github_username", slug)` fell back to
   the exec SLUG, which is not a GitHub username. Every request 404'd, every repo
   printed "[no access]", and the run reported a clean revocation of nobody.

3. THE REGISTRY UPDATE THAT COMMITTED NOTHING AND SAID "[ok]". A slug matching no
   executive fell through the loop; the file was rewritten unchanged, a commit
   reading "EMERGENCY: Revoke access for {slug}" was created and pushed, and the
   operator was told "Status set to 'revoked'".

4. THE COMMIT AUDIT THAT CORRUPTED ITS OWN FORENSIC RECORD. `git log
   --format=%H|%an|%ae|%s|%ci` split with `maxsplit=4`: a commit subject
   containing a pipe shifted every field right, truncating the subject and
   writing the remainder into `date`.

5. THE MODULE DOCSTRING THAT STILL PROMISED THE OLD CONTRACT. It said the script
   "immediately revokes all GitHub access" while `main()` exits 2 revoking
   nothing. The argparse description and epilog had been corrected at disable
   time; the file header had not.

6. TWO PATH-CONTAINMENT GUARDS THAT WERE CORRECT ONLY BY SEPARATOR. Both
   `eval-flag._staged_dir` and `eval-outcomes.valid_skill_name` tested
   `str(resolved).startswith(str(parent) + "/")`. On Windows `resolve()` returns
   backslashes, so the test matched nothing and refused every legitimate skill.
   Both files already used `Path.is_relative_to` elsewhere.

My own earlier call on this file was "audited, NOT changed -- the dead code below
`sys.exit(2)` is deliberate and documented". That was right about the exit and
wrong about the rest: the docstring is read TODAY, and dormant code kept for a
re-enable is kept with its defects unless someone fixes them.

What is deliberately NOT fixed here: no org-membership or team endpoint was
added. Whether 31C grants exec access by direct invite or by team is the
operator's knowledge, and guessing wrong inside a revocation tool deletes the
wrong thing during an incident. Flagged in the source, raised in the summary.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def er():
    return _load("emergency_revoke", "scripts/emergency-revoke.py")


@pytest.fixture(scope="module")
def ef():
    return _load("eval_flag_mod", "scripts/eval-flag.py")


@pytest.fixture(scope="module")
def eo():
    return _load("eval_outcomes_mod", "scripts/eval-outcomes.py")


# ============================================================
# 1 + 2. The revocation that reported clear
# ============================================================

def _run_revoke(er, monkeypatch, capsys, exec_info, *, rc=0, stderr=""):
    calls = []

    def _fake(cmd, cwd=None, check=True):
        calls.append(cmd)
        return SimpleNamespace(returncode=rc, stderr=stderr, stdout="")

    monkeypatch.setattr(er, "run_cmd", _fake)
    er.revoke_all_github_access("some-exec", exec_info)
    return calls, capsys.readouterr()


def test_a_404_is_not_reported_as_no_access(er, monkeypatch, capsys):
    """The org-team member is exactly the person a 404 does NOT clear."""
    _, cap = _run_revoke(er, monkeypatch, capsys,
                         {"github_username": "realuser"}, rc=1, stderr="HTTP 404")
    assert "no access" not in cap.out
    assert "not a direct collaborator" in cap.out


def test_a_404_says_org_access_was_not_checked(er, monkeypatch, capsys):
    _, cap = _run_revoke(er, monkeypatch, capsys,
                         {"github_username": "realuser"}, rc=1, stderr="HTTP 404")
    assert "org/team access NOT checked" in cap.out


def test_the_step_heading_no_longer_claims_all_access(er, monkeypatch, capsys):
    _, cap = _run_revoke(er, monkeypatch, capsys, {"github_username": "realuser"})
    assert "REVOKING ALL GITHUB ACCESS" not in cap.out
    assert "DIRECT COLLABORATOR" in cap.out


def test_a_successful_delete_is_still_reported_as_revoked(er, monkeypatch, capsys):
    _, cap = _run_revoke(er, monkeypatch, capsys, {"github_username": "realuser"})
    assert "[REVOKED]" in cap.out


def test_a_missing_github_username_stops_rather_than_guessing(er, monkeypatch, capsys):
    calls, cap = _run_revoke(er, monkeypatch, capsys, {"slug": "some-exec"})
    assert calls == [], "it must not issue a request under a guessed username"
    assert "[STOP]" in cap.out
    assert "MANUAL ACTION REQUIRED" in cap.out


def test_a_null_exec_info_stops_too(er, monkeypatch, capsys):
    calls, cap = _run_revoke(er, monkeypatch, capsys, None)
    assert calls == []
    assert "[STOP]" in cap.out


def test_the_real_username_is_the_one_sent(er, monkeypatch, capsys):
    calls, _ = _run_revoke(er, monkeypatch, capsys, {"github_username": "realuser"})
    assert calls, "a known username must still be acted on"
    for cmd in calls:
        assert any("realuser" in str(part) for part in cmd)
        assert not any("some-exec" in str(part).split("/")[-1] for part in cmd), \
            "the slug must never be sent as the username"


# ============================================================
# 3. The registry update that committed a lie
# ============================================================

def _registry(er, tmp_path, monkeypatch, execs):
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "exec-registry.json").write_text(
        json.dumps({"executives": execs}), encoding="utf-8")
    monkeypatch.setattr(er, "get_data_config_dir", lambda: cfg)
    ran = []
    monkeypatch.setattr(er, "run_cmd",
                        lambda cmd, cwd=None, check=True: (
                            ran.append(cmd),
                            SimpleNamespace(returncode=0, stderr="", stdout=""))[1])
    return cfg / "exec-registry.json", ran


def test_an_unknown_slug_writes_nothing(er, tmp_path, monkeypatch, capsys):
    path, ran = _registry(er, tmp_path, monkeypatch, [{"slug": "real-exec"}])
    before = path.read_text(encoding="utf-8")
    er.update_registry_status("typo-exec")
    assert path.read_text(encoding="utf-8") == before


def test_an_unknown_slug_commits_nothing(er, tmp_path, monkeypatch, capsys):
    _, ran = _registry(er, tmp_path, monkeypatch, [{"slug": "real-exec"}])
    er.update_registry_status("typo-exec")
    assert ran == [], "a revocation that did not happen must leave no paper trail"


def test_an_unknown_slug_never_says_ok(er, tmp_path, monkeypatch, capsys):
    _registry(er, tmp_path, monkeypatch, [{"slug": "real-exec"}])
    er.update_registry_status("typo-exec")
    out = capsys.readouterr().out
    assert "Status set to 'revoked'" not in out
    assert "[STOP]" in out


def test_an_unknown_slug_lists_the_slugs_that_do_exist(er, tmp_path, monkeypatch,
                                                        capsys):
    """During an incident, "which one did I mean" is the next question."""
    _registry(er, tmp_path, monkeypatch,
              [{"slug": "real-exec"}, {"slug": "other-exec"}])
    er.update_registry_status("typo-exec")
    out = capsys.readouterr().out
    assert "real-exec" in out and "other-exec" in out


def test_a_known_slug_is_still_marked_revoked(er, tmp_path, monkeypatch, capsys):
    path, _ = _registry(er, tmp_path, monkeypatch, [{"slug": "real-exec"}])
    er.update_registry_status("real-exec")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["executives"][0]["status"] == "revoked"
    assert data["executives"][0]["revoked_at"]
    assert "Status set to 'revoked'" in capsys.readouterr().out


def test_an_empty_registry_stops_rather_than_committing(er, tmp_path, monkeypatch,
                                                         capsys):
    _, ran = _registry(er, tmp_path, monkeypatch, [])
    er.update_registry_status("anyone")
    assert ran == []
    assert "[STOP]" in capsys.readouterr().out


# ============================================================
# 4. The commit audit that corrupted its record
# ============================================================

def test_a_subject_containing_a_pipe_survives_the_split():
    """The forensic record this step exists to produce."""
    line = "abc123\x1fA Name\x1fa@example.test\x1frevert | hotfix\x1f2026-08-25 10:00:00 +0000"
    parts = line.split("\x1f", 4)
    assert len(parts) == 5
    _, _, _, subject, date = parts
    assert subject == "revert | hotfix"
    assert date.startswith("2026-08-25")


def test_the_old_separator_was_the_defect():
    """Pinned so the reason the format changed is not lost."""
    line = "abc123|A Name|a@example.test|revert | hotfix|2026-08-25 10:00:00 +0000"
    _, _, _, subject, date = line.split("|", 4)
    assert subject == "revert ", "the subject was truncated at the first pipe"
    assert "hotfix" in date, "the rest of the subject landed in the date field"
    assert date != "2026-08-25 10:00:00 +0000"


def test_the_git_format_and_the_split_use_the_same_separator(er):
    """A format and a split that disagree is how this class of bug returns."""
    src = (ROOT / "scripts" / "emergency-revoke.py").read_text(encoding="utf-8")
    assert '"--format=%H|%an|%ae|%s|%ci",' not in src
    assert src.count("--format=%H%x1f%an%x1f%ae%x1f%s%x1f%ci") == 2
    assert src.count('split("\\x1f", 4)') == 2


# ============================================================
# 5. The docstring that promised the old contract
# ============================================================

def test_the_module_docstring_says_it_revokes_nothing(er):
    doc = er.__doc__ or ""
    assert "DISABLED" in doc
    assert "revokes nothing" in doc


def test_the_module_docstring_no_longer_promises_immediate_revocation(er):
    assert "Immediately revokes all GitHub access" not in (er.__doc__ or "")


def test_the_docstring_agrees_with_the_argparse_description(er):
    """These two disagreed for as long as the disable has been in place."""
    src = (ROOT / "scripts" / "emergency-revoke.py").read_text(encoding="utf-8")
    assert "DISABLED" in (er.__doc__ or "")
    assert 'description="DISABLED.' in src


def test_the_docstring_names_the_org_team_gap(er):
    """The re-enable trap is recorded where the next author will read it."""
    doc = er.__doc__ or ""
    assert "org team" in doc or "org-membership" in doc


# ============================================================
# 6. The containment guards that were correct only by separator
# ============================================================

def test_a_valid_skill_still_resolves_in_eval_flag(ef):
    assert ef._staged_dir("email-intel").name == "_staged"


def test_a_valid_skill_still_resolves_in_eval_outcomes(eo):
    assert eo.valid_skill_name("email-intel") == "email-intel"


@pytest.mark.parametrize("bad", ["../../etc", "/etc/passwd", "a/../../b", "..", ""])
def test_eval_flag_still_refuses_an_escape(ef, bad):
    with pytest.raises(ValueError):
        ef._staged_dir(bad)


@pytest.mark.parametrize("bad", ["../../etc", "/etc/passwd", "a/../../b", "..", ""])
def test_eval_outcomes_still_refuses_an_escape(eo, bad):
    with pytest.raises(ValueError):
        eo.valid_skill_name(bad)


def test_neither_guard_tests_a_hardcoded_separator():
    """A containment check must not depend on the platform's path separator."""
    for rel in ("scripts/eval-flag.py", "scripts/eval-outcomes.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert 'startswith(str(SKILLS_DIR.resolve()) + "/")' not in src, rel
        assert "is_relative_to(SKILLS_DIR.resolve())" in src, rel


def test_the_windows_shape_the_prefix_test_refused():
    """Why the primitive changed, pinned so it is not reverted as cosmetic."""
    from pathlib import PureWindowsPath
    parent = PureWindowsPath(r"C:\ws\.claude\skills")
    child = PureWindowsPath(r"C:\ws\.claude\skills\email-intel\evals")
    assert not str(child).startswith(str(parent) + "/")
    assert child.is_relative_to(parent)
