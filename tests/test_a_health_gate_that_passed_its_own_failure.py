#!/usr/bin/env python3
"""Shard scripts-11-p4: three gates, each green over something it never checked.

`cliproxyapi_update.py` swaps a running binary and rolls back if the new one is
unhealthy. Its health gate was `"HTTP 200" in (stdout + stderr)`. The canonical
failure message of a health tool is `expected HTTP 200, got HTTP 502`, which
CONTAINS that substring -- so a dead service passed, the rollback never fired,
and the swap was reported as a success.

The same file compared a `v7.2.104` GitHub tag against a grep-normalised
`7.2.104` binary version, so the "already current" short-circuit never fired:
every scheduled run stopped the service, swapped the same version in, restarted,
and overwrote the backup. `==` is also not an ordering, so a retracted release
resolving "latest" to something older downgraded happily.

`validate-crm-schema.py` printed "All 0 records pass schema." and exited 0 for a
typo'd `--dir`, a moved CRM tree, or a fresh clone -- a fully green gate over
nothing validated. The file's own comment block records this exact fail-open
class as a measured incident; it had been closed for the missing-jsonschema path
and left open here.

`verify-skills-lock.py` hashed through symlinks, so a vendored file replaced by
a link to an identical-content file elsewhere hashed the same -- the one
substitution the verifier exists to catch. And `--relock` returned 0 while
discarding the count of entries it could not verify.

Run: .venv/bin/python -m pytest tests/test_a_health_gate_that_passed_its_own_failure.py -q
"""

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.workspace import data_root_is_demo  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cpx = _load("cliproxyapi_update_p11d", "scripts/updaters/cliproxyapi_update.py")
vsl = _load("verify_skills_lock_p11d", "scripts/verify-skills-lock.py")


class _Res:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


# ============================================================
# 1 - the health gate rejects the message it used to pass
# ============================================================
@pytest.mark.parametrize("out", [
    "expected HTTP 200, got HTTP 502",
    "expected HTTP 200, got HTTP 000 (connection refused)",
    "want HTTP 200 / received HTTP 503",
])
def test_the_canonical_failure_message_is_not_healthy(out, monkeypatch):
    monkeypatch.setattr(cpx.subprocess, "run", lambda *a, **k: _Res(out))
    assert cpx._health_ok() is False, out


@pytest.mark.parametrize("out", ["HTTP 200 OK", "probe: HTTP 200", "status HTTP 200\n"])
def test_a_real_two_hundred_is_healthy(out, monkeypatch):
    monkeypatch.setattr(cpx.subprocess, "run", lambda *a, **k: _Res(out))
    assert cpx._health_ok() is True, out


def test_a_nonzero_probe_exit_is_not_healthy(monkeypatch):
    monkeypatch.setattr(cpx.subprocess, "run",
                        lambda *a, **k: _Res("HTTP 200", "", returncode=1))
    assert cpx._health_ok() is False


def test_the_probe_runs_without_a_shell(monkeypatch):
    """The path was interpolated unquoted into `bash -c`, so a home directory
    with a space broke the probe and every update ended in a spurious
    rollback."""
    seen = {}
    monkeypatch.setattr(cpx.subprocess, "run",
                        lambda cmd, **k: seen.update(cmd=cmd) or _Res("HTTP 200"))
    cpx._health_ok()
    assert seen["cmd"][0] != "bash", seen["cmd"]
    assert isinstance(seen["cmd"], list) and len(seen["cmd"]) == 2


# ============================================================
# 2 - both sides of the version comparison are normalised
# ============================================================
@pytest.mark.parametrize("raw,expect", [
    ("v7.2.104", "7.2.104"),
    ("7.2.104", "7.2.104"),
    ("Version: 7.2.104", "7.2.104"),
    ("", ""),
    ("nightly", ""),
])
def test_a_version_string_normalises_to_bare_digits(raw, expect):
    assert cpx._normalise_version(raw) == expect


def test_versions_order_numerically_not_lexically():
    """"7.2.92" > "7.2.104" as strings; as versions it is the other way."""
    assert cpx._version_tuple("7.2.92") < cpx._version_tuple("7.2.104")


def test_a_tagged_latest_matches_an_untagged_current(monkeypatch, capsys):
    monkeypatch.setattr(cpx.update_sources, "latest_version", lambda spec: "v7.2.104")
    monkeypatch.setattr(cpx, "_current_version", lambda: "7.2.104")
    assert cpx.main() == 0
    assert "already" in capsys.readouterr().out


def test_a_downgrade_is_refused(monkeypatch, capsys):
    monkeypatch.setattr(cpx.update_sources, "latest_version", lambda spec: "v7.2.92")
    monkeypatch.setattr(cpx, "_current_version", lambda: "7.2.104")
    assert cpx.main() == 2
    assert "refusing a downgrade" in capsys.readouterr().out


# ============================================================
# 3 - the release member is THE binary
# ============================================================
class _Member:
    def __init__(self, name, isfile=True):
        self.name = name
        self._isfile = isfile

    def isfile(self):
        return self._isfile


def test_an_archive_with_no_binary_refuses_cleanly():
    """A bare `next(...)` outside the try raised StopIteration as a traceback."""
    member, why = cpx.select_binary_member([_Member("README.md")])
    assert member is None
    assert "no `cli-proxy-api` file" in why


def test_a_lookalike_member_is_not_the_binary():
    """`endswith` matched `docs/old-cli-proxy-api` and `not-cli-proxy-api`, and
    `next()` took whichever came first in ARCHIVE order."""
    members = [_Member("assets/old-cli-proxy-api"),
               _Member("not-cli-proxy-api"),
               _Member("bin/cli-proxy-api")]
    member, why = cpx.select_binary_member(members)
    assert member is not None, why
    assert member.name == "bin/cli-proxy-api"


def test_a_directory_of_that_name_is_not_the_binary():
    member, why = cpx.select_binary_member([_Member("cli-proxy-api", isfile=False)])
    assert member is None
    assert "no `cli-proxy-api` file" in why


def test_an_ambiguous_archive_refuses():
    members = [_Member("a/cli-proxy-api"), _Member("b/cli-proxy-api")]
    member, why = cpx.select_binary_member(members)
    assert member is None
    assert "ambiguous swap" in why


# ============================================================
# 4 - the swap is a rename, beside the target
# ============================================================
def test_the_swap_uses_os_replace_not_a_cross_filesystem_move():
    """`shutil.move` from the tempdir degrades to copy-and-delete across
    filesystems (TMPDIR is commonly tmpfs), so a kill mid-copy left a PARTIAL
    binary with the service stopped -- and no exception for `_restore` to
    catch."""
    src = (ROOT / "scripts" / "updaters" / "cliproxyapi_update.py").read_text(
        encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "shutil.move(str(newbin), str(BIN))" not in code, code
    assert "os.replace(side, BIN)" in code


def test_an_endless_body_is_refused_rather_than_filling_the_disk(
        tmp_path, monkeypatch):
    """`copyfileobj` streamed without a limit, and the URL is API-response data
    -- the staging dir is frequently a small tmpfs."""
    monkeypatch.setattr(cpx, "MAX_DOWNLOAD_BYTES", 4096)

    class _Endless:
        def read(self, n):
            return b"x" * n

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cpx.urllib.request, "urlopen", lambda *a, **k: _Endless())
    with pytest.raises(ValueError, match="exceeded"):
        cpx._download("https://example.com/x.tar.gz", tmp_path / "out")


def test_a_normal_body_downloads_whole(tmp_path, monkeypatch):
    payload = b"hello world" * 10

    class _Body:
        def __init__(self):
            self.done = False

        def read(self, n):
            if self.done:
                return b""
            self.done = True
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cpx.urllib.request, "urlopen", lambda *a, **k: _Body())
    dest = tmp_path / "out"
    cpx._download("https://example.com/x.tar.gz", dest)
    assert dest.read_bytes() == payload


def test_a_non_https_url_is_still_refused(tmp_path):
    with pytest.raises(ValueError, match="non-https"):
        cpx._download("file:///etc/passwd", tmp_path / "out")


def test_the_exit_contract_documents_every_code_the_code_returns():
    """The codes are DERIVED from the source, not a hand-kept list of four.

    This iterated the literals `("0", "1", "2", "3")`. The test's name claims
    it documents "every code the code returns", and four literals cannot do
    that in either direction: a new `return 4` path is undocumented and the
    test stays green, and a dropped code 3 leaves a stale docstring entry the
    test insists on. Both are the drift this exists to catch.

    The codes now come from the integer returns of the module's own top-level
    functions, so the docstring and the code cannot part company silently.
    """
    path = ROOT / "scripts" / "updaters" / "cliproxyapi_update.py"
    src = path.read_text(encoding="utf-8")
    doc = src.split('"""', 2)[1]

    returned: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, int) \
                and not isinstance(node.value.value, bool):
            returned.add(str(node.value.value))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "exit" and node.args \
                and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, int) \
                and not isinstance(node.args[0].value, bool):
            returned.add(str(node.args[0].value))

    assert returned, f"no integer exit paths found in {path.name}; the scan broke"
    undocumented = sorted(c for c in returned if f"  {c}  " not in doc)
    assert not undocumented, (
        f"{path.name} returns exit code(s) {undocumented} that its 'Exit codes:' "
        f"block does not document")
    assert "non-zero on rollback." not in doc


# ============================================================
# 5 - an empty corpus is not a pass
# ============================================================
def test_a_missing_corpus_exits_2_not_0(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate-crm-schema.py"),
         "--dir", str(tmp_path / "nope")],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "empty corpus" in proc.stderr
    assert "All 0 records pass" not in proc.stdout


@pytest.mark.skipif(
    data_root_is_demo(),
    reason="no operator CRM corpus: without a private data overlay the data root "
           "falls back to the bundled examples/ inside the engine clone, whose one "
           "fictional demo contact is deliberately a minimal stub and carries "
           "neither `type` nor `last_touch`. This test measures that the "
           "empty-corpus refusal above did not also break validation of a "
           "populated corpus, and there is no populated corpus here to measure.")
def test_a_real_corpus_still_passes():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate-crm-schema.py")],
        capture_output=True, text=True, cwd=str(ROOT), timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "records pass schema" in proc.stdout


@pytest.mark.parametrize("bad", ["../../tmp/planted", "a/b", "../x", ".hidden"])
def test_a_traversing_contact_is_refused(bad):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate-crm-schema.py"),
         "--contact", bad],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "bare slug" in proc.stderr


# ============================================================
# 6 - the lock verifier sees a symlink as a symlink
# ============================================================
def test_a_symlink_substitution_changes_the_tree_hash(tmp_path):
    """`is_file()` FOLLOWS links, so a vendored file swapped for a link to an
    identical-content file elsewhere hashed the same -- undetected, which is the
    one thing this verifier exists to catch."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.txt").write_text("same content\n", encoding="utf-8")
    before = vsl._tree_hash(tree)

    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("same content\n", encoding="utf-8")
    (tree / "a.txt").unlink()
    (tree / "a.txt").symlink_to(elsewhere)
    after = vsl._tree_hash(tree)
    assert before != after, "a symlink substitution hashed identically"


def test_a_broken_symlink_is_recorded_rather_than_skipped(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "dangling").symlink_to(tmp_path / "never-existed")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert vsl._tree_hash(tree) != vsl._tree_hash(empty)


def test_an_unchanged_tree_hashes_the_same_twice(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.txt").write_text("x\n", encoding="utf-8")
    assert vsl._tree_hash(tree) == vsl._tree_hash(tree)


# ============================================================
# 7 - skillPath cannot pin the whole repository
# ============================================================
def test_a_parentless_skillpath_is_refused(tmp_path):
    """It made tree_dir the WORKSPACE ROOT, and `--relock` would then pin a hash
    of the entire repo -- changing every commit, failing verification forever."""
    assert vsl._vendored_dir(tmp_path, {"skillPath": "SKILL.md"}) is None


@pytest.mark.parametrize("bad", ["/etc/passwd", "../../outside/SKILL.md"])
def test_an_escaping_skillpath_is_refused(tmp_path, bad):
    assert vsl._vendored_dir(tmp_path, {"skillPath": bad}) is None


def test_an_ordinary_skillpath_resolves(tmp_path):
    got = vsl._vendored_dir(tmp_path, {"skillPath": ".claude/skills/x/SKILL.md"})
    assert got == (tmp_path / ".claude" / "skills" / "x").resolve()


# ============================================================
# 8 - a wrong-shaped lock fails cleanly
# ============================================================
@pytest.mark.parametrize("payload", ["[]", '"a string"', "42"])
def test_a_lock_that_is_not_an_object_fails_without_a_traceback(
        payload, tmp_path, monkeypatch, capsys):
    lock = tmp_path / "skills-lock.json"
    lock.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(vsl, "LOCK_PATH", lock)
    assert vsl.verify(relock=False, quiet=True) == 1
    assert "expected an object" in capsys.readouterr().out


def test_a_skills_value_that_is_a_list_fails_cleanly(tmp_path, monkeypatch, capsys):
    lock = tmp_path / "skills-lock.json"
    lock.write_text(json.dumps({"recipe": vsl.RECIPE, "skills": []}),
                    encoding="utf-8")
    monkeypatch.setattr(vsl, "LOCK_PATH", lock)
    assert vsl.verify(relock=False, quiet=True) == 1
    assert "expected an object" in capsys.readouterr().out


def test_a_non_dict_entry_fails_cleanly(tmp_path, monkeypatch, capsys):
    lock = tmp_path / "skills-lock.json"
    lock.write_text(json.dumps({"recipe": vsl.RECIPE, "skills": {"x": "oops"}}),
                    encoding="utf-8")
    monkeypatch.setattr(vsl, "LOCK_PATH", lock)
    assert vsl.verify(relock=False, quiet=True) == 1
    assert "expected an object" in capsys.readouterr().out


# ============================================================
# 9 - --relock keeps the issues it found
# ============================================================
def test_relock_does_not_discard_unverifiable_entries():
    src = (ROOT / "scripts" / "verify-skills-lock.py").read_text(encoding="utf-8")
    block = src.split("if relock and changed:", 1)[1].split("\n    if issues:", 1)[0]
    assert "if issues:" in block, block
    assert "return 1" in block, block


def test_the_live_lock_still_verifies():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify-skills-lock.py")],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
