"""The four sandbox controls, asserted as refusals rather than as intentions.

These began as five commands run by hand in a shell on 2026-08-13. A control
proven once, by a human, in a terminal, is a control nobody will notice losing:
the whole value of `/census` executing model-written Python rests on this box,
so the proof has to run on every suite.

Two disciplines this file holds to, both learned the same day.

**Assert the SPECIFIC refusal.** A test satisfied by "some failure" passes
against a sandbox that never started, which is exactly the defect `/scrutinize`
found in the reproduction harness hours earlier: any non-zero exit read as
evidence. So each control here asserts the symptom of its own failure mode -
`Errno 101` for the network, a read-only filesystem for the corpus - and the
corpus test additionally verifies the bytes on disk did not move, because a
refusal message is a claim and unchanged bytes are the proof.

**Refuse before the process exists.** The air-gap and missing-bwrap tests assert
that nothing ran at all, not that a run failed. A guard that fires after the
traversal has read the corpus is a report.
"""
from __future__ import annotations

import json
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import sandbox
from scripts.utils.sandbox import run_sandboxed

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "census_corpus"

needs_bwrap = pytest.mark.skipif(
    shutil.which("bwrap") is None,
    reason="bubblewrap absent; the refusal path is covered separately below")


@pytest.fixture
def out(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    return d


def program(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "traverse.py"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def answer(out_dir: Path) -> dict:
    return json.loads((out_dir / "answer.json").read_text(encoding="utf-8"))


def snapshot(root: Path) -> dict[Path, bytes]:
    """Every file under ``root``, mapped to its bytes. Fails, never skips.

    The two callers below take one of these before the box runs and one after,
    and assert the two are equal to prove the corpus was mounted read-only. That
    is a CHECKSUM over a whole tree, so a file dropped from either side is not a
    narrowed scan - it is the wrong answer. Drop it from `before` and a mutation
    to that file is invisible; drop it from `after` and a file the box DELETED
    reads as a corpus that never changed. Either way the control reports clean
    over the breach it exists to catch.

    So a read that misses is retried once, in case it landed in a rewrite
    window, and then fails naming the file. `read_sources` is the right answer
    for a sweep hunting offenders and the wrong one here, and it reads text
    besides; these comparisons are over bytes.
    """
    snap: dict[Path, bytes] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        try:
            snap[p] = p.read_bytes()
            continue
        except FileNotFoundError:
            pass
        try:
            snap[p] = p.read_bytes()
        except FileNotFoundError:
            pytest.fail(
                f"{p} vanished between the corpus walk and the read. This "
                f"comparison cannot drop a file and still say the corpus is "
                f"unchanged, so it stops here."
            )
    return snap


# ============================================================
# Control 1 - no network inside the box
# ============================================================

@needs_bwrap
def test_the_box_cannot_reach_the_network(tmp_path, out):
    prog = program(tmp_path, '''
        import json, pathlib, socket
        results = {}
        for label, target in (("ip", ("1.1.1.1", 443)), ("proxy", ("127.0.0.1", 8317))):
            try:
                socket.create_connection(target, timeout=4)
                results[label] = "REACHED"
            except OSError as exc:
                results[label] = f"refused: [Errno {exc.errno}] {exc}"
        pathlib.Path("/out/answer.json").write_text(json.dumps(results))
    ''')
    run_sandboxed(program=prog, corpus_paths=[FIXTURE], out_dir=out, timeout_s=60)
    got = answer(out)
    assert "Errno 101" in got["ip"], got["ip"]
    # The local proxy matters on its own: the design forbids a model call from
    # inside, and an empty netns is what makes that structural rather than
    # a promise in a docstring.
    assert got["proxy"].startswith("refused:"), got["proxy"]


# ============================================================
# Control 2 - no secrets in the box
# ============================================================

@needs_bwrap
def test_nothing_crosses_from_the_parent_environment(tmp_path, out, monkeypatch):
    monkeypatch.setenv("CENSUS_CANARY_SECRET", "must-not-cross-the-boundary")
    prog = program(tmp_path, '''
        import json, os, pathlib
        pathlib.Path("/out/answer.json").write_text(json.dumps(sorted(os.environ)))
    ''')
    run_sandboxed(program=prog, corpus_paths=[FIXTURE], out_dir=out, timeout_s=60)
    keys = answer(out)
    assert "CENSUS_CANARY_SECRET" not in keys, keys
    # LC_CTYPE is created by CPython at startup (PEP 538 C-locale coercion), not
    # inherited: bare `bwrap ... /usr/bin/env` hands over PATH and PWD only.
    assert set(keys) <= {"PATH", "PWD", "LC_CTYPE"}, keys


@needs_bwrap
def test_the_engine_dotenv_does_not_exist_inside(tmp_path, out):
    prog = program(tmp_path, '''
        import json, pathlib
        found = []
        for candidate in ("/data/.env", "/.env", "/data/census_corpus/.env"):
            if pathlib.Path(candidate).exists():
                found.append(candidate)
        pathlib.Path("/out/answer.json").write_text(json.dumps(found))
    ''')
    run_sandboxed(program=prog, corpus_paths=[FIXTURE], out_dir=out, timeout_s=60)
    assert answer(out) == []


# ============================================================
# Control 3 - the corpus is read-only
# ============================================================

@needs_bwrap
def test_the_corpus_cannot_be_written_and_does_not_change(tmp_path, out):
    prog = program(tmp_path, '''
        import json, pathlib
        results = {}
        target = sorted(pathlib.Path("/data").rglob("*.md"))[0]
        try:
            target.write_text("mutated")
            results["overwrite"] = "WROTE"
        except OSError as exc:
            results["overwrite"] = f"refused: {exc.strerror}"
        try:
            (target.parent / "planted.md").write_text("planted")
            results["create"] = "WROTE"
        except OSError as exc:
            results["create"] = f"refused: {exc.strerror}"
        pathlib.Path("/out/answer.json").write_text(json.dumps(results))
    ''')
    before = snapshot(FIXTURE)
    run_sandboxed(program=prog, corpus_paths=[FIXTURE], out_dir=out, timeout_s=60)
    got = answer(out)
    assert "Read-only file system" in got["overwrite"], got["overwrite"]
    assert "Read-only file system" in got["create"], got["create"]
    after = snapshot(FIXTURE)
    assert after == before, "the corpus changed despite the read-only mount"


@needs_bwrap
def test_the_output_directory_is_the_only_writable_path(tmp_path, out):
    prog = program(tmp_path, '''
        import json, pathlib
        pathlib.Path("/out/answer.json").write_text(json.dumps({"wrote": True}))
    ''')
    result = run_sandboxed(program=prog, corpus_paths=[FIXTURE], out_dir=out,
                           timeout_s=60)
    assert result.ok, (result.refused, result.stderr)
    assert answer(out) == {"wrote": True}


# ============================================================
# Refusals that happen BEFORE any process starts
# ============================================================

def test_an_air_gapped_corpus_path_is_refused_without_running(tmp_path):
    """The CEO-private thread branch is never mounted, whatever was asked for."""
    private = tmp_path / "threads" / "personal"
    private.mkdir(parents=True)
    (private / "note.md").write_text("private", encoding="utf-8")
    prog = program(tmp_path, "raise SystemExit('this program must never run')\n")
    out_dir = tmp_path / "out"

    result = run_sandboxed(program=prog, corpus_paths=[private], out_dir=out_dir,
                           timeout_s=60)
    assert result.refused is not None
    assert "air-gapped" in result.refused
    assert result.exit_code is None, "the program ran despite the air-gap refusal"
    assert not (out_dir / "answer.json").exists()


def test_a_secure_prefix_is_refused(tmp_path, monkeypatch):
    """`_secure/` is a prefix rule, so it is checked in root-relative form.

    The reason is asserted, not merely the refusal. On the first run of this
    test the path did not exist, so it refused with "corpus path does not
    exist" and passed anyway - a test satisfied by the wrong refusal proves
    nothing about the rule it is named after, and the air-gap check now runs
    before the existence check partly because of it.
    """
    root = Path(__file__).resolve().parent.parent
    monkeypatch.setattr(sandbox, "_known_roots", lambda: [root])
    vault = root / "_secure" / "vault"
    result = run_sandboxed(program=Path(__file__), corpus_paths=[vault],
                           out_dir=tmp_path / "out", timeout_s=5)
    assert result.refused is not None
    assert "air-gapped" in result.refused, result.refused
    assert result.exit_code is None


def test_the_air_gap_decides_before_the_filesystem_is_consulted(tmp_path):
    """An air-gapped path that does not exist still refuses AS air-gapped."""
    missing = tmp_path / "threads" / "personal" / "not-there"
    result = run_sandboxed(program=Path(__file__), corpus_paths=[missing],
                           out_dir=tmp_path / "out", timeout_s=5)
    assert "air-gapped" in (result.refused or ""), result.refused


@needs_bwrap
def test_an_air_gapped_child_of_a_mounted_scope_is_blanked(tmp_path, out):
    """The hole the path-level check leaves open, closed by a tmpfs overlay.

    Found on the first live run, 2026-08-13. The air-gap check looked at the
    path the caller NAMED, and the CEO-private thread branch is a CHILD of the
    threads directory: `--corpus <data>/threads` passes the check and then
    mounts the private branch read-only, where a traversal reads every note in
    it. Refusing the whole scope would have been safe and useless; laying a
    tmpfs over the denied child after the read-only bind makes the branch not
    denied but ABSENT.
    """
    corpus = tmp_path / "threads"
    (corpus / "business").mkdir(parents=True)
    (corpus / "business" / "deal.md").write_text("public", encoding="utf-8")
    (corpus / "personal").mkdir()
    (corpus / "personal" / "medical.md").write_text("PRIVATE", encoding="utf-8")

    prog = program(tmp_path, '''
        import json, pathlib
        seen = sorted(str(p) for p in pathlib.Path("/data").rglob("*.md"))
        personal = pathlib.Path("/data/threads/personal")
        pathlib.Path("/out/answer.json").write_text(json.dumps({
            "seen": seen,
            "personal_exists": personal.exists(),
            "personal_contents": sorted(str(p) for p in personal.rglob("*")),
        }))
    ''')
    run_sandboxed(program=prog, corpus_paths=[corpus], out_dir=out, timeout_s=60,
                  mount_names={corpus: "threads"})
    got = answer(out)
    assert got["seen"] == ["/data/threads/business/deal.md"], got["seen"]
    assert got["personal_contents"] == [], got["personal_contents"]
    assert not any("PRIVATE" in s for s in got["seen"])


def test_denied_descendants_finds_the_private_branch(tmp_path):
    corpus = tmp_path / "threads"
    (corpus / "business").mkdir(parents=True)
    (corpus / "personal").mkdir()
    found = [p.name for p in sandbox.denied_descendants(corpus)]
    assert found == ["personal"], found


def test_denied_descendants_walks_deeper_than_one_level(tmp_path):
    """The walk is a stack, and nothing proved it ever pushed onto it.

    Every case above puts the denied directory ONE level under the corpus path,
    which the first pass of the loop finds without ever descending. MEASURED
    2026-09-01: replacing `stack.append(child)` with `pass` left this file and
    `tests/test_census_engine.py` green over 48 tests, so the recursion that
    makes this a walk rather than a listing was carrying no coverage at all.

    A one-level-only check is not a smaller guard, it is an open one:
    `--corpus <data>/knowledge` mounts everything under it read-only, and a
    `personal` branch two directories down is then read by the traversal exactly
    as the thread branch was on 2026-08-13 -- the same defect this function was
    written for, one directory deeper.

    The tmpfs overlay is asserted on the command line as well, because finding
    the directory and BLANKING it are two different things, and only the second
    one makes the branch absent inside the box.
    """
    corpus = tmp_path / "knowledge"
    (corpus / "shared" / "notes").mkdir(parents=True)
    deep = corpus / "shared" / "notes" / "personal"
    deep.mkdir()
    (deep / "medical.md").write_text("PRIVATE", encoding="utf-8")

    found = sandbox.denied_descendants(corpus)
    assert [p.relative_to(corpus).as_posix() for p in found] == \
        ["shared/notes/personal"], found

    program = tmp_path / "t.py"
    program.write_text("pass\n", encoding="utf-8")
    argv = sandbox.build_argv(program=program, corpus_paths=[corpus],
                              out_dir=tmp_path / "out",
                              mount_names={corpus: "knowledge"})
    assert "--tmpfs" in argv
    assert "/data/knowledge/shared/notes/personal" in argv, argv


def test_the_writable_output_may_not_CONTAIN_a_corpus_path(tmp_path):
    """The other nesting order, and it had no test at all.

    Its sibling `test_the_writable_output_may_not_sit_inside_the_corpus` covers
    `out_dir` UNDER the corpus. The reverse is the one the module's own comment
    records as MEASURED with bwrap 0.9.0 on 2026-08-26: an `out_dir` that
    contains a corpus path is bound read-write at `/out`, the corpus then sits
    under it as a writable subtree, and a traversal writing `/out/<name>/x.md`
    reaches the host corpus without ever touching the read-only mount. The box
    reported exit 0 and the host file had been overwritten.

    MEASURED 2026-09-01: switching that refusal off left this file and
    `tests/test_census_engine.py` green over 48 tests. A control with a
    measured breach behind it and no test is a control that gets deleted by the
    next person tidying a duplicated-looking branch.

    The refusal is asserted together with its SIDE EFFECT: no process, and the
    corpus bytes unchanged. A message that says "refused" while the write
    happened is the failure this whole file is written against.
    """
    out = tmp_path / "out"
    corpus = out / "corpus"
    corpus.mkdir(parents=True)
    (corpus / "note.md").write_text("original", encoding="utf-8")
    before = snapshot(corpus)

    prog = program(tmp_path, '''
        import pathlib
        for target in sorted(pathlib.Path("/out").rglob("*.md")):
            target.write_text("mutated")
        (pathlib.Path("/out") / "corpus" / "planted.md").write_text("planted")
    ''')

    result = run_sandboxed(program=prog, corpus_paths=[corpus], out_dir=out,
                           timeout_s=60)

    assert result.refused is not None
    assert "contains the corpus path" in result.refused, result.refused
    assert result.exit_code is None, "the traversal ran despite the refusal"
    after = snapshot(corpus)
    assert after == before, "the corpus changed behind a refusal"
    assert not (corpus / "planted.md").exists()


def test_a_missing_bwrap_refuses_and_never_falls_back(tmp_path, out, monkeypatch):
    """The one failure mode a soft degradation would quietly convert into the
    configuration this whole design exists to refuse."""
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)
    prog = program(tmp_path, "open('/out/answer.json', 'w').write('{}')\n")
    result = run_sandboxed(program=prog, corpus_paths=[FIXTURE], out_dir=out,
                           timeout_s=60)
    assert result.refused is not None
    assert "does not run without its sandbox" in result.refused
    assert result.exit_code is None
    assert not (out / "answer.json").exists(), "it ran unsandboxed"


def test_a_missing_corpus_path_is_refused(tmp_path, out):
    prog = program(tmp_path, "pass\n")
    result = run_sandboxed(program=prog, corpus_paths=[tmp_path / "nope"],
                           out_dir=out, timeout_s=60)
    assert result.refused is not None
    assert "does not exist" in result.refused


def test_an_empty_corpus_list_is_refused(tmp_path, out):
    prog = program(tmp_path, "pass\n")
    result = run_sandboxed(program=prog, corpus_paths=[], out_dir=out, timeout_s=60)
    assert result.refused is not None


# ============================================================
# A run that does not finish is not an answer
# ============================================================

@needs_bwrap
def test_a_program_that_never_exits_is_killed_and_reported_as_refused(tmp_path, out):
    prog = program(tmp_path, '''
        import time
        time.sleep(600)
    ''')
    result = run_sandboxed(program=prog, corpus_paths=[FIXTURE], out_dir=out,
                           timeout_s=2)
    assert result.timed_out
    assert result.refused is not None
    assert result.exit_code is None
    assert not result.ok


# ============================================================
# Mount construction
# ============================================================

def test_colliding_basenames_get_distinct_mounts(tmp_path):
    a = tmp_path / "one" / "contacts"
    b = tmp_path / "two" / "contacts"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    names = sandbox._mount_names([a, b])
    assert len(set(names.values())) == 2, names


def test_the_argv_never_reaches_a_shell(tmp_path):
    """Every element is a separate argument; `shell=True` is never used."""
    prog = program(tmp_path, "pass\n")
    argv = sandbox.build_argv(program=prog, corpus_paths=[FIXTURE],
                              out_dir=tmp_path, mount_names={FIXTURE: "c"})
    assert argv[0] == "bwrap"
    assert "--unshare-all" in argv
    assert "--clearenv" in argv
    assert "--die-with-parent" in argv
    assert argv[-2:] == ["/usr/bin/python3", "/traverse.py"]
    assert "--ro-bind" in argv and "--bind" in argv
    source = Path(sandbox.__file__).read_text(encoding="utf-8")
    assert "shell=True" not in source


def test_the_writable_output_may_not_sit_inside_the_corpus(tmp_path):
    """The one writable mount is a control only while it lies outside the corpus.

    An `out_dir` under a corpus path re-binds that subtree read-write, defeating
    the read-only mount on the host - voiding condition #2 of the carve-out rule.
    `census.py` passes a temp directory and was never at risk; the gap was in the
    reusable helper, which a second caller would inherit.
    """
    corpus = tmp_path / "corpus"
    (corpus / "sub").mkdir(parents=True)
    (corpus / "a.md").write_text("x", encoding="utf-8")
    program = tmp_path / "t.py"
    program.write_text("pass\n", encoding="utf-8")

    result = sandbox.run_sandboxed(program=program, corpus_paths=[corpus],
                                   out_dir=corpus / "sub", timeout_s=10)
    assert result.refused is not None
    assert "inside the corpus" in result.refused


@needs_bwrap
def test_an_output_outside_the_corpus_is_accepted(tmp_path):
    """The positive control for the rule above: the placement check must refuse a
    bad `out_dir` without refusing every `out_dir`.

    Split out of that test on 2026-08-14. Held together, the pair could only run
    where bubblewrap is installed, because this half is a real run - so the
    refusal half, which asserts a decision made BEFORE any process exists, was
    silently unavailable exactly on the hosts that had no sandbox. That is where
    the check-ordering defect this file exists to catch was hiding.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("x", encoding="utf-8")
    program = tmp_path / "t.py"
    program.write_text("pass\n", encoding="utf-8")

    outside = sandbox.run_sandboxed(program=program, corpus_paths=[corpus],
                                    out_dir=tmp_path / "out", timeout_s=30)
    assert outside.refused is None, outside.refused


def test_the_box_gets_its_own_session_so_it_cannot_reach_the_tty(tmp_path):
    """Without --new-session the box inherits the controlling terminal.

    On a host with `dev.tty.legacy_tiocsti=1` that is keystroke injection out of
    the sandbox. Measured 0 on this kernel; the engine ships to a fleet, so the
    flag is asserted on the command line rather than on this machine's setting.
    """
    program = tmp_path / "t.py"
    program.write_text("pass\n", encoding="utf-8")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    argv = sandbox.build_argv(program=program, corpus_paths=[corpus],
                              out_dir=tmp_path / "out", mount_names={corpus: "corpus"})
    assert "--new-session" in argv
