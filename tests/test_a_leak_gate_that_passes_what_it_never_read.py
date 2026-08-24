#!/usr/bin/env python3
"""Shard scripts-10-p3: gates that failed open, and a record that could be wrong.

The gates:
  - `sanitize-check --staged` returned `[]` when git itself failed -- no repo,
    no git binary, a corrupt index -- and main() printed "No staged changes to
    scan" and exited 0. It also ran git in the CALLER's directory, so from
    anywhere else it scanned a different repo's staged set against engine paths
    that do not exist, which is another silent pass.
  - A file named explicitly on the command line but absent was counted as
    scanned-and-clean. A typo'd path in a publish pipeline got a green PASS.
  - `secret-scanner` returned no findings for a file it could not read, so a
    permission error passed the pre-commit gate as "No secrets detected."

The record:
  - `scrutinize-dispatch` took the FIRST verdict-looking token anywhere in a
    judge's free text, so "This is not REFUTED because ... Overall:
    CORRECT_DOWNGRADE" was recorded as REFUTED -- into the artefact --validate
    reconciles as ground truth.
  - The kimi judge accepted an empty brief and recorded the verdict it got back.
  - `stratified_sample` capped every tier at n//5 and never redistributed, so
    `--sample 8` over 500 findings returned 2.

And two irreversible-action fixes in `send-email.py`: `--cc/--bcc` were accepted
and silently discarded in every threaded mode, and an attachment failure escaped
the batch loop mid-run. Nothing here sends anything; the send path is exercised
only by reading it.

Run: .venv/bin/python -m pytest tests/test_a_leak_gate_that_passes_what_it_never_read.py -q
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sc = _load("sanitize_check_p10c", "scripts/sanitize-check.py")
ss = _load("secret_scanner_p10c", "scripts/secret-scanner.py")
sd = _load("scrutinize_dispatch_p10c", "scripts/scrutinize-dispatch.py")
sr = _load("scrutinize_replay_p10c", "scripts/scrutinize-replay.py")


# ============================================================
# 1 - a broken git is a failure, not an empty staged set
# ============================================================
def test_a_failing_git_raises_instead_of_reporting_nothing_staged(monkeypatch):
    class Fake:
        returncode = 128
        stdout = ""
        stderr = "fatal: not a git repository"

    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: Fake())
    with pytest.raises(sc.GitUnavailable, match="not a git repository"):
        sc.staged_files()


def test_a_genuinely_empty_staged_set_is_still_empty(monkeypatch):
    """The guard must not turn "nothing staged" into an error."""
    class Fake:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: Fake())
    assert sc.staged_files() == []


def test_git_is_run_in_the_workspace_root(monkeypatch):
    """Without `cwd`, the command reported whatever repo the caller's directory
    sat in, and those paths then failed to exist under the engine root."""
    seen = {}

    class Fake:
        returncode = 0
        stdout = ""
        stderr = ""

    def spy(cmd, **kwargs):
        seen.update(kwargs)
        return Fake()

    monkeypatch.setattr(sc.subprocess, "run", spy)
    sc.staged_files()
    assert seen.get("cwd") == str(sc.get_workspace_root())


# ============================================================
# 2 - a named file that is absent is an error, not a clean pass
# ============================================================
def test_a_missing_explicit_file_exits_2():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sanitize-check.py"),
         "definitely-not-a-real-file-9f2a.md"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "not found" in proc.stderr


def test_a_real_file_still_scans_and_passes(tmp_path):
    target = tmp_path / "clean.md"
    target.write_text("Nothing sensitive here.\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sanitize-check.py"), str(target)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ============================================================
# 3 - an unreadable file is unknown, never clean
# ============================================================
def test_an_unreadable_file_raises_rather_than_scanning_clean(tmp_path, monkeypatch):
    target = tmp_path / "locked.md"
    target.write_text("x\n", encoding="utf-8")

    def deny(*a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("builtins.open", deny)
    with pytest.raises(ss.UnreadableFile):
        ss.scan_file(str(target))


def test_scan_files_collects_the_unreadable_instead_of_dropping_them(
        tmp_path, monkeypatch):
    target = tmp_path / "locked.md"
    target.write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(ss, "scan_file",
                        lambda p: (_ for _ in ()).throw(ss.UnreadableFile(f"{p}: denied")))
    unreadable = []
    results = ss.scan_files([str(target)], unreadable)
    assert results == {}
    assert len(unreadable) == 1 and "denied" in unreadable[0]


def test_the_scanner_exits_2_when_a_file_could_not_be_read(tmp_path):
    """End to end: the gate must not print "No secrets detected" over a file it
    never opened."""
    target = tmp_path / "locked.md"
    target.write_text("nothing\n", encoding="utf-8")
    target.chmod(0o000)
    try:
        if os.access(target, os.R_OK):
            pytest.skip("running as a user that ignores the mode bits (root)")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "secret-scanner.py"), str(target)],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60)
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "could not be read" in proc.stderr
    finally:
        target.chmod(0o644)


# ============================================================
# 4 - the recursive sweep skips the trees that are not source
# ============================================================
def test_the_walk_prunes_vcs_and_dependency_trees(tmp_path):
    (tmp_path / ".git" / "objects").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "pack").write_text("binary", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("x", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x", encoding="utf-8")
    found = {p.name for p in ss._walk_scannable(tmp_path)}
    assert found == {"app.py"}, found


def test_the_walk_skips_an_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "WALK_MAX_BYTES", 10)
    (tmp_path / "small.py").write_text("x", encoding="utf-8")
    (tmp_path / "huge.py").write_text("y" * 100, encoding="utf-8")
    found = {p.name for p in ss._walk_scannable(tmp_path)}
    assert found == {"small.py"}, found


# ============================================================
# 5 - the recorded verdict is the judge's ruling
# ============================================================
def test_a_verdict_discussed_before_the_ruling_is_not_the_ruling():
    text = ("This is not REFUTED, because the evidence holds up under the "
            "second reading.\n\nVERDICT: CORRECT_DOWNGRADE\n")
    assert sd._verdict_in(text) == "CORRECT_DOWNGRADE"


def test_a_restated_verdict_line_wins_over_an_earlier_one():
    text = "VERDICT: AMBIGUOUS\n\nOn reflection:\n\nVERDICT: REFUTED\n"
    assert sd._verdict_in(text) == "REFUTED"


def test_a_bare_conclusion_at_the_end_is_taken():
    """No structured line: the conclusion is where the prose ends, not where it
    starts."""
    text = "We considered whether this is REFUTED. It is not. Overall: CORRECT\n"
    assert sd._verdict_in(text) == "CORRECT"


def test_no_verdict_at_all_is_none():
    assert sd._verdict_in("I could not decide.") is None
    assert sd._verdict_in("") is None


# ============================================================
# 6 - a stratified sample reaches the size it was asked for
# ============================================================
def _sample(i, sev):
    return sr.FindingSample(scrutiny_id="r", finding_id=f"{sev[0]}{i}", severity=sev,
                            confidence=None, statement="s", location="", evidence="",
                            was_flagged_fp=False)


def test_a_lopsided_pool_still_fills_the_requested_sample():
    """Each tier was capped at n//5 with no redistribution, so 500 MEDIUM plus
    one BLOCKER yielded 2 of the 8 asked for."""
    pool = [_sample(i, "MEDIUM") for i in range(500)] + [_sample(1, "BLOCKER")]
    got = sr.stratified_sample(pool, 8)
    assert len(got) == 8, len(got)
    assert any(g.severity == "BLOCKER" for g in got), "the thin tier was crowded out"


def test_a_pool_smaller_than_the_sample_returns_the_whole_pool():
    pool = [_sample(i, "MEDIUM") for i in range(3)]
    assert len(sr.stratified_sample(pool, 8)) == 3


def test_a_balanced_pool_is_still_balanced():
    pool = [_sample(i, sev) for sev in ("BLOCKER", "HIGH", "MEDIUM", "LOW", "NIT")
            for i in range(10)]
    got = sr.stratified_sample(pool, 10)
    assert len(got) == 10
    assert len({g.severity for g in got}) >= 4


def test_the_sample_is_deterministic():
    pool = [_sample(i, "MEDIUM") for i in range(50)]
    first = [g.finding_id for g in sr.stratified_sample(pool, 8)]
    second = [g.finding_id for g in sr.stratified_sample(pool, 8)]
    assert first == second


def test_the_top_up_draws_from_across_the_pool_not_off_one_end():
    """Deterministic is not the same as unbiased. Replacing the shuffle with any
    fixed ordering (reverse, as-is) still passes the determinism test above
    while making the top-up a slice off one end of the pool -- so a 500-item
    MEDIUM tier would always contribute the same neighbours."""
    pool = [_sample(i, "MEDIUM") for i in range(200)]
    picked = sorted(int(g.finding_id[1:]) for g in sr.stratified_sample(pool, 40))
    head = [i for i in picked if i < 40]
    tail = [i for i in picked if i >= 160]
    assert len(head) < 40 and len(tail) < 40, picked
    assert max(picked) - min(picked) > 100, picked


# ============================================================
# 7 - the outbound path: read, never run
# ============================================================
def test_send_email_still_refuses_to_run_from_inside_a_test():
    """This guard is why the check below is a source assertion.

    `send-email.py` exits before argparse when it detects a test run, so the
    parser cannot be exercised through a subprocess here -- and that is the
    correct order. The guard stays; the test bends around it.
    """
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "send-email.py"),
         "--forward", "--match-id", "abc", "--to", "a@example.com",
         "--cc", "b@example.com", "--body", "hi"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "will not send from inside a test run" in proc.stderr


def test_cc_and_bcc_are_refused_in_threaded_mode_not_dropped():
    """Silent loss on an irreversible action, converted to a loud refusal.

    `_send_threaded_core` accepts cc/bcc and passes neither to create_reply /
    create_reply_all / create_forward, so `--forward --to X --cc Y` sent without
    Y and said nothing. Asserted at the source because the run-time guard above
    stops any subprocess from reaching the parser.
    """
    src = (ROOT / "scripts" / "send-email.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    # BOTH halves. Asserting the message alone left the guard removable: disable
    # the branch and the string still sits in the file.
    assert "--cc/--bcc are not supported with --" in code
    assert "if args.cc or args.bcc:" in code, "the refusal branch is gone"
    # And the reason the refusal is needed: still no cc wiring in the threaded core.
    threaded = src.split("def _send_threaded_core", 1)[1].split("\ndef ", 1)[0]
    assert "cc_recipients" not in threaded, (
        "cc is wired into the threaded core now -- replace the refusal with a "
        "real test against it")


def test_the_attach_loops_are_guarded_in_both_send_cores():
    """An oversized attachment used to raise through send_batch, killing every
    remaining message with a traceback and no per-message result."""
    src = (ROOT / "scripts" / "send-email.py").read_text(encoding="utf-8")
    assert src.count('"error": f"attach failed ({e});') == 2, src.count("attach failed")


def test_the_dead_sig_attachments_parameter_is_gone():
    import ast
    tree = ast.parse((ROOT / "scripts" / "send-email.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_send_email_core":
            names = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
            assert "sig_attachments" not in names, names
            break
    else:
        pytest.fail("_send_email_core not found")


def test_the_public_docstring_carries_no_real_looking_counterparty():
    src = (ROOT / "scripts" / "send-email.py").read_text(encoding="utf-8")
    doc = src.split('"""', 2)[1]
    for token in ("globex.com", "pat.nolan", "carol@31c.io", "dave@31c.io"):
        assert token not in doc, token
    assert "example.com" in doc or "example.org" in doc
