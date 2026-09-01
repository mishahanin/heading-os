#!/usr/bin/env python3
"""Both secret walls read git's `-z` bytes exactly and then joined them with "\\n".

`scripts/push-all.py` and `scripts/publish-service.py` each list the files a push
or a publish would carry with `git ... -z`, decode the raw bytes with
`surrogateescape`, and say so at length in their own comments: a name is a name,
and a mistranslated one is a file the scanner never opens. Both then handed that
list to `scripts/secret-scanner.py --stdin`, which reads ONE PATH PER LINE. The
join undid the listing one line after it was done.

A second, quieter half sat inside the scanner: `scan_files` called
`filepath.strip()` on every entry, so a leading or trailing space - legal in a
POSIX filename, and reported verbatim by `git ls-files -z` - became a name that
opens nothing. `_z_paths` in push-all carries the comment "No `.strip()`: a
filename may legally begin or end with whitespace" directly above the call that
fed it into a strip.

In both halves the unreadable name fell into `if not Path(filepath).is_file():
continue`, which is a deliberate SILENT skip for the stdin modes (a path that
vanished between the listing and the scan is legitimate there). So the scanner
printed "No secrets detected.", exited 0, and both gates reported clean.

MEASURED 2026-09-01 in scratch repositories under /tmp, one `git init` each, a
synthesised 36-character `ghp_`-shaped token as the whole content of one tracked
file:

    tracked filename        | publish-service.secret_scan | push-all.content_scan
    ------------------------+-----------------------------+----------------------
    "two\\nlines.env"        | True  -> False              | passed -> exit 2
    " leading.env"          | True  -> False              | passed -> exit 2
    "trailing.env "         | True  -> False              | passed -> exit 2
    "creds.env" (control)   | False -> False              | exit 2 -> exit 2
    clean tree (control)    | True  -> True               | passed -> passed

The fix is a NUL-delimited handoff: `secret-scanner.py --stdin0` reads
`sys.stdin.buffer`, decodes `surrogateescape`, splits on NUL and strips nothing;
both callers write NUL-joined bytes. The line-oriented `--stdin` is unchanged,
because the standalone pre-commit hook `install-hooks.py` writes drives it and
already REFUSES any path git had to escape (see
`tests/test_a_secret_gate_a_space_in_a_filename_walked_through.py`).

Nothing here pushes, commits into this repository, or reaches a network. Every
repository is `git init`ed under `tmp_path`.

`push-all.py` is loaded BY PATH: it calls `ensure_venv()` at module scope, so a
plain import `os.execv`s the whole pytest process under any interpreter that is
not `.venv/bin/python`.

Run: .venv/bin/python -m pytest
     tests/test_two_secret_walls_that_split_a_filename_in_half.py -q
"""
from __future__ import annotations

import importlib.util
import os
import string
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pubsvc = _load("publish_service_nulhandoff", "scripts/publish-service.py")
push_all = _load("push_all_nulhandoff", "scripts/push-all.py")
scanner = _load("secret_scanner_nulhandoff", "scripts/secret-scanner.py")

SCANNER = ROOT / "scripts" / "secret-scanner.py"

# Synthesised at import, never written out as one literal: a real-shaped token
# spelled into this file would be stopped by the repository's own commit gate
# before the test could run. 36 characters after the prefix, the shape the
# scanner matches on.
TOKEN = "ghp" + "_" + (string.ascii_lowercase + string.digits * 2)[:36]

# Every name here is legal on ext4 and reported verbatim by `git ls-files -z`.
# `two\nlines.env` is the newline case, the two spaced names are the strip case.
AWKWARD_NAMES = [
    pytest.param("two\nlines.env", id="newline-in-the-name"),
    pytest.param(" leading.env", id="leading-space"),
    pytest.param("trailing.env ", id="trailing-space"),
    pytest.param("creds.env", id="control-ordinary-name"),
]


def _repo_with(tmp_path: Path, name: str, content: str) -> Path:
    dest = tmp_path / "repo"
    dest.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    (dest / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(dest), "add", "-A"], check=True)
    return dest


def _tracked_names(repo: Path) -> list[bytes]:
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"],
                         capture_output=True, check=True).stdout
    return [n for n in out.split(b"\0") if n]


# ============================================================
# 1 - the premise: git really does report these names whole
# ============================================================

@pytest.mark.parametrize("name", AWKWARD_NAMES)
def test_git_reports_the_awkward_name_as_one_path(tmp_path, name):
    """If git ever started splitting or escaping these under `-z`, the gates
    below would be defending a shape that no longer arrives."""
    repo = _repo_with(tmp_path, name, "harmless\n")
    assert _tracked_names(repo) == [os.fsencode(name)]


# ============================================================
# 2 - publish-service refuses the token whatever the file is called
# ============================================================

@pytest.mark.parametrize("name", AWKWARD_NAMES)
def test_the_publish_gate_refuses_a_token_under_any_legal_name(tmp_path, name,
                                                               capsys):
    repo = _repo_with(tmp_path, name, TOKEN + "\n")
    assert pubsvc.secret_scan(repo) is False
    assert "REFUSING TO PUBLISH" in capsys.readouterr().out


def test_the_publish_gate_still_clears_a_clean_tree(tmp_path):
    """The over-refusal direction. A gate that refused every tree would pass
    every case above and be deleted by the first person who hit it."""
    repo = _repo_with(tmp_path, "README.md", "# nothing secret here\n")
    assert pubsvc.secret_scan(repo) is True


def test_the_publish_gate_still_clears_a_clean_awkward_name(tmp_path):
    """And the awkward name alone is not the offence: the TOKEN is."""
    repo = _repo_with(tmp_path, "two\nlines.env", "PORT=8080\n")
    assert pubsvc.secret_scan(repo) is True


# ============================================================
# 3 - the push wall, which is the unbypassable one
# ============================================================

@pytest.mark.parametrize("name", AWKWARD_NAMES)
def test_the_push_wall_refuses_a_token_under_any_legal_name(tmp_path, name):
    repo = _repo_with(tmp_path, name, TOKEN + "\n")
    with pytest.raises(SystemExit) as exited:
        push_all.content_scan(repo)
    assert exited.value.code == 2


def test_the_push_wall_still_clears_a_clean_tree(tmp_path):
    repo = _repo_with(tmp_path, "README.md", "# nothing secret here\n")
    push_all.content_scan(repo)          # must not raise SystemExit


def test_the_push_wall_refuses_the_awkward_name_beside_an_ordinary_one(tmp_path):
    """TWO tracked files, which is what makes the join observable at all.

    With a single path a `"\\n".join` and a `b"\\0".join` produce identical
    bytes, so every one-file case above stays green against the defect. This
    one puts a harmless file in front of the offending one: joined by newline
    the child sees a single nonexistent name and skips it, and the gate passes
    a push carrying the token.
    """
    dest = tmp_path / "repo"
    dest.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    (dest / "harmless.md").write_text("nothing here\n", encoding="utf-8")
    (dest / "two\nlines.env").write_text(TOKEN + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(dest), "add", "-A"], check=True)

    with pytest.raises(SystemExit) as exited:
        push_all.content_scan(dest)
    assert exited.value.code == 2


def test_the_publish_gate_refuses_the_awkward_name_beside_an_ordinary_one(
        tmp_path, capsys):
    """The same two-file shape on the publishing side."""
    dest = tmp_path / "mirror"
    dest.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    (dest / "harmless.md").write_text("nothing here\n", encoding="utf-8")
    (dest / "two\nlines.env").write_text(TOKEN + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(dest), "add", "-A"], check=True)

    assert pubsvc.secret_scan(dest) is False
    assert "REFUSING TO PUBLISH" in capsys.readouterr().out


def test_the_push_wall_lists_the_awkward_name_before_it_scans(tmp_path):
    """The delta resolver and the scanner have to agree about the name.

    A push wall that refused because the LISTING crashed would satisfy the
    parametrized case above while measuring nothing about the handoff.
    """
    repo = _repo_with(tmp_path, "two\nlines.env", "harmless\n")
    assert push_all._push_delta_files(repo) == {"two\nlines.env"}


# ============================================================
# 3b - the two refusal reasons stay apart
# ============================================================

@pytest.mark.parametrize("code,expected,forbidden", [
    (1, "secret-like CONTENT", "secret-scanner error"),
    (2, "secret-scanner error", "secret-like CONTENT"),
])
def test_the_publish_gate_names_the_right_reason_for_each_scanner_exit(
        tmp_path, monkeypatch, capsys, code, expected, forbidden):
    """Exit 1 is a found secret; exit 2 is unread coverage. Different sentences.

    `secret-scanner.py` carries a comment saying this branch, "which branches on
    1-vs-2, printed 'secret-scanner error' over a real leak" - so the split was
    made deliberately and then nothing held it. MEASURED 2026-09-01: collapsing
    both arms to the scanner-error wording survived all 148 tests across the
    four files that touch this gate, including the one whose docstring names the
    rendering as the reason it exists.

    The sibling branch in `push-all._refuse_on_scanner` was already covered; only
    this copy was not, which is the usual shape of a fix that landed in one of
    two places.
    """
    dest = _repo_with(tmp_path, "notes.md", "harmless\n")
    real_run = subprocess.run

    def stubbed(cmd, *args, **kwargs):
        if isinstance(cmd, list) and str(SCANNER) in " ".join(str(c) for c in cmd):
            # BYTES, because the caller no longer runs the child in text mode.
            # A str-shaped double would stand in for a call this code does not
            # make, and the decode below would raise on it.
            assert not (kwargs.get("text") or kwargs.get("encoding")), (
                "the scanner handoff must stay in bytes mode")
            return subprocess.CompletedProcess(cmd, code, b"", b"")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(pubsvc.subprocess, "run", stubbed)
    assert pubsvc.secret_scan(dest) is False
    out = capsys.readouterr().out
    assert expected in out, out
    assert forbidden not in out, out


# ============================================================
# 4 - the scanner's own NUL reader
# ============================================================

def _run_scanner(argv: list[str], payload: bytes, cwd: Path):
    return subprocess.run([sys.executable, str(SCANNER), *argv],
                          input=payload, cwd=str(cwd),
                          capture_output=True, timeout=120, check=False)


def test_stdin0_reads_a_newline_bearing_name_as_one_path(tmp_path):
    (tmp_path / "two\nlines.env").write_text(TOKEN + "\n", encoding="utf-8")
    proc = _run_scanner(["--stdin0"], b"two\nlines.env", tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert b"SECRETS DETECTED" in proc.stdout


@pytest.mark.parametrize("name", [" leading.env", "trailing.env "])
def test_stdin0_does_not_strip_a_padded_name(tmp_path, name):
    (tmp_path / name).write_text(TOKEN + "\n", encoding="utf-8")
    proc = _run_scanner(["--stdin0"], os.fsencode(name), tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_stdin0_reads_a_name_that_is_not_valid_utf8(tmp_path):
    """The other half of what `-z` plus `surrogateescape` preserves.

    Both callers encode with `surrogateescape`, so the bytes reach the child
    unchanged; the child has to decode them the same way or the name it opens
    is not the name git reported.
    """
    raw = b"not-utf8-\xff.env"
    (tmp_path / os.fsdecode(raw)).write_text(TOKEN + "\n", encoding="utf-8")
    proc = _run_scanner(["--stdin0"], raw, tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_stdin0_still_reports_a_clean_list_as_clean(tmp_path):
    """Anchor: a reader that refused every list would pass all four above."""
    (tmp_path / "two\nlines.env").write_text("PORT=8080\n", encoding="utf-8")
    proc = _run_scanner(["--stdin0"], b"two\nlines.env", tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert b"No secrets detected" in proc.stdout


def test_stdin0_scans_every_name_in_a_multi_entry_list(tmp_path):
    """One NUL-separated pair, the offending file second.

    A reader that took only the first entry would pass
    `test_stdin0_reads_a_newline_bearing_name_as_one_path` unchanged.
    """
    (tmp_path / "a.md").write_text("harmless\n", encoding="utf-8")
    (tmp_path / "two\nlines.env").write_text(TOKEN + "\n", encoding="utf-8")
    proc = _run_scanner(["--stdin0"], b"a.md\0two\nlines.env", tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_the_line_oriented_stdin_mode_is_unchanged(tmp_path):
    """`--stdin` still splits on newlines, and still tolerates padded lines.

    The standalone pre-commit hook `install-hooks.py` writes drives this mode
    and refuses an escaped path before it ever gets here, so widening it was
    never the fix. Both properties are pinned, because moving the strip out of
    `scan_files` could have taken the second one with it.
    """
    (tmp_path / "a.md").write_text("harmless\n", encoding="utf-8")
    (tmp_path / "b.env").write_text(TOKEN + "\n", encoding="utf-8")
    proc = _run_scanner(["--stdin"], b"  a.md  \n b.env \n", tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert b"b.env" in proc.stdout


def test_a_bare_invocation_still_prints_usage(tmp_path):
    """Adding a fourth mode must not make "no mode at all" one of them."""
    proc = _run_scanner([], b"", tmp_path)
    assert proc.returncode == 2


# ============================================================
# 5 - the callers ask for the NUL mode, not the line mode
# ============================================================

@pytest.mark.parametrize("rel,func", [
    ("scripts/push-all.py", "_run_scanner"),
    ("scripts/publish-service.py", "secret_scan"),
])
def test_neither_wall_joins_the_listing_with_a_newline(rel, func):
    """Structural backstop for the two behavioural sections above.

    They drive the real gates, so they are the measurement. This exists because
    the defect is one character wide and reads as harmless: a future edit that
    reintroduces `"\\n".join(...)` in the handoff should be refused here with
    the reason attached, not diagnosed again from a scratch repository.

    The whole FUNCTION is the window, taken from the AST. A fixed-width slice
    after the `SCANNER` reference was the first spelling and it could not see
    push-all at all: there the join sits on the line ABOVE the subprocess call,
    outside the window, so reverting that one line left this green.
    """
    import ast

    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == func)
    body = ast.unparse(fn)
    assert "--stdin0" in body, body
    assert "'\\n'.join" not in body, body
