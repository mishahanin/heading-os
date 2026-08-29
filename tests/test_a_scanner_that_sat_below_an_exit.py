#!/usr/bin/env python3
"""Shard 09-p2: a security control that reported healthy and could not run.

`install-hooks.py` merged its secret scanner into an existing pre-commit hook by
APPENDING it. An existing hook ending in `exit 0` -- the ordinary shape, and
what git-lfs writes -- made every appended line unreachable. The marker was in
the file all the same, so `--check` printed "pre-commit: installed" for a
scanner that could never execute. That is the exact silent-bypass class the
file's own header docstring exists because of.

Its sibling `install-git-hooks.py` had the mirror gap: `--check` verified the
pre-push gate only, while the file's docstring says the script also ensures the
pre-commit framework hooks. A repo with the push gate installed and
`pre-commit install` never run exited 0 with every commit-time gate absent. And
its install path died on a raw `shutil.copyfile` traceback when `.githooks/`
was incomplete, which is exactly the fresh-clone case the docstring sends you
there for.

Three quieter ones alongside. `knowledge-health.py` dropped a whole note from
its terminal report when the note was stale, so a stale note that was ALSO
missing a required field had the missing field reported only in `--json`: two
consumers of one scan, two health pictures. `rules.py` tested Tribe Leadership
with a SUBSTRING, so `ex-tribe-leadership` was promoted to HIGH_LIKELY weight
99 -- the opposite of what the directive in that same block requires -- while
the strict membership test ten methods away disagreed with it. And the same
file accepted any line beginning `---` as a frontmatter closing fence, so a
markdown horizontal rule truncated a contact's frontmatter and a file with no
fence at all still parsed.

Plus one speed change with a correctness argument: `mutation_harness` wiped
every `__pycache__` in the repo before EVERY run. It now wipes once and forbids
the child to write bytecode at all, which is a stronger guarantee and about a
third faster.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ih = _load("install-hooks", "install_hooks_09p2")
igh = _load("install-git-hooks", "install_git_hooks_09p2")
kh = _load("knowledge-health", "knowledge_health_09p2")
la = _load("linkedin-activity", "linkedin_activity_09p2")

from scripts.inbox_pulse.rules import _extract_frontmatter  # noqa: E402
from scripts.utils import mutation_harness  # noqa: E402


# ============================================================
# Finding 1 -- the scanner that sat below an exit
# ============================================================
@pytest.fixture
def hooks_dir(tmp_path):
    d = tmp_path / "hooks"
    d.mkdir()
    return d


def _lfs_style_hook() -> str:
    """The shape that made the appended scanner dead: work, then `exit 0`."""
    return "#!/bin/sh\ncommand -v git-lfs >/dev/null || exit 2\ngit lfs pre-commit \"$@\"\nexit 0\n"


def test_the_merged_scanner_sits_above_the_existing_exit(hooks_dir):
    (hooks_dir / "pre-commit").write_text(_lfs_style_hook(), encoding="utf-8")
    ih.install_pre_commit(hooks_dir, check_only=False)
    body = (hooks_dir / "pre-commit").read_text(encoding="utf-8")
    assert body.index(ih.HOOK_MARKER) < body.index("git lfs pre-commit")


def test_the_original_hooks_work_is_still_in_the_merged_file(hooks_dir):
    """Prepending must not cost the hook that was already there."""
    (hooks_dir / "pre-commit").write_text(_lfs_style_hook(), encoding="utf-8")
    ih.install_pre_commit(hooks_dir, check_only=False)
    body = (hooks_dir / "pre-commit").read_text(encoding="utf-8")
    assert "git lfs pre-commit" in body
    assert body.rstrip().endswith("exit 0")


def _run_merged_hook(hooks_dir, tmp_path, staged: str, dirty: str = ""):
    """Actually execute the merged hook with a stub `git` on PATH.

    Reading the block's source for the string `exit` is not enough: the early
    return that made this a defect was `[ -z "$STAGED" ] && exit 0`, which no
    line-shape test notices. Running it is the only check that does.

    The stub answers TWO different questions since 2026-08-29, because the block
    now asks two: `git diff --cached ...` for the staged set, and `git diff
    --name-only -- <staged>` for the subset that also has unstaged edits. A stub
    that printed the same string for every git call answered the second question
    with the first question's answer, so every merged hook looked dirty.
    """
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    (stub / "git").write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '  if [ "$a" = "--cached" ]; then printf "%s" "' + staged + '"; exit 0; fi\n'
        "done\n"
        'printf "%s" "' + dirty + '"\n',
        encoding="utf-8")
    (stub / "git").chmod(0o755)
    (stub / "python3").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (stub / "python3").chmod(0o755)
    env = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}")
    return subprocess.run(["sh", str(hooks_dir / "pre-commit")],
                          capture_output=True, text=True, timeout=60, env=env)


def test_a_merged_hook_with_nothing_staged_still_runs_the_original(hooks_dir, tmp_path):
    """The clean path must FALL THROUGH, not exit. An early `exit 0` here skips
    whatever hook the scanner was merged into -- git-lfs, most often."""
    (hooks_dir / "pre-commit").write_text(
        "#!/bin/sh\necho ORIGINAL-HOOK-RAN\nexit 0\n", encoding="utf-8")
    ih.install_pre_commit(hooks_dir, check_only=False)
    out = _run_merged_hook(hooks_dir, tmp_path, staged="")
    assert "ORIGINAL-HOOK-RAN" in out.stdout, out.stdout + out.stderr
    assert out.returncode == 0


def test_a_merged_hook_with_staged_files_still_runs_the_original(hooks_dir, tmp_path):
    (hooks_dir / "pre-commit").write_text(
        "#!/bin/sh\necho ORIGINAL-HOOK-RAN\nexit 0\n", encoding="utf-8")
    ih.install_pre_commit(hooks_dir, check_only=False)
    out = _run_merged_hook(hooks_dir, tmp_path, staged="a.py\n")
    assert "ORIGINAL-HOOK-RAN" in out.stdout, out.stdout + out.stderr


def test_a_merged_hook_refuses_when_a_staged_file_has_unstaged_edits(
        hooks_dir, tmp_path):
    """The scanner reads PATHS off disk while the list comes from the INDEX, and
    this hook has no stash, so on a partially-staged file it would scan the
    wrong version. MEASURED 2026-08-29 before the guard: a staged AWS key with a
    cleaned worktree copy produced "No secrets detected" and the key was
    committed. Full measurement in
    `tests/test_a_gate_that_named_one_file_and_read_another.py`.
    """
    (hooks_dir / "pre-commit").write_text(
        "#!/bin/sh\necho ORIGINAL-HOOK-RAN\nexit 0\n", encoding="utf-8")
    ih.install_pre_commit(hooks_dir, check_only=False)
    out = _run_merged_hook(hooks_dir, tmp_path, staged="a.py\n", dirty="a.py\n")
    assert out.returncode == 1, out.stdout + out.stderr
    assert "COMMIT BLOCKED" in out.stdout
    assert "ORIGINAL-HOOK-RAN" not in out.stdout


def test_the_scanner_block_never_exits_on_the_clean_path():
    """The source-shape check, kept beside the behavioural one above."""
    for line in ih.SCANNER_BLOCK.splitlines():
        assert not line.lstrip().startswith("exit 0"), \
            f"a clean-path exit in the block: {line!r}"


def test_the_scanner_block_still_blocks_a_commit_when_secrets_are_found():
    assert "exit 1" in ih.SCANNER_BLOCK
    assert "COMMIT BLOCKED" in ih.SCANNER_BLOCK


def test_the_old_appended_shape_is_reported_dead():
    dead = "#!/bin/sh\necho hi\nexit 0\n\n" + ih.SCANNER_BLOCK
    reachable, why = ih.scanner_reachability(dead)
    assert reachable is False
    assert "unconditional `exit` at line 3" in why


def test_the_dead_shape_no_longer_passes_check(hooks_dir, capsys):
    (hooks_dir / "pre-commit").write_text(
        "#!/bin/sh\necho hi\nexit 0\n\n" + ih.SCANNER_BLOCK, encoding="utf-8")
    assert ih.install_pre_commit(hooks_dir, check_only=True) is False
    assert "DEAD" in capsys.readouterr().out


def test_the_merged_shape_passes_check(hooks_dir):
    (hooks_dir / "pre-commit").write_text(_lfs_style_hook(), encoding="utf-8")
    ih.install_pre_commit(hooks_dir, check_only=False)
    assert ih.install_pre_commit(hooks_dir, check_only=True) is True


def test_the_check_line_says_what_it_actually_established(hooks_dir, capsys):
    """A nested exit is not detected, so the sentence must not imply it was."""
    (hooks_dir / "pre-commit").write_text(_lfs_style_hook(), encoding="utf-8")
    ih.install_pre_commit(hooks_dir, check_only=False)
    capsys.readouterr()
    ih.install_pre_commit(hooks_dir, check_only=True)
    assert "nested exits not checked" in capsys.readouterr().out


def test_an_absent_marker_is_unreachable_for_a_named_reason():
    reachable, why = ih.scanner_reachability("#!/bin/sh\necho hi\n")
    assert reachable is False
    assert "marker is not in this hook" in why


def test_an_indented_exit_is_conditional_and_does_not_count():
    body = ("#!/bin/sh\nif [ -z \"$X\" ]; then\n    exit 0\nfi\n\n" + ih.SCANNER_BLOCK)
    reachable, _ = ih.scanner_reachability(body)
    assert reachable is True


def test_a_hook_with_no_shebang_gets_one(hooks_dir):
    (hooks_dir / "pre-commit").write_text("echo legacy\n", encoding="utf-8")
    ih.install_pre_commit(hooks_dir, check_only=False)
    body = (hooks_dir / "pre-commit").read_text(encoding="utf-8")
    assert body.startswith("#!/bin/sh\n")
    assert "echo legacy" in body


def test_a_second_merge_does_not_duplicate_the_scanner(hooks_dir):
    (hooks_dir / "pre-commit").write_text(_lfs_style_hook(), encoding="utf-8")
    ih.install_pre_commit(hooks_dir, check_only=False)
    ih.install_pre_commit(hooks_dir, check_only=False)
    body = (hooks_dir / "pre-commit").read_text(encoding="utf-8")
    assert body.count(ih.HOOK_MARKER) == 1


def test_a_fresh_install_writes_a_hook_that_terminates(hooks_dir):
    ih.install_pre_commit(hooks_dir, check_only=False)
    body = (hooks_dir / "pre-commit").read_text(encoding="utf-8")
    assert body.startswith("#!/bin/sh\n")
    assert body.rstrip().endswith("exit 0")
    assert ih.scanner_reachability(body)[0] is True


@pytest.mark.parametrize("existing", [None, "lfs", "no-shebang"])
def test_every_produced_hook_is_valid_shell(hooks_dir, existing):
    if existing == "lfs":
        (hooks_dir / "pre-commit").write_text(_lfs_style_hook(), encoding="utf-8")
    elif existing == "no-shebang":
        (hooks_dir / "pre-commit").write_text("echo legacy\n", encoding="utf-8")
    ih.install_pre_commit(hooks_dir, check_only=False)
    out = subprocess.run(["sh", "-n", str(hooks_dir / "pre-commit")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr


def test_the_installed_hook_is_executable(hooks_dir):
    ih.install_pre_commit(hooks_dir, check_only=False)
    assert os.access(hooks_dir / "pre-commit", os.X_OK)


# ============================================================
# Finding 2 -- schema issues hidden behind staleness
# ============================================================
def _note(title, issues, is_stale):
    return {"file": f"{title}.md", "path": f"knowledge/{title}.md", "subdir": "fleeting",
            "id": "", "title": title, "type": "note", "status": "seed", "keywords": [],
            "confidence": "", "created": "2026-01-01", "updated": "", "links_out": [],
            "issues": issues, "is_stale": is_stale}


def test_a_stale_note_still_reports_its_other_schema_issues():
    notes = [_note("x", ["stale seed (40 days old)", "missing fields: id"], True)]
    report = kh.format_terminal_report(notes)
    assert "missing fields: id" in report


def test_the_stale_seed_line_is_not_repeated_in_the_schema_section():
    notes = [_note("x", ["stale seed (40 days old)", "missing fields: id"], True)]
    schema_part = kh.format_terminal_report(notes).split("Schema Issues:")[-1]
    assert "stale seed" not in schema_part


def test_the_terminal_and_json_reports_now_agree_on_the_issue_set():
    notes = [_note("x", ["stale seed (40 days old)", "invalid status: bogus"], True)]
    report = kh.format_terminal_report(notes)
    payload = json.loads(kh.format_json(notes))
    json_issues = {i for n in payload["schema_issues"] for i in n["issues"]}
    # Floored, then checked. The loop below is derived entirely from
    # `format_json`'s own output, so a `--json` that stops emitting issues makes
    # `json_issues` empty and this test - the one that exists to prove the two
    # consumers of one scan report the same picture - assert nothing at all.
    assert json_issues, "--json emitted no schema issue; the loop below is vacuous"
    assert "invalid status: bogus" in json_issues, (
        f"--json lost the schema issue this case is built from: {json_issues}")
    for issue in json_issues - {i for i in json_issues if "stale seed" in i}:
        assert issue in report, f"{issue!r} is in --json and not on the terminal"


def test_a_non_stale_note_still_reports_its_issues():
    """Regression: the path that always worked."""
    notes = [_note("y", ["missing fields: id"], False)]
    assert "missing fields: id" in kh.format_terminal_report(notes)


# ============================================================
# Finding 3 -- Tribe Leadership by substring
# ============================================================
_RULES_YAML = textwrap.dedent("""\
    sender_overrides:
      always_critical: []
      always_important: []
      always_normal: []
    internal_domains:
      - "internal.example"
""")


@pytest.fixture
def classify_from(tmp_path):
    """Classify one internal sender whose CRM relationship_type we choose."""
    from scripts.inbox_pulse.overrides import RulesEngine
    from scripts.inbox_pulse.rules import CheapClassifier

    contacts = tmp_path / "crm" / "contacts"
    contacts.mkdir(parents=True)
    (tmp_path / "context").mkdir(parents=True)
    (tmp_path / "threads" / "business").mkdir(parents=True)
    yaml_path = tmp_path / "rules.yaml"
    yaml_path.write_text(_RULES_YAML, encoding="utf-8")

    def run(relationship_type):
        (contacts / "jane.md").write_text(textwrap.dedent(f"""\
            ---
            entity_ref: jane
            relationship_type: {relationship_type}
            email: jane@internal.example
            ---

            # jane
        """), encoding="utf-8")
        clf = CheapClassifier(
            rules=RulesEngine(yaml_path=yaml_path),
            workspace_root=tmp_path,
            my_email="ceo@internal.example",
        )
        return clf.classify(
            sender_email="jane@internal.example", subject="hi",
            now=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
            recipients_to=["ceo@internal.example"],
        )

    return run


def test_current_tribe_leadership_is_still_promoted(classify_from):
    result = classify_from("tribe-leadership")
    assert result["tier_guess"] == "HIGH_LIKELY"


@pytest.mark.parametrize("relationship", [
    "ex-tribe-leadership",
    "former-tribe-leadership",
    "past-tribe-leadership",
])
def test_a_negating_prefix_is_not_current_leadership(classify_from, relationship):
    result = classify_from(relationship)
    assert result["tier_guess"] == "LOW", \
        f"{relationship} was treated as current leadership"


def test_a_subtype_is_still_leadership(classify_from):
    """`tests/inbox_pulse/test_rules.py` pins this, so an exact compare is wrong."""
    assert classify_from("tribe-leadership-active")["tier_guess"] == "HIGH_LIKELY"


def test_a_negating_suffix_is_the_known_gap_and_still_fires(classify_from):
    """Pinned as it stands, not as it ought to be. Nothing in the repo defines
    the subtype vocabulary, so a denylist here would be an invented taxonomy.
    If `-alumni` should be excluded, this test is where that decision lands."""
    assert classify_from("tribe-leadership-alumni")["tier_guess"] == "HIGH_LIKELY"


def test_case_and_surrounding_space_are_still_tolerated(classify_from):
    assert classify_from('" Tribe-Leadership "')["tier_guess"] == "HIGH_LIKELY"


def test_the_canonical_relationship_set_holds_only_the_bare_form():
    from scripts.inbox_pulse.rules import _HIGH_VALUE_RELATIONSHIPS
    assert "tribe-leadership" in _HIGH_VALUE_RELATIONSHIPS
    assert "ex-tribe-leadership" not in _HIGH_VALUE_RELATIONSHIPS


# ============================================================
# Finding 5 -- any line starting `---` closed the frontmatter
# ============================================================
def _md(tmp_path, body):
    p = tmp_path / "a.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_a_key_beginning_with_dashes_no_longer_ends_the_frontmatter(tmp_path):
    """`---note: x` is a legal YAML key and was read as the closing fence, so
    everything below it was thrown away and a partial dict returned."""
    fm = _extract_frontmatter(_md(tmp_path,
        "---\nemail: bob@x.com\n---note: internal\nrelationship_type: customer\n---\nbody\n"))
    assert fm is not None
    assert fm.get("relationship_type") == "customer"
    assert fm.get("---note") == "internal"


def test_an_unparseable_frontmatter_is_none_rather_than_a_partial_dict(tmp_path):
    """A bare `----` at column 0 is not valid YAML in a mapping. It used to be
    taken for the fence, so the lines above it parsed and the lines below were
    dropped without a word; the caller got a dict missing half its fields."""
    fm = _extract_frontmatter(_md(tmp_path,
        "---\nemail: bob@x.com\n----\nrelationship_type: customer\n---\nbody\n"))
    assert fm is None


def test_a_file_with_no_closing_fence_returns_none(tmp_path):
    """It used to borrow a rule from the BODY and parse whatever sat above."""
    fm = _extract_frontmatter(_md(tmp_path,
        "---\nemail: bob@x.com\nrelationship_type: customer\n\n# Notes\n----\nmore\n"))
    assert fm is None


def test_an_ordinary_contact_still_parses(tmp_path):
    fm = _extract_frontmatter(_md(tmp_path,
        "---\nemail: bob@x.com\nrelationship_type: customer\n---\n\n# bob\n"))
    assert fm == {"email": "bob@x.com", "relationship_type": "customer"}


def test_a_fence_with_trailing_spaces_still_closes(tmp_path):
    fm = _extract_frontmatter(_md(tmp_path, "---\nemail: b@x.com\n---   \nbody\n"))
    assert fm == {"email": "b@x.com"}


def test_a_crlf_fence_still_closes(tmp_path):
    fm = _extract_frontmatter(_md(tmp_path, "---\r\nemail: b@x.com\r\n---\r\nbody\r\n"))
    assert fm is not None and fm.get("email") == "b@x.com"


def test_an_indented_rule_inside_a_block_scalar_still_works(tmp_path):
    """Regression: this shape already worked, and the report's repro used it."""
    fm = _extract_frontmatter(_md(tmp_path,
        "---\nemail: b@x.com\nnotes: |\n  first\n  ---\n  second\n"
        "relationship_type: customer\n---\nbody\n"))
    assert fm.get("relationship_type") == "customer"


def test_a_file_with_no_frontmatter_at_all_is_still_none(tmp_path):
    assert _extract_frontmatter(_md(tmp_path, "# just a heading\n")) is None


# ============================================================
# Finding 4 -- the docstring that named the wrong defaults
# ============================================================
def test_the_linkedin_docstring_matches_the_actual_defaults():
    doc = " ".join((la.__doc__ or "").split())
    assert "Playwright Firefox DIRECTLY, with no proxy" in doc
    assert "Playwright Chromium through the Decodo residential proxy" not in doc


def test_the_defaults_really_are_firefox_and_no_proxy():
    """The docstring is checked against the parser, not against itself."""
    import argparse
    parser = argparse.ArgumentParser()
    src = (ROOT / "scripts" / "linkedin-activity.py").read_text(encoding="utf-8")
    assert 'default="firefox"' in src
    assert '"--proxy-slot", type=int, default=0' in src
    del parser, argparse


def test_the_correction_precedes_the_note_about_what_it_replaced():
    doc = " ".join((la.__doc__ or "").split())
    assert doc.index("Firefox DIRECTLY") < doc.index("Until 2026-08-25")


# ============================================================
# Findings 6 and 7 -- the installer that crashed, and the check that skipped
# ============================================================
@pytest.fixture
def fake_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git" / "hooks").mkdir(parents=True)
    return repo


def test_check_pre_commit_is_out_of_scope_without_a_config(fake_repo):
    assert igh.check_pre_commit(fake_repo) is None


def test_check_pre_commit_is_false_when_the_config_has_no_hook(fake_repo):
    (fake_repo / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    assert igh.check_pre_commit(fake_repo) is False


def test_check_pre_commit_is_false_for_a_hook_the_framework_did_not_write(fake_repo):
    (fake_repo / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    (fake_repo / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
    assert igh.check_pre_commit(fake_repo) is False


def test_check_pre_commit_is_true_for_a_framework_hook(fake_repo):
    (fake_repo / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    (fake_repo / ".git" / "hooks" / "pre-commit").write_text(
        f"#!/usr/bin/env bash\n# {igh.PRE_COMMIT_FRAMEWORK_MARKER}\n", encoding="utf-8")
    assert igh.check_pre_commit(fake_repo) is True


def test_the_missing_hook_report_names_the_repo_and_the_command(fake_repo, capsys):
    (fake_repo / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    assert igh._report_pre_commit(fake_repo, "engine") is False
    out = capsys.readouterr().out
    assert "pre-commit install" in out
    assert str(fake_repo) in out


def test_check_fails_when_the_commit_gate_is_absent(fake_repo, monkeypatch, capsys):
    """The whole point: the push gate present and the commit gate gone was exit 0."""
    (fake_repo / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    (fake_repo / ".git" / "hooks" / "pre-push").write_text(
        "#!/bin/sh\nrun-tests.py\n", encoding="utf-8")
    monkeypatch.setattr(igh, "get_workspace_root", lambda: fake_repo)
    monkeypatch.setattr(igh, "data_repo_to_gate", lambda d, e: None)
    monkeypatch.setattr(sys, "argv", ["install-git-hooks.py", "--check"])
    assert igh.main() == 1
    assert "pre-commit framework hook MISSING" in capsys.readouterr().out


def test_check_passes_when_both_gates_are_present(fake_repo, monkeypatch):
    (fake_repo / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    (fake_repo / ".git" / "hooks" / "pre-push").write_text(
        "#!/bin/sh\nrun-tests.py\n", encoding="utf-8")
    (fake_repo / ".git" / "hooks" / "pre-commit").write_text(
        f"# {igh.PRE_COMMIT_FRAMEWORK_MARKER}\n", encoding="utf-8")
    monkeypatch.setattr(igh, "get_workspace_root", lambda: fake_repo)
    monkeypatch.setattr(igh, "data_repo_to_gate", lambda d, e: None)
    monkeypatch.setattr(sys, "argv", ["install-git-hooks.py", "--check"])
    assert igh.main() == 0


def test_a_missing_hook_source_is_an_error_not_a_traceback(fake_repo, monkeypatch, capsys):
    monkeypatch.setattr(igh, "get_workspace_root", lambda: fake_repo)
    monkeypatch.setattr(igh, "data_repo_to_gate", lambda d, e: None)
    monkeypatch.setattr(sys, "argv", ["install-git-hooks.py"])
    assert igh.main() == 2
    err = capsys.readouterr().err
    assert "missing hook source" in err
    assert ".githooks" in err


def test_the_installer_still_installs_when_the_source_is_present(fake_repo, monkeypatch, capsys):
    (fake_repo / ".githooks").mkdir()
    (fake_repo / ".githooks" / "pre-push").write_text(
        "#!/bin/sh\nrun-tests.py\n", encoding="utf-8")
    monkeypatch.setattr(igh, "get_workspace_root", lambda: fake_repo)
    monkeypatch.setattr(igh, "data_repo_to_gate", lambda d, e: None)
    monkeypatch.setattr(igh, "ensure_pre_commit", lambda repo: None)
    monkeypatch.setattr(sys, "argv", ["install-git-hooks.py"])
    assert igh.main() == 0
    assert igh.check_pre_push(fake_repo) is True


# ============================================================
# The mutation harness: one wipe instead of one per run
# ============================================================
def test_the_child_is_forbidden_to_write_bytecode(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs.get("env") or {})
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(mutation_harness.subprocess, "run", fake_run)
    mutation_harness.run_tests(tmp_path, ["t.py"], timeout=5, memory_limit_bytes=0,
                               clear_cache=False)
    assert seen.get("PYTHONDONTWRITEBYTECODE") == "1"


def test_the_child_env_still_carries_the_rest_of_the_environment(tmp_path, monkeypatch):
    """`.claude/rules/trace-id.md`: an explicit env must be built from os.environ."""
    monkeypatch.setenv("X31C_TRACE_ID", "trace-09p2")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs.get("env") or {})
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(mutation_harness.subprocess, "run", fake_run)
    mutation_harness.run_tests(tmp_path, ["t.py"], timeout=5, memory_limit_bytes=0,
                               clear_cache=False)
    assert seen.get("X31C_TRACE_ID") == "trace-09p2"


def test_clear_cache_false_skips_the_repo_walk(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(mutation_harness, "_clear_pycache", lambda root: calls.append(root))
    monkeypatch.setattr(mutation_harness.subprocess, "run",
                        lambda cmd, **kw: type("R", (), {"returncode": 0})())
    mutation_harness.run_tests(tmp_path, ["t.py"], timeout=5, memory_limit_bytes=0,
                               clear_cache=False)
    assert calls == []


def test_clear_cache_defaults_to_true_for_a_direct_caller(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(mutation_harness, "_clear_pycache", lambda root: calls.append(root))
    monkeypatch.setattr(mutation_harness.subprocess, "run",
                        lambda cmd, **kw: type("R", (), {"returncode": 0})())
    mutation_harness.run_tests(tmp_path, ["t.py"], timeout=5, memory_limit_bytes=0)
    assert len(calls) == 1


def test_no_bytecode_survives_a_real_run(tmp_path):
    """The guarantee, end to end: after a run, nothing cached is on disk."""
    pkg = tmp_path / "mod.py"
    pkg.write_text("VALUE = 1\n", encoding="utf-8")
    test = tmp_path / "test_it.py"
    test.write_text("import mod\ndef test_v():\n    assert mod.VALUE == 1\n",
                    encoding="utf-8")
    outcome = mutation_harness.run_tests(tmp_path, ["test_it.py"], timeout=120,
                                         memory_limit_bytes=0, python=sys.executable)
    assert outcome == "pass"
    assert list(tmp_path.rglob("__pycache__")) == []
