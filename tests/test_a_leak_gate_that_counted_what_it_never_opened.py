#!/usr/bin/env python3
"""Shard scripts-12-p2: four gates that reported coverage they did not have.

Every finding here is the shape `.claude/rules/scope-claims.md` names: a tool
states an outcome its method never established. Three of them are the SAME
fail-open - a file that was never opened, counted as a file that came back clean.

  - `sanitize-text.py` returned 2 for an unreadable file and the entry point
    called a bare `main()`, so the code was DISCARDED and the process exited 0.
    Four callers read that code as the verdict: `artifact-evaluator` prints
    "Clean", `render-doctype` prints "[CLEAN] Hidden-character scan passed.",
    `inbox-pulse-report` prints "Hidden char scan: clean", and
    `crm_migrate_to_entity_model` carries on with the apply.
  - `sanitize-check.py --staged` asked git for names without `-z`, so any path
    holding a non-ASCII byte came back C-quoted, opened nothing, and was counted
    as scanned. `scripts/push-all.py` fixed this exact defect on 2026-08-23 and
    wrote the reason down; the fix was never carried here.
  - The same gate decided text-versus-binary on the SUFFIX alone, skipping 116
    of 1802 tracked files - 15 `*.tmpl` templates that ship to the fleet, 18
    `.xml`, 19 `.service`, 5 `.jsonl`, 16 with no extension - and still counted
    every one of them in "N files scanned".
  - The same gate's `except OSError: return []` made a permission-denied file
    clean, while `secret-scanner.py` already carried the ruling for this
    workspace: an unreadable file is UNKNOWN, not clean.
  - The same gate's docstring claimed `crm/contacts/` as a blocked path. No such
    term has ever been in SUBSTRING_CRITICAL.
  - `secret-scanner.py` skipped `.svg` as binary. SVG is text XML, and it was
    the only text format in the skip set.
  - Its `sys.exit(2)` for an unreadable file ran BEFORE the denial log, so a
    mixed run printed its refusals and recorded none, and exited 2 - which
    `publish-service.py` renders as "secret-scanner error" over a real leak.
  - `scrutinize-dispatch.py` decided "was this pytest?" by substring over every
    argv token. `scripts/run-tests.py` is this workspace's own test command,
    passes pytest's code through, and holds no such token, so a broken import
    was recorded as REPRODUCED for a check that never ran.
  - Its `--family claude` branch wrote a kind="verdict" row with verdict=None,
    which `validate()` counts - defeating the one backstop the module's own
    docstring names for that omission.
  - `sanitize-text.py --text ... -o out.md` accepted the flag and never wrote
    the file.

NOTE: this file carries four real zero-width spaces, on purpose. They are the
fixtures the hidden-character tests need, so `sanitize-text.py --scan` on this
file reports them and is RIGHT to. Same convention as
`tests/security/test_sanitize_text_subprocess.py`.

Run: .venv/bin/python -m pytest tests/test_a_leak_gate_that_counted_what_it_never_opened.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.code_only import strip_comments  # noqa: E402

SANITIZE_TEXT = ROOT / "scripts" / "sanitize-text.py"
SANITIZE_CHECK = ROOT / "scripts" / "sanitize-check.py"
SECRET_SCANNER = ROOT / "scripts" / "secret-scanner.py"


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "scripts" / stem))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def check():
    return _load("sanitize-check.py", "p12p2_sanitize_check")


@pytest.fixture(scope="module")
def scanner():
    return _load("secret-scanner.py", "p12p2_secret_scanner")


@pytest.fixture(scope="module")
def dispatch():
    return _load("scrutinize-dispatch.py", "p12p2_scrutinize_dispatch")


def _run(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                          cwd=str(ROOT), timeout=90, **kwargs)


# A credential-shaped fixture assembled from parts, so no literal that looks
# like a key exists in this file, in any command line, or in git history.
def _aws_shaped_token() -> str:
    return "AKIA" + "IOSFODNN7" + "EXAMPLE"


@pytest.fixture()
def unreadable(tmp_path):
    """A file that exists and cannot be read. Restored so tmp cleanup works."""
    made: list[Path] = []

    def _make(name: str = "locked.md", body: str = "hello\n") -> Path:
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o000)
        made.append(path)
        return path

    yield _make
    for path in made:
        path.chmod(0o644)


def _root_is_writable_by_this_user() -> bool:
    """chmod 000 does not stop root, so the unreadable tests are meaningless there."""
    return os.geteuid() == 0


skip_if_root = pytest.mark.skipif(
    _root_is_writable_by_this_user(),
    reason="chmod 000 does not block root, so an unreadable file cannot be staged")


# ============================================================
# 1 - the exit code that was computed and thrown away
# ============================================================
def test_an_unreadable_file_no_longer_exits_zero():
    """THE case. `return 2` reached a bare `main()` and the process exited 0."""
    proc = _run(str(SANITIZE_TEXT), "--scan", "/does/not/exist/at/all.md")
    assert proc.returncode == 2
    assert "cannot read" in proc.stderr
    # The PATH, not just the words: the fix's own comment says a hook chain
    # surfaces a traceback as "the hook failed" with nothing naming what was
    # wrong, and a message that drops the path repeats exactly that.
    assert "/does/not/exist/at/all.md" in proc.stderr


def test_the_sanitize_form_also_reports_the_unreadable_path():
    proc = _run(str(SANITIZE_TEXT), "/does/not/exist/at/all.md")
    assert proc.returncode == 2


def test_the_entry_point_propagates_the_return_value():
    src = SANITIZE_TEXT.read_text(encoding="utf-8")
    code = strip_comments(src)
    assert "sys.exit(main())" in code
    # The bare call must be gone: its own fix comment quotes it, so strip
    # comments before searching or the tombstone answers for the corpse.
    assert "\n    main()\n" not in code


def test_a_clean_file_still_exits_zero(tmp_path):
    good = tmp_path / "good.md"
    good.write_text("plain ascii prose\n", encoding="utf-8")
    proc = _run(str(SANITIZE_TEXT), "--scan", str(good))
    assert proc.returncode == 0


def test_a_dirty_file_still_exits_one(tmp_path):
    dirty = tmp_path / "dirty.md"
    dirty.write_text("zero\u200bwidth\n", encoding="utf-8")
    proc = _run(str(SANITIZE_TEXT), "--scan", str(dirty))
    assert proc.returncode == 1


def test_inline_text_still_exits_zero():
    proc = _run(str(SANITIZE_TEXT), "--scan", "--text", "hello world")
    assert proc.returncode == 0
    assert "Word count: 2" in proc.stderr


@skip_if_root
def test_a_permission_denied_file_is_not_reported_clean(unreadable):
    """The exact caller-visible consequence: four callers print "clean" on 0."""
    locked = unreadable()
    proc = _run(str(SANITIZE_TEXT), "--scan", str(locked))
    assert proc.returncode == 2


# ============================================================
# 2 - the output flag that was accepted and ignored
# ============================================================
def test_output_with_inline_text_is_refused(tmp_path):
    target = tmp_path / "never-written.md"
    proc = _run(str(SANITIZE_TEXT), "--text", "x", "-o", str(target))
    assert proc.returncode == 2
    # Loosened 2026-08-26: the assertion quoted the message VERBATIM, so adding
    # `--scan` to the same refusal (a third branch where `-o` is dead) failed a
    # test whose subject is `--text`. What this test owns is that inline text
    # with `-o` is refused and writes nothing; the exact list of named paths
    # belongs to the test that adds one.
    assert "--text" in proc.stderr
    assert "does nothing" in proc.stderr
    assert not target.exists()


def test_output_with_stdin_is_refused_too(tmp_path):
    """The stdin half is the same silent drop, via `or args.file == '-'`."""
    target = tmp_path / "never-written.md"
    proc = _run(str(SANITIZE_TEXT), "-", "-o", str(target), input="x\n")
    assert proc.returncode == 2
    assert not target.exists()


def test_output_with_a_real_file_still_works(tmp_path):
    src = tmp_path / "in.md"
    src.write_text("zero\u200bwidth\n", encoding="utf-8")
    out = tmp_path / "out.md"
    proc = _run(str(SANITIZE_TEXT), str(src), "-o", str(out))
    assert proc.returncode == 0
    assert out.read_text(encoding="utf-8") == "zerowidth\n"
    assert src.read_text(encoding="utf-8") == "zero\u200bwidth\n"


# ============================================================
# 3 - the staged listing that lost non-ASCII paths
# ============================================================
def test_the_staged_listing_uses_nul_separation(check, monkeypatch):
    """Behaviour, not a source grep.

    This asserted three substrings of the source, which is a control that reads
    the code instead of running it: it went red on 2026-08-29 for a change that
    made the splitting MORE correct (`text=True` dropped, so git's raw path
    bytes are decoded with `os.fsdecode` rather than the caller's locale). A
    grep cannot tell a regression from a refactor. Drive the function instead.
    """
    seen = {}

    class Fake:
        returncode = 0
        # Two paths, NUL-separated, the second holding a byte no ASCII codec
        # would survive. A `splitlines()` implementation returns ONE path here.
        stdout = "one.md\0документ.md\0".encode("utf-8")
        stderr = b""

    def spy(cmd, **kwargs):
        seen["cmd"] = cmd
        return Fake()

    monkeypatch.setattr(check.subprocess, "run", spy)
    assert check.staged_files() == [Path("one.md"), Path("документ.md")]
    assert "-z" in seen["cmd"]


def test_a_cyrillic_named_staged_file_is_actually_scanned(check, tmp_path, monkeypatch):
    """THE case. Without -z git returns `"докум/leak.md"`, quotes and all, and
    `Path()` on that string opens nothing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@example.invalid"],
                ["git", "config", "user.name", "T"]):
        subprocess.run(cmd, cwd=str(repo), check=True, capture_output=True)
    leaky = repo / "документ.md"
    leaky.write_text("contact me at someone@gmail.com\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True, capture_output=True)

    monkeypatch.setattr(check, "get_workspace_root", lambda: repo)
    staged = check.staged_files()
    assert [p.name for p in staged] == ["документ.md"]
    findings = check.scan_file(repo / staged[0], check.SUBSTRING_CRITICAL, set())
    assert findings, "the Cyrillic-named file was not scanned"


def test_an_ascii_staged_file_is_unaffected(check, tmp_path, monkeypatch):
    repo = tmp_path / "repo2"
    repo.mkdir()
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@example.invalid"],
                ["git", "config", "user.name", "T"]):
        subprocess.run(cmd, cwd=str(repo), check=True, capture_output=True)
    (repo / "plain.md").write_text("nothing here\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True, capture_output=True)
    monkeypatch.setattr(check, "get_workspace_root", lambda: repo)
    assert [str(p) for p in check.staged_files()] == ["plain.md"]


def test_a_broken_git_still_raises_rather_than_passing(check, tmp_path, monkeypatch):
    """The older fix in this file, unchanged by the -z change."""
    monkeypatch.setattr(check, "get_workspace_root", lambda: tmp_path)
    with pytest.raises(check.GitUnavailable):
        check.staged_files()


# ============================================================
# 4 - the files that were counted without being opened
# ============================================================
@pytest.mark.parametrize("name", [
    "template.md.tmpl", "unit.service", "unit.timer", "records.jsonl",
    "table.csv", "LICENSE", "pre-push", "page.xml", "drawing.svg",
])
def test_a_text_file_outside_the_suffix_allowlist_is_scanned(check, tmp_path, name):
    """Fifteen `.tmpl`, nineteen `.service`, sixteen extensionless files and the
    rest were skipped on their suffix and counted as scanned anyway."""
    path = tmp_path / name
    path.write_text("mail: someone@gmail.com\n", encoding="utf-8")
    findings = check.scan_file(path, check.SUBSTRING_CRITICAL, set())
    assert findings, f"{name} was skipped without being opened"


def test_a_real_binary_is_still_skipped(check, tmp_path):
    """The sniff must not turn the gate into a binary scanner."""
    blob = tmp_path / "logo.woff2"
    blob.write_bytes(b"wOF2\x00\x01\x00\x00" + b"\x00" * 64)
    assert check.scan_file(blob, check.SUBSTRING_CRITICAL, set()) is None


def test_a_suffix_allowlisted_file_takes_the_fast_path(check, tmp_path):
    path = tmp_path / "note.md"
    path.write_text("someone@gmail.com\n", encoding="utf-8")
    assert check.is_text_file(path) is True


def test_an_allowlisted_suffix_beats_the_sniff(check, tmp_path):
    """The fast path is load-bearing, not an optimisation.

    A declared-text extension must be scanned whatever its bytes look like. If
    the sniff governed `.md` too, one stray NUL byte in a markdown file would
    make the gate SKIP it - a silent skip of exactly the file class this gate
    exists for, decided by a heuristic instead of by the extension.
    """
    odd = tmp_path / "weird.md"
    odd.write_bytes(b"\x00 someone@gmail.com\n")
    assert check.is_text_file(odd) is True
    assert check.scan_file(odd, check.SUBSTRING_CRITICAL, set())


def test_a_missing_file_is_not_scanned_and_not_clean(check, tmp_path):
    assert check.scan_file(tmp_path / "gone.md", check.SUBSTRING_CRITICAL, set()) is None


# ------------------------------------------------------------
# 4b - the SAME rule, asked of the bytes in the index
# ------------------------------------------------------------
# `is_text_blob` carries its own copy of the suffix fast path and its own copy
# of the NUL sniff; `is_text_file` does not call it for the fast path, it
# repeats it. Two copies of one rule, and every test above drives only the
# on-disk one. MEASURED 2026-09-01: deleting the suffix fast path from
# `is_text_blob` alone left this file at 83 passed and the whole
# `sanitize-check` corpus at 120 passed, 0 failed. That mutant is a silent skip
# of a staged markdown blob carrying one NUL byte - decided by a heuristic
# instead of by the extension, on the `--staged` path that gates a commit.


def test_a_declared_text_blob_is_scanned_whatever_its_bytes(check):
    """The `is_text_file` twin of this is `test_an_allowlisted_suffix_beats_the
    _sniff`. This is the copy the `--staged` path uses, and it needs its own
    case or the two can drift apart unnoticed."""
    assert check.is_text_blob("weird.md", b"\x00 someone@gmail.com\n") is True


def test_a_staged_markdown_blob_holding_a_nul_is_still_scanned(check):
    """End of the same wire: `scan_blob` is what `--staged` calls, and `None`
    from it means the blob was never opened but still counted."""
    findings = check.scan_blob("weird.md", b"\x00 someone@gmail.com\n",
                               check.SUBSTRING_CRITICAL, set())
    assert findings, "a declared-text blob was skipped on one NUL byte"


def test_a_binary_blob_is_still_skipped(check):
    """The other direction: the sniff must not turn `--staged` into a binary
    scanner, and a name outside the allowlist is decided on its bytes."""
    assert check.is_text_blob("logo.woff2", b"wOF2\x00\x01\x00\x00" + b"\x00" * 64) is False
    assert check.scan_blob("logo.woff2", b"wOF2\x00\x01\x00\x00" + b"\x00" * 64,
                           check.SUBSTRING_CRITICAL, set()) is None


def test_an_extensionless_text_blob_is_scanned_on_its_bytes(check):
    """The suffix allowlist is a fast path, not the whole test, on this side
    too: `LICENSE` and `.githooks/pre-push` carry no suffix at all."""
    assert check.is_text_blob("pre-push", b"#!/bin/sh\nmail someone@gmail.com\n") is True
    assert check.scan_blob("pre-push", b"#!/bin/sh\nmail someone@gmail.com\n",
                           check.SUBSTRING_CRITICAL, set())


def test_the_pass_line_counts_only_files_that_were_opened(check, tmp_path, capsys,
                                                          monkeypatch):
    text = tmp_path / "a.md"
    text.write_text("nothing\n", encoding="utf-8")
    blob = tmp_path / "b.woff2"
    blob.write_bytes(b"\x00\x01\x02\x00")
    monkeypatch.setattr(check, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["prog", str(text), str(blob)])
    assert check.main() == 0
    out = capsys.readouterr().out
    assert "1 file(s) scanned" in out
    assert "1 file(s) not scanned" in out


def test_the_skipped_files_are_named_not_merely_counted(check, tmp_path, capsys,
                                                        monkeypatch):
    """Obligation 2 of scope-claims: silence about an exclusion reads as coverage."""
    text = tmp_path / "a.md"
    text.write_text("nothing\n", encoding="utf-8")
    blob = tmp_path / "b.woff2"
    blob.write_bytes(b"\x00\x01\x02\x00")
    monkeypatch.setattr(check, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["prog", str(text), str(blob)])
    check.main()
    assert "b.woff2" in capsys.readouterr().out


def test_a_real_finding_still_fails_the_gate(check, tmp_path, capsys, monkeypatch):
    leaky = tmp_path / "leak.md"
    leaky.write_text("write to someone@gmail.com\n", encoding="utf-8")
    monkeypatch.setattr(check, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["prog", str(leaky)])
    assert check.main() == 1
    assert "of 1 scanned file(s)" in capsys.readouterr().out


# ============================================================
# 5 - the unreadable file the gate called clean
# ============================================================
@skip_if_root
def test_an_unreadable_file_raises_instead_of_returning_empty(check, unreadable):
    locked = unreadable()
    with pytest.raises(check.UnreadableFile):
        check.scan_file(locked, check.SUBSTRING_CRITICAL, set())


@skip_if_root
def test_the_gate_exits_two_on_an_unreadable_file(check, unreadable, tmp_path,
                                                  capsys, monkeypatch):
    locked = unreadable()
    monkeypatch.setattr(check, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["prog", str(locked)])
    assert check.main() == 2
    assert "could not be read" in capsys.readouterr().err


@skip_if_root
def test_the_unreadable_path_is_named(check, unreadable, tmp_path, capsys,
                                      monkeypatch):
    locked = unreadable("secret-ish.md")
    monkeypatch.setattr(check, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["prog", str(locked)])
    check.main()
    assert "secret-ish.md" in capsys.readouterr().err


def test_the_unreadable_class_mirrors_the_secret_scanner(check, scanner):
    """Same name, same ruling, two gates. One of them had it and one did not."""
    assert issubclass(check.UnreadableFile, Exception)
    assert issubclass(scanner.UnreadableFile, Exception)


# ============================================================
# 6 - the docstring that claimed a term the code never had
# ============================================================
def test_the_docstring_no_longer_claims_an_unimplemented_path(check):
    doc = SANITIZE_CHECK.read_text(encoding="utf-8").split('"""')[1]
    claimed = doc.split("CEO-only file paths (", 1)[1].split(")", 1)[0]
    assert "crm/contacts" not in claimed


def test_every_path_the_docstring_claims_is_a_real_term(check):
    doc = SANITIZE_CHECK.read_text(encoding="utf-8").split('"""')[1]
    claimed = doc.split("CEO-only file paths (", 1)[1].split(")", 1)[0]
    terms = [t.strip().strip("`") for t in claimed.split(",")]
    for term in terms:
        assert any(term.rstrip("/") in existing for existing in check.SUBSTRING_CRITICAL), \
            f"docstring claims {term!r} but SUBSTRING_CRITICAL has {check.SUBSTRING_CRITICAL}"


def test_the_term_set_is_unchanged_by_this_shard(check):
    """The fix is to the SENTENCE. Adding a term is the operator's call: the
    path appears legitimately in the classification rule and in this file."""
    assert sorted(check.SUBSTRING_CRITICAL) == sorted(
        ["@gmail.com", "_secure/", "knowledge/odin-brain", "odin-brain-health"])


# ============================================================
# 7 - the text format filed under binary
# ============================================================
def test_svg_is_no_longer_skipped_as_binary(scanner):
    assert ".svg" not in scanner.SKIP_EXTENSIONS


def test_a_secret_in_an_svg_comment_is_found(scanner, tmp_path):
    drawing = tmp_path / "logo.svg"
    drawing.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg"><!-- {_aws_shaped_token()} -->'
        "</svg>\n", encoding="utf-8")
    assert scanner.scan_file(str(drawing))


def test_the_genuinely_binary_members_are_still_skipped(scanner):
    for ext in (".png", ".woff2", ".pptx", ".pen", ".session"):
        assert ext in scanner.SKIP_EXTENSIONS


def test_a_clean_svg_still_reports_nothing(scanner, tmp_path):
    drawing = tmp_path / "clean.svg"
    drawing.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>\n', encoding="utf-8")
    assert scanner.scan_file(str(drawing)) == []


# ============================================================
# 8 - the refusals that were printed and never recorded
# ============================================================
@skip_if_root
def test_a_mixed_run_exits_one_not_two(scanner, tmp_path, unreadable, monkeypatch):
    """`publish-service.py` renders exit 2 as "secret-scanner error", which is
    the wrong sentence to print over a detected leak."""
    leaky = tmp_path / "leak.md"
    leaky.write_text(f"key={_aws_shaped_token()}\n", encoding="utf-8")
    locked = unreadable()
    monkeypatch.setattr(scanner, "log_denial", lambda **kw: None)
    monkeypatch.setattr(sys, "argv", ["prog", str(leaky), str(locked)])
    with pytest.raises(SystemExit) as exc:
        scanner.main()
    assert exc.value.code == 1


@skip_if_root
def test_a_mixed_run_still_records_every_refusal(scanner, tmp_path, unreadable,
                                                 monkeypatch):
    """THE case. `sys.exit(2)` stood above the loop, so the denial log - the
    audit record - was skipped entirely whenever anything was unreadable."""
    leaky = tmp_path / "leak.md"
    leaky.write_text(f"key={_aws_shaped_token()}\n", encoding="utf-8")
    locked = unreadable()
    logged: list[dict] = []
    monkeypatch.setattr(scanner, "log_denial", lambda **kw: logged.append(kw))
    monkeypatch.setattr(sys, "argv", ["prog", str(leaky), str(locked)])
    with pytest.raises(SystemExit):
        scanner.main()
    assert [entry["path"] for entry in logged] == [str(leaky)]


@skip_if_root
def test_a_mixed_run_still_prints_both_reports(scanner, tmp_path, unreadable,
                                               monkeypatch, capsys):
    """The precedence changes the code, never what is shown."""
    leaky = tmp_path / "leak.md"
    leaky.write_text(f"key={_aws_shaped_token()}\n", encoding="utf-8")
    locked = unreadable()
    monkeypatch.setattr(scanner, "log_denial", lambda **kw: None)
    monkeypatch.setattr(sys, "argv", ["prog", str(leaky), str(locked)])
    with pytest.raises(SystemExit):
        scanner.main()
    captured = capsys.readouterr()
    assert "SECRETS DETECTED" in captured.out
    assert "could not be read" in captured.err
    # Named, not merely counted. A count with no names tells the operator that
    # something was skipped and never which thing.
    assert locked.name in captured.err


@skip_if_root
def test_an_unreadable_file_alone_still_exits_two(scanner, unreadable, monkeypatch):
    locked = unreadable()
    monkeypatch.setattr(sys, "argv", ["prog", str(locked)])
    with pytest.raises(SystemExit) as exc:
        scanner.main()
    assert exc.value.code == 2


def test_a_clean_run_still_exits_zero(scanner, tmp_path, monkeypatch):
    good = tmp_path / "fine.md"
    good.write_text("nothing to see\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["prog", str(good)])
    with pytest.raises(SystemExit) as exc:
        scanner.main()
    assert exc.value.code == 0


def test_a_plain_detection_still_exits_one(scanner, tmp_path, monkeypatch):
    leaky = tmp_path / "leak.md"
    leaky.write_text(f"key={_aws_shaped_token()}\n", encoding="utf-8")
    logged: list[dict] = []
    monkeypatch.setattr(scanner, "log_denial", lambda **kw: logged.append(kw))
    monkeypatch.setattr(sys, "argv", ["prog", str(leaky)])
    with pytest.raises(SystemExit) as exc:
        scanner.main()
    assert exc.value.code == 1
    assert logged


# ============================================================
# 9 - "was this pytest?" answered by a substring
# ============================================================
@pytest.mark.parametrize("cmd", [
    ["/x/.venv/bin/python", "-m", "pytest", "tests/a.py"],
    ["/x/.venv/bin/pytest", "tests/a.py"],
    ["py.test", "tests/a.py"],
    ["pytest"],
])
def test_a_real_pytest_invocation_is_recognised(dispatch, cmd):
    assert dispatch._is_pytest_command(cmd) is True


@pytest.mark.parametrize("cmd", [
    ["python", "scripts/check.py", "--log", "logs/pytest-run.log"],
    ["python", "scripts/run-tests.py"],
    ["make", "test"],
    ["python", "-m", "pytest-cov"],
    ["/opt/pytest-helper/bin/report"],
    ["tox", "-e", "pytest"],
    ["make", "pytest"],
    [],
])
def test_a_command_merely_carrying_the_word_is_not_pytest(dispatch, cmd):
    """The false-positive direction: a data-file argument cost a reproduction.

    `tox -e pytest` and `make pytest` are the reason a bare word is not enough:
    they are WRAPPERS, so their exit code is not pytest's contract. When one
    really is hiding pytest, the output half settles it.
    """
    assert dispatch._is_pytest_command(cmd) is False


def test_the_module_form_is_recognised_on_its_own(dispatch):
    """`-m pytest` must not depend on the path clause: neither `python` nor
    `pytest` here carries a separator."""
    assert dispatch._is_pytest_command(["python", "-m", "pytest"]) is True


def test_a_bare_binary_is_recognised_on_its_own(dispatch):
    """argv[0] `pytest` carries no separator and is not preceded by `-m`."""
    assert dispatch._is_pytest_command(["pytest", "-q"]) is True


def test_a_pathed_binary_away_from_argv_zero_is_recognised(dispatch):
    assert dispatch._is_pytest_command(
        ["env", "CI=1", "/x/.venv/bin/pytest", "-q"]) is True


def test_a_pytest_usage_error_is_refused_on_argv_alone(dispatch):
    """THE case for the argv half. `pytest --bogus-flag` exits 4 and its output
    carries NO did-not-run marker, so only argv can settle it."""
    run = dispatch._run([sys.executable, "-m", "pytest",
                         "--bogus-flag-xyz", "--collect-only"])
    assert run.exit_code == 4
    assert run.unusable is not None
    assert not dispatch._says_pytest_did_not_run(run.stdout_tail, run.stderr_tail)


def test_a_non_pytest_command_exiting_two_is_still_evidence(dispatch, tmp_path):
    """A script that exits 2 for its own reasons must remain usable evidence."""
    script = tmp_path / "pytest-named-thing.py"
    script.write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
    run = dispatch._run([sys.executable, str(script)])
    assert run.unusable is None
    assert run.exit_code == 2


def test_a_pytest_collection_error_is_still_refused(dispatch, tmp_path):
    broken = tmp_path / "test_broken.py"
    broken.write_text("import a_module_that_does_not_exist_xyz\n", encoding="utf-8")
    run = dispatch._run([sys.executable, "-m", "pytest", "-q",
                         "-p", "no:cacheprovider", str(broken)])
    assert run.unusable is not None
    assert "no test ran" in run.unusable


def test_a_pytest_failure_is_still_evidence(dispatch, tmp_path):
    """Exit 1 is the only non-zero code that may be recorded as REPRODUCED."""
    failing = tmp_path / "test_fails.py"
    failing.write_text("def test_x():\n    assert False\n", encoding="utf-8")
    run = dispatch._run([sys.executable, "-m", "pytest", "-q",
                         "-p", "no:cacheprovider", str(failing)])
    assert run.unusable is None
    assert run.exit_code == 1


def test_a_wrapper_hiding_pytest_is_caught_by_its_output(dispatch, tmp_path):
    """THE case. `scripts/run-tests.py` holds no "pytest" token and returns
    pytest's code verbatim, so a broken import was recorded as REPRODUCED."""
    wrapper = tmp_path / "run_tests_like.py"
    wrapper.write_text(
        "import sys\n"
        "print('test gate: FAIL (pytest exit 2)')\n"
        "sys.exit(2)\n", encoding="utf-8")
    run = dispatch._run([sys.executable, str(wrapper)])
    assert run.unusable is not None
    assert "wrapper" in run.unusable


@pytest.mark.parametrize("marker", [
    "Interrupted: 1 error during collection",
    "ERROR collecting tests/x.py",
    "no tests ran in 0.01s",
])
def test_the_output_markers_are_pytest_s_own_words(dispatch, marker):
    assert dispatch._says_pytest_did_not_run(marker, "") is True


@pytest.mark.parametrize("ordinary", [
    "1 failed, 3 passed in 0.4s",
    "AssertionError: expected 3, got 4",
    "",
])
def test_ordinary_output_does_not_trip_the_marker_scan(dispatch, ordinary):
    assert dispatch._says_pytest_did_not_run(ordinary, "") is False


def test_a_wrapper_exiting_one_is_still_evidence(dispatch, tmp_path):
    """Only the DID-NOT-RUN codes consult the markers; exit 1 never does."""
    wrapper = tmp_path / "wrapper_one.py"
    wrapper.write_text(
        "import sys\nprint('no tests ran')\nsys.exit(1)\n", encoding="utf-8")
    run = dispatch._run([sys.executable, str(wrapper)])
    assert run.unusable is None


# ============================================================
# 10 - the empty verdict that satisfied its own backstop
# ============================================================
# A `record_root` fixture used to sit here, promising to "point the run record
# at a temp tree so no real record is written". It was never requested by any
# test, and both of its patches were wrong: `scrutinize_record` has no
# `get_data_root` at all, and its `record_path()` takes no arguments while the
# stand-in took one. `raising=False` is what let both stand. Anyone who had
# reached for it would have got a fixture that protects nothing, or a TypeError.
# What actually keeps the live record untouched is the `append_row` patch each
# test below sets for itself.


def test_a_claude_judge_without_a_verdict_is_refused(dispatch, monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(dispatch, "append_row", lambda **kw: rows.append(kw))
    code = dispatch.judge(run_id="r1", target="t", finding_id="H1", pass_="2.5a",
                          brief="", family="claude", verdict=None)
    assert code == 1
    assert [r["kind"] for r in rows] == ["degraded"]


def test_a_blank_verdict_is_refused_too(dispatch, monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(dispatch, "append_row", lambda **kw: rows.append(kw))
    assert dispatch.judge(run_id="r1", target="t", finding_id="H1", pass_="2.5a",
                          brief="", family="claude", verdict="   ") == 1
    assert rows[0]["kind"] == "degraded"


def test_no_verdict_row_is_written_for_an_empty_verdict(dispatch, monkeypatch):
    """`validate()` counts verdict rows by kind alone, so the empty row PASSED
    the one check the module's docstring names as the backstop."""
    rows: list[dict] = []
    monkeypatch.setattr(dispatch, "append_row", lambda **kw: rows.append(kw))
    dispatch.judge(run_id="r1", target="t", finding_id="H1", pass_="2.5a",
                   brief="", family="claude", verdict=None)
    assert not [r for r in rows if r["kind"] == "verdict"]


def test_a_supplied_verdict_still_records_and_exits_zero(dispatch, monkeypatch):
    """The verdict is `REFUTED`, and it used to be `CONFIRMED`.

    `CONFIRMED` is in no vocabulary this workspace has: not
    `scrutinize_record.VERDICTS`, not the seven a judge may rule. The test
    passed anyway because the stub below accepts any keyword, so the only
    coverage the claude happy path had was a value the real record REFUSES.
    That is what hid the missing check for a year of edits: measured
    2026-08-29, `--verdict REFUTTED` against the real `append_row` was an
    uncaught ValueError with nothing written at all.

    The stub stays - this test is about the exit code and the row shape, not
    about the record - but the value it carries is now one that would survive
    the round trip.
    """
    rows: list[dict] = []
    monkeypatch.setattr(dispatch, "append_row", lambda **kw: rows.append(kw))
    code = dispatch.judge(run_id="r1", target="t", finding_id="H1", pass_="2.5a",
                          brief="", family="claude", verdict="REFUTED")
    assert code == 0
    assert rows[0]["kind"] == "verdict"
    assert rows[0]["verdict"] == "REFUTED"


def test_the_refusal_uses_the_code_the_table_reserves():
    """Exit 1 is documented as "degraded: no judge verdict was produced"."""
    doc = (ROOT / "scripts" / "scrutinize-dispatch.py").read_text(
        encoding="utf-8").split('"""')[1]
    table = doc.split("Exit codes:", 1)[1]
    assert "1  degraded: no judge verdict was produced" in table


# ============================================================
# 11 - the caller that would have reported a failure with no reason
# ============================================================
def test_the_artifact_evaluator_reads_stderr_for_the_reason():
    """The scanner names an unreadable path on stderr; reading stdout alone now
    yields an empty detail beside a failed check."""
    src = (ROOT / "scripts" / "artifact-evaluator.py").read_text(encoding="utf-8")
    code = strip_comments(src)
    assert "result.stdout.strip() or result.stderr.strip()" in code


def _evaluator_hidden_chars_check(path: Path) -> dict:
    proc = _run(str(ROOT / "scripts" / "artifact-evaluator.py"),
                "--path", str(path), "--json")
    payload = json.loads(proc.stdout)
    hits = [c for c in payload.get("checks", []) if c["name"] == "hidden_chars"]
    assert hits, proc.stdout[:400]
    return hits[0]


def test_the_evaluator_still_reports_clean_on_a_clean_file(tmp_path):
    good = tmp_path / "good.md"
    good.write_text("# Title\n\nPlain prose.\n", encoding="utf-8")
    assert _evaluator_hidden_chars_check(good)["status"] == "pass"


def test_the_evaluator_reports_a_dirty_file_as_failing(tmp_path):
    """The verdict must follow the scanner's exit code, in both directions."""
    dirty = tmp_path / "dirty.md"
    dirty.write_text("# Title\n\nZero\u200bwidth prose.\n", encoding="utf-8")
    assert _evaluator_hidden_chars_check(dirty)["status"] == "fail"


def test_the_evaluator_gives_a_reason_when_the_scan_cannot_read_the_path():
    """The detail must not be empty beside a failed check.

    Driven through `run_hidden_char_scan` rather than through `main`, because
    `evaluate_reference` calls `read_text` with no handler and dies on a
    traceback before any check runs. That crash is PRE-EXISTING, on a different
    code path, and outside this shard: named here, not fixed.
    """
    evaluator = _load("artifact-evaluator.py", "p12p2_artifact_evaluator")
    result = evaluator.run_hidden_char_scan(Path("/does/not/exist/at/all.md"))
    assert result["status"] == "fail"
    assert result["detail"].strip(), "a failed check with no reason"
    assert "cannot read" in result["detail"]
