"""A brief written by another Claude session authorised a release.

`check_release_gate` decides that the operator asked for a commit or a push by
reading the most recent transcript record with `promptSource: "typed"` and
matching it against a word list. The premise was that such a record is a human
at a keyboard.

IT IS NOT. A prompt injected into a session by ANOTHER Claude session through
`herdr agent prompt` lands as exactly that record. MEASURED 2026-09-05 on
`f4d56072-...jsonl`, which holds both kinds in one file: a brief delivered from
HELM at line 11, and three prompts the operator typed into that same yard by
hand at lines 839, 1157 and 1285. Every field identical except `parentUuid`,
`promptId`, `uuid` and `timestamp`. `promptSource` is `"typed"` for all four and
`sessionKind` is `"bg"` for all four. No field says who produced the text.

It had already fired: every brief HELM wrote on 2026-09-04 ended with "COMMIT
when green", so every yard it opened carried a forged authorisation nobody
intended.

PRE-FIX MEASUREMENT, taken 2026-09-06 by restoring `.claude/hooks/_dispatch.py`
from HEAD (24c24e8) and running this file against it: **15 failed, 11 passed**.
The 11 that passed are the positive-direction anchors, so the failing half is
the new behaviour and not a broken fixture. The centre of it:

    check_release_gate({"tool_name": "Bash",
                        "tool_input": {"command": "git commit -m 'done'"}, ...})
    -> None                      (the marked brief AUTHORISED the commit)
    prompt_authorises(THE_BRIEF, "commit")  -> True

After the fix the gate blocks with a reason naming the marker, and nothing is
appended to the authorised-release log.

WHAT THESE TESTS ESTABLISH, AND WHAT THEY DO NOT. They establish that a prompt
CARRYING the marker never authorises a release, that the operator's own short
words still do, and that `scripts/herdr-brief.py` marks what it sends, refuses a
text that already carries the marker, and refuses to run from a YARD at all.
They do NOT establish that a prompt WITHOUT the marker was typed by a human;
nothing in the record can establish that, and the mechanism is fail-open by
construction. No brief is delivered anywhere by this file: `herdr` is a stub on
PATH whose argv is recorded, and the one case that reaches the guard's refusal
runs inside a throwaway git worktree built under `tmp_path`.

Run: .venv/bin/python -m pytest
     tests/test_a_brief_from_another_session_that_authorised_a_release.py
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SENDER_REL = "scripts/herdr-brief.py"
GATE_REL = ".claude/hooks/_dispatch.py"

# Spelled here on purpose. If the test read the constant from the source it
# would only prove the source agrees with itself; this is the anchor that goes
# red when the literal changes on either side of the seam.
EXPECTED_MARKER = "X-HEADING-BRIEF: machine-to-machine, not an operator authorisation"

# Floors, outside every loop below. MEASURED 2026-09-06 on this tree:
# `_PUSH_WORDS` holds 11 entries and `_COMMIT_WORDS` holds 4. A parametrisation
# built from an empty tuple passes without asserting anything.
MIN_PUSH_WORDS = 11
MIN_COMMIT_WORDS = 4

# The support files a throwaway checkout needs before `scripts/herdr-brief.py`
# will import. `_dispatch.py` reaches for `scripts.utils.pathnorm` at module
# scope, and the sender loads `_dispatch.py` for the marker.
_CLONE_FILES = (
    SENDER_REL,
    GATE_REL,
    "scripts/__init__.py",
    "scripts/utils/__init__.py",
    "scripts/utils/checkpoint_paths.py",
    "scripts/utils/clone_guard.py",
    "scripts/utils/colors.py",
    "scripts/utils/pathnorm.py",
)

# `workspace list` as well as `agent prompt`, since 2026-09-06: the sender maps
# the pane to a checkout before it sends anything, and verifies afterwards that
# the text reached THAT checkout's transcript. A stub that only recorded argv
# would exercise a sender that no longer exists.
#
# Writing the payload into the transcript is the stub standing in for a correct
# delivery. The misdelivery direction has its own file,
# `tests/test_a_brief_that_herdr_said_it_delivered_somewhere_else.py`.
_HERDR_STUB = """\
#!/usr/bin/env bash
if [ "$1" = "workspace" ]; then
  printf '{"result":{"workspaces":[{"workspace_id":"w37","worktree":{"checkout_path":"%s"}}]}}\\n' "$STUB_CHECKOUT"
  exit 0
fi
for a in "$@"; do printf '%s\\0' "$a" >> "$STUB_ARGV"; done
if [ -n "$STUB_TRANSCRIPT" ] && [ "${HERDR_EXIT:-0}" = "0" ]; then
  printf '%s' "$4" >> "$STUB_TRANSCRIPT"
fi
printf '{"type":"agent_prompted"}\\n'
exit "${HERDR_EXIT:-0}"
"""


def _load_gate():
    spec = importlib.util.spec_from_file_location(
        "dispatch_brief_marker_probe", ROOT / GATE_REL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dispatch_brief_marker_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


D = _load_gate()


# ==========================================================================
# Fixtures: a transcript in the real shape, and a throwaway checkout
# ==========================================================================

def _transcript(tmp_path: Path, text: str) -> Path:
    """One turn, recorded the way the harness records it.

    BOTH records, because `_reconcile` needs both: the capped `last-prompt` the
    model cannot forge, and the `promptSource: "typed"` record carrying the full
    string. The cap is 200 characters plus an ellipsis, reproduced here, which
    is what makes a long brief exercise the same path a real one does.
    """
    flat = " ".join(text.split())
    capped = flat if len(flat) <= 200 else flat[:200] + "…"
    lines = [
        json.dumps({"type": "user",
                    "message": {"content": "<task-notification>done</task-notification>"}}),
        json.dumps({"type": "last-prompt", "lastPrompt": capped}),
        json.dumps({"type": "user", "promptSource": "typed",
                    "sessionKind": "bg", "userType": "external",
                    "origin": {"kind": "human"},
                    "message": {"content": text}}),
        json.dumps({"type": "assistant", "message": {"content": "ok"}}),
    ]
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _payload(command: str, transcript: Path) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command},
            "transcript_path": str(transcript)}


def _gate(tmp_path, monkeypatch, command: str, prompt: str):
    """Drive the real entry point, with the audit log redirected.

    The log is a real file under `.claude/state/release` in the live checkout,
    and a test that authorises a release must not append to the operator's audit
    trail. Redirecting it also means a run that WOULD have logged is visible.
    """
    monkeypatch.setattr(D, "_RELEASE_STATE_DIR", tmp_path / "release")
    return D.check_release_gate(_payload(command, _transcript(tmp_path, prompt)))


THE_BRIEF = (
    f"{EXPECTED_MARKER}\n"
    "\n"
    "TASK FROM HELM. Read this whole brief before touching anything.\n"
    "\n"
    "You are in the yard `yard-example`, branch of the same name. Fix the "
    "defect described below, run the gates, and COMMIT when green.\n"
)

# The same brief with the other half of the vocabulary in it. Without this the
# `push` cases below would pass pre-fix for the wrong reason: `THE_BRIEF` says
# "COMMIT when green" and carries no push word, so the old gate refused a push
# from it correctly and the case proved nothing.
THE_PUSH_BRIEF = THE_BRIEF.replace(
    "COMMIT when green", "push the branch when green")


@pytest.fixture(scope="module")
def helm(tmp_path_factory) -> dict:
    """A throwaway MAIN clone holding the sender and what it imports.

    `.git` a DIRECTORY is what makes this HELM rather than a YARD, so `git init`
    is load-bearing: without it every positive case below would pass for the
    wrong reason, refused by the clone guard instead of exercised.
    """
    base = tmp_path_factory.mktemp("helm")
    clone = base / "clone"
    _populate(clone)
    subprocess.run(["git", "-C", str(clone), "init", "-q"], check=True,
                   capture_output=True)

    binstub = base / "bin"
    binstub.mkdir()
    stub = binstub / "herdr"
    stub.write_text(_HERDR_STUB, encoding="utf-8")
    stub.chmod(0o755)
    return {"clone": clone, "bin": binstub, "argv": base / "argv.bin",
            **_delivery_fixture(base, clone)}


@pytest.fixture(scope="module")
def yard(tmp_path_factory) -> dict:
    """A throwaway checkout that is a REAL git worktree, built under tmp.

    Not `git worktree add` against this repository: that would register a
    worktree beside the operator's live ones. An upstream repo is created here
    and the worktree is taken from it, so `.git` is a FILE for the reason the
    clone guard cares about and nothing outside `tmp_path` is touched.
    """
    base = tmp_path_factory.mktemp("yard")
    upstream = base / "upstream"
    upstream.mkdir()
    git = ["git", "-C", str(upstream), "-c", "user.email=t@example.invalid",
           "-c", "user.name=t"]
    subprocess.run(git[:3] + ["init", "-q"], check=True, capture_output=True)
    (upstream / "seed").write_text("seed\n", encoding="utf-8")
    subprocess.run(git + ["add", "seed"], check=True, capture_output=True)
    subprocess.run(git + ["commit", "-q", "-m", "seed"], check=True,
                   capture_output=True)
    tree = base / "wt"
    subprocess.run(git + ["worktree", "add", "-q", str(tree), "-b", "wt"],
                   check=True, capture_output=True)
    _populate(tree)

    binstub = base / "bin"
    binstub.mkdir()
    stub = binstub / "herdr"
    stub.write_text(_HERDR_STUB, encoding="utf-8")
    stub.chmod(0o755)
    return {"clone": tree, "bin": binstub, "argv": base / "argv.bin",
            **_delivery_fixture(base, tree)}


def _delivery_fixture(base: Path, checkout: Path) -> dict:
    """The transcript tree the sender's two delivery checks read.

    The directory NAME comes from `checkpoint_paths.transcript_dir`, the one
    owner of that rule, so this fixture cannot pass against a mangle the real
    run does not perform. Only the ROOT is relocated, which is what
    `--projects-root` is for.
    """
    from scripts.utils.checkpoint_paths import transcript_dir

    projects = base / "projects"
    owned = transcript_dir(checkout)
    target = projects / owned.name
    target.mkdir(parents=True, exist_ok=True)
    # A pane with no session is refused, so the fixture gives it one.
    session = target / "session.jsonl"
    session.write_text('{"type":"user"}\n', encoding="utf-8")
    return {"projects": projects, "transcript": session}


def _populate(checkout: Path) -> None:
    for rel in _CLONE_FILES:
        target = checkout / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((ROOT / rel).read_text(encoding="utf-8"),
                          encoding="utf-8")


def _run_sender(env_home: dict, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{env_home['bin']}{os.pathsep}{env['PATH']}"
    env["STUB_ARGV"] = str(env_home["argv"])
    env["STUB_CHECKOUT"] = str(env_home["clone"])
    env["STUB_TRANSCRIPT"] = str(env_home["transcript"])
    return subprocess.run(
        [sys.executable, str(env_home["clone"] / SENDER_REL), *args,
         "--projects-root", str(env_home["projects"])],
        capture_output=True, text=True, check=False, env=env,
        cwd=str(env_home["clone"]))


def _stub_argv(env_home: dict) -> list[str]:
    """Every argument the fake `herdr` was called with, in order.

    NUL-separated by the stub because a brief contains newlines, and a
    line-oriented log would report a 40-line brief as 40 calls.
    """
    try:
        raw = env_home["argv"].read_bytes()
    except OSError:
        return []
    return [a.decode("utf-8") for a in raw.split(b"\0") if a]


@pytest.fixture(autouse=True)
def _clear_argv(request):
    for name in ("helm", "yard"):
        if name in request.fixturenames:
            home = request.getfixturevalue(name)
            home["argv"].unlink(missing_ok=True)
    yield


# ==========================================================================
# The marker, in the gate: the refusing direction
# ==========================================================================

def test_the_literal_is_exactly_what_helm_prepends():
    """The seam is one string. If it drifts, everything below is theatre."""
    assert D._BRIEF_MARKER == EXPECTED_MARKER


@pytest.mark.parametrize("brief,command,action", [
    (THE_BRIEF, "git commit -m 'done'", "commit"),
    (THE_BRIEF, "git tag -a v1 -m x", "commit"),
    (THE_PUSH_BRIEF, "git push origin HEAD", "push"),
    (THE_PUSH_BRIEF, ".venv/bin/python scripts/push-all.py", "push"),
    (THE_PUSH_BRIEF, "git commit -m 'done'", "commit"),
])
def test_a_marked_brief_never_authorises_a_release(tmp_path, monkeypatch,
                                                   brief, command, action):
    """The defect, at the real entry point. Pre-fix every case returned None."""
    verdict = _gate(tmp_path, monkeypatch, command, brief)
    assert verdict is not None, (
        f"the marked brief authorised a {action}: {command!r}")
    assert verdict["decision"] == "block"
    assert verdict["_policy_deny"] is True
    assert EXPECTED_MARKER in verdict["reason"]


def test_the_refusal_says_what_it_does_not_establish(tmp_path, monkeypatch):
    """Fail-open by construction, said out loud rather than left to be found."""
    verdict = _gate(tmp_path, monkeypatch, "git commit -m x", THE_BRIEF)
    reason = verdict["reason"]
    assert "DOES NOT ESTABLISH" in reason
    assert "fail-open" in reason


def test_a_marked_brief_writes_no_audit_record(tmp_path, monkeypatch):
    """The observable consequence: nothing was logged as authorised."""
    log_dir = tmp_path / "release"
    monkeypatch.setattr(D, "_RELEASE_STATE_DIR", log_dir)
    D.check_release_gate(_payload("git commit -m x",
                                  _transcript(tmp_path, THE_BRIEF)))
    assert not (log_dir / "authorised.jsonl").exists()


@pytest.mark.parametrize("wrapper", [
    "{m}",
    "  {m}",                                   # indented
    "> {m}",                                   # quoted in a reply
    "Forwarding HELM's brief:\n\n    {m}\n",   # pasted mid-message
    "```\n{m}\n```",                           # fenced
])
def test_the_marker_is_matched_anywhere_in_the_prompt(wrapper):
    """A brief that gets quoted, indented or wrapped must still be inert."""
    prompt = wrapper.format(m=EXPECTED_MARKER) + "\n\nplease commit and push"
    assert D.prompt_authorises(prompt, "commit") is False
    assert D.prompt_authorises(prompt, "push") is False


# ==========================================================================
# ...and the passing direction: the operator's real vocabulary still works
# ==========================================================================

def test_the_authorising_vocabulary_is_not_empty():
    """A floor outside the loops below. See MIN_PUSH_WORDS."""
    assert len(D._PUSH_WORDS) >= MIN_PUSH_WORDS, D._PUSH_WORDS
    assert len(D._COMMIT_WORDS) >= MIN_COMMIT_WORDS, D._COMMIT_WORDS


def test_every_push_word_still_authorises_a_push():
    """Taken from the gate's own list, not invented: these are the words the
    operator actually types, and a guard that refuses everything satisfies
    every refusal test above."""
    checked = 0
    for word in D._PUSH_WORDS:
        assert D.prompt_authorises(word, "push") is True, word
        assert D.prompt_authorises(word, "commit") is True, word
        checked += 1
    assert checked >= MIN_PUSH_WORDS


def test_every_commit_word_still_authorises_a_commit():
    checked = 0
    for word in D._COMMIT_WORDS:
        assert D.prompt_authorises(word, "commit") is True, word
        checked += 1
    assert checked >= MIN_COMMIT_WORDS


@pytest.mark.parametrize("word", ["push", "коммить", "запуш"])
def test_the_operators_short_word_passes_the_real_gate(tmp_path, monkeypatch,
                                                       word):
    """End to end, through `check_release_gate`, with the audit log redirected.

    The shortest real authorisation measured in HELM's transcripts is 3
    characters, which is why the ceiling this replaced could not work."""
    action = "commit" if word == "коммить" else "push"
    command = "git commit -m x" if action == "commit" else "git push"
    assert _gate(tmp_path, monkeypatch, command, word) is None


def test_an_unmarked_brief_still_authorises(tmp_path, monkeypatch):
    """The hole, asserted rather than left implicit.

    This is what the mechanism does NOT close: a brief sent by hand with a bare
    `herdr agent prompt` carries no marker and is indistinguishable from the
    operator. If this test ever goes red the gate grew a discriminator that is
    not the marker, and it must be measured before it is trusted."""
    unmarked = THE_BRIEF.replace(EXPECTED_MARKER + "\n", "")
    assert EXPECTED_MARKER not in unmarked
    assert _gate(tmp_path, monkeypatch, "git commit -m x", unmarked) is None


# ==========================================================================
# The sender, so the marker is not a promise
# ==========================================================================

def test_the_sender_prepends_the_marker_and_delegates(helm):
    result = _run_sender(helm, "w37:p1", "TASK FROM HELM. Do the thing.")
    assert result.returncode == 0, result.stdout + result.stderr
    argv = _stub_argv(helm)
    assert argv[:3] == ["agent", "prompt", "w37:p1"], argv
    assert len(argv) == 4, argv
    sent = argv[3]
    assert sent.startswith(EXPECTED_MARKER + "\n"), repr(sent[:120])
    # The body is followed by the per-send id line the delivery check looks for
    # in the addressed session's transcript (added 2026-09-06). The body itself
    # must still be verbatim and unwrapped, so this asserts on the whole tail
    # rather than loosening to a containment check.
    body = "TASK FROM HELM. Do the thing."
    assert body in sent
    tail = sent.split(body, 1)[1]
    assert tail.strip().startswith("X-HEADING-BRIEF-ID: "), repr(tail[:80])
    assert tail.count("\n") <= 3, (
        f"more than the id line follows the brief: {tail!r}")


def test_the_sender_prints_what_it_is_about_to_send(helm):
    result = _run_sender(helm, "w37:p1", "TASK FROM HELM. Do the thing.")
    assert EXPECTED_MARKER in result.stdout
    assert "TASK FROM HELM. Do the thing." in result.stdout
    assert "About to send to w37:p1" in result.stdout


def test_the_sender_refuses_text_that_already_carries_the_marker(helm):
    """Sending it would produce a brief carrying the marker twice."""
    result = _run_sender(helm, "w37:p1", f"{EXPECTED_MARKER}\n\nagain")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "REFUSED" in result.stderr
    assert _stub_argv(helm) == [], "herdr was called on a refused brief"


def test_the_sender_refuses_a_text_carrying_the_marker_twice(helm):
    result = _run_sender(
        helm, "w37:p1", f"{EXPECTED_MARKER}\nx\n{EXPECTED_MARKER}\n")
    assert result.returncode == 2
    assert "2 time(s)" in result.stderr
    assert _stub_argv(helm) == []


def test_the_sender_refuses_to_run_from_a_yard(yard):
    """Delivering a prompt changes a running process, which a YARD never does."""
    result = _run_sender(yard, "w37:p1", "TASK FROM HELM. Do the thing.")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "HELM" in result.stderr
    assert _stub_argv(yard) == [], "a YARD reached herdr"


def test_the_sender_never_reaches_a_shell():
    """AST, not a substring: a grep goes red the moment a comment explains the
    pattern, which teaches people to stop explaining."""
    tree = ast.parse((ROOT / SENDER_REL).read_text(encoding="utf-8"))
    calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = getattr(target, "attr", None) or getattr(target, "id", None)
        if name not in {"run", "Popen", "call", "check_output", "system"}:
            continue
        calls += 1
        for kw in node.keywords:
            assert kw.arg != "shell" or (
                isinstance(kw.value, ast.Constant) and kw.value.value is False), (
                "shell=True in the sender")
    assert calls >= 1, "no subprocess call found; the AST walk found nothing"


def test_the_marker_is_spelled_once_in_the_engine():
    """One literal, in the wall. The sender imports it.

    This repository's dominant defect shape is a fix that landed in one of N
    copies, and a security literal written twice is one edit from being written
    once. Docstrings and comments are excluded: they explain the seam and are
    not what either side matches on."""
    holders = []
    scanned = 0
    for tree_name in ("scripts", ".claude/hooks"):
        for path in sorted((ROOT / tree_name).rglob("*.py")):
            try:
                source = path.read_text(encoding="utf-8")
            except OSError:
                continue
            scanned += 1
            if EXPECTED_MARKER not in source:
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            docstrings = set()
            for node in ast.walk(tree):
                body = getattr(node, "body", None)
                if isinstance(node, (ast.Module, ast.FunctionDef,
                                     ast.AsyncFunctionDef, ast.ClassDef)) and body:
                    first = body[0]
                    if (isinstance(first, ast.Expr)
                            and isinstance(first.value, ast.Constant)
                            and isinstance(first.value.value, str)):
                        docstrings.add(id(first.value))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant)
                        and isinstance(node.value, str)
                        and node.value == EXPECTED_MARKER
                        and id(node) not in docstrings):
                    holders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert scanned >= 100, f"the walk found only {scanned} files"
    assert len(holders) == 1, f"the marker is spelled in {len(holders)}: {holders}"
    assert holders[0].startswith(GATE_REL), holders


def test_the_sender_reads_the_marker_from_the_gate(helm):
    """Not a copy: change the gate's constant and the sender follows."""
    gate = helm["clone"] / GATE_REL
    source = gate.read_text(encoding="utf-8")
    patched = source.replace(
        f'_BRIEF_MARKER = "{EXPECTED_MARKER}"', '_BRIEF_MARKER = "X-ALTERED: nope"')
    assert patched != source, "the constant assignment was not found to patch"
    gate.write_text(patched, encoding="utf-8")
    try:
        result = _run_sender(helm, "w37:p1", "TASK FROM HELM. Do the thing.")
        assert result.returncode == 0, result.stdout + result.stderr
        assert _stub_argv(helm)[3].startswith("X-ALTERED: nope")
    finally:
        gate.write_text(source, encoding="utf-8")
