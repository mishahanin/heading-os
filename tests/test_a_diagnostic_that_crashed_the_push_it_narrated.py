#!/usr/bin/env python3
"""The push wall's own diagnostics could not be emitted over the paths it carried.

`scripts/utils/git_push.py` decodes git's raw path bytes with `surrogateescape`
deliberately: a POSIX path is bytes and need not be valid UTF-8, so a repository
directory named `b"re\\xffpo"` becomes the string `"re\\udcffpo"` and every
comparison downstream is exact. `test_the_root_reader_does_not_run_in_subprocess
_text_mode` in the sibling file guards that decode.

Then the module PRINTS it. A lone surrogate cannot be ENCODED by any codec.

MEASURED 2026-09-05 on `main` at 26d84ca::

    .venv/bin/python -m pytest --capture=sys \\
      "tests/test_a_push_wall_that_refused_the_root_it_was_given.py\\
::test_the_chokepoint_does_not_refuse_a_root_for_being_itself[not-utf8]" -q

      UnicodeEncodeError: 'utf-8' codec can't encode character '\\udcff' in
      position 54: surrogates not allowed
      scripts/utils/git_push.py:443

The wall is documented as failing OPEN and LOUDLY. The loud part was a hard
crash of the push it was added to narrate.

WHY THE SIBLING FILE DID NOT CATCH IT, and why that is the gap this file fills.
`test_the_chokepoint_does_not_refuse_a_root_for_being_itself` drives a real push
over exactly these exotic names and asserts the push SUCCEEDS. It never asked
whether the module's own diagnostics could be emitted at all, and under pytest's
DEFAULT `--capture=fd` they can: pytest hands the test an `EncodedFile` opened
with `errors="replace"`, which encodes anything. The defect is invisible to the
default runner and appears under `--capture=sys`, `--capture=no` and
`--capture=tee-sys`. This file therefore never relies on the ambient stream: it
installs a stream with a KNOWN encoding and a strict error handler, which is
what production has.

Both directions are asserted, because the two wrong fixes both make a crash
stop happening:

  * dropping the print, or the value inside it, leaves the operator with no
    message and no path to go and look at;
  * `errors="replace"` prints `re?po`, which is a path that does not exist,
    offered as the path to go and look at.

MUTATION-VERIFIED 2026-09-05. Editor-only edits, one live mutation at a time,
each reverted before the next, and both source files checked against their
pre-run sha256 afterwards. Every result below came from::

    .venv/bin/python -m pytest \\
      tests/test_a_diagnostic_that_crashed_the_push_it_narrated.py -q \\
      --capture=sys -p no:randomly

    Baseline, unmutated: 8 passed in 4.74s.

DIRECTION ONE, the fix removed. `git_push.py` carries nine `safe_for_stream`
call sites; each was unwrapped on its own. Two of the nine are killed by this
file, and they are the two paths a repository NAME actually reaches here.

Line 489, the lower-ceiling NOTE. Three tests die inside `print` itself, before
any assertion runs::

    scripts/utils/git_push.py:489: in remote_objection
    E  UnicodeEncodeError: 'utf-8' codec can't encode character '\\udcff' in
       position 54: surrogates not allowed

    E  UnicodeEncodeError: 'ascii' codec can't encode characters in position
       52-55: ordinal not in range(128)          <- the ascii-stdout test

    3 failed, 5 passed. Killed:
    test_the_lower_ceiling_note_is_emitted_over_a_non_utf8_repository_name,
    test_the_chokepoint_emits_its_note_instead_of_crashing_the_push,
    test_an_ascii_stdout_gets_an_escaped_message_rather_than_a_crash.

Line 681, the `not a git repository root` reason. Caught one frame EARLIER than
the crash, on the assertion about the returned value::

    E  AssertionError: the refusal dropped the identifying path:
       "/tmp/.../re\\udcffpo/nested is not a git repository root: it sits inside
       the repository at /tmp/.../re\\udcffpo. ..."
    E  assert 're\\\\udcffpo' in "..."

    1 failed, 7 passed. Killed:
    test_the_not_a_root_refusal_can_be_printed_by_its_caller.

Seven survive: lines 402, 435, 442, 462, 705, 722, 732. Each needs a state no
fixture here produces (unreadable workspace roots, a remote that IS the engine's
push URL, a GitHub PUBLIC verdict, a token plus a github.com host whose
visibility cannot be read, an engine clone carrying data-class artifacts). Line
435 was carried further, to ask whether anything ELSE holds it: with its wrapper
removed, `test_git_push.py`, `test_a_wall_that_looked_at_a_different_world.py`,
`test_a_push_that_verified_the_wrong_repository.py`, `test_push_all_gate.py`,
`security/test_leak_path_matrix.py` and
`test_a_push_wall_that_refused_the_root_it_was_given.py` gave 204 passed. Those
suites drive those branches with ASCII names, where the wrapper is the identity
function, so no test there can notice its absence. NOT CLAIMED: the other six
survivors were reasoned from their branch conditions, not measured against the
rest of the suite.

DIRECTION TWO, the wrong fix installed in `scripts/utils/stream_safe.py`. Three
shapes of `errors="replace"`, each reverted before the next.

Whole-string `text.encode(enc, "replace").decode(enc, "replace")`, and the
one-token swap of `_escaped` to `"replace"` instead of `"backslashreplace"`:
both give 6 failed, 2 passed, and
`test_the_helper_never_silently_drops_the_identifying_value` is among the six::

    E  AssertionError: the byte the operator needs in order to find the
       directory is gone: 're?po'
    E  assert 'udcff' in 're?po'

`text.encode(enc, "surrogateescape").decode(enc, "replace")`, the shape
`stream_safe`'s own docstring rejects for writing the raw byte back out: 7
failed, 1 passed, and the same test catches it on its OTHER assertion::

    E  AssertionError: replaced instead of escaped: 're\\ufffdpo'
    E  assert U+FFFD not in 're\\ufffdpo'

So the test guards what it was written to guard, and its two assertions are not
redundant. An encode-side `replace` emits `?`, never U+FFFD, so only the
`udcff` assertion catches the first two shapes; U+FFFD arrives only when a
DECODE is involved, and only that assertion catches the third.

Nothing here reaches a network. Every repository is local, and no wall is
removed to be tested.

Run: .venv/bin/python -m pytest \\
     tests/test_a_diagnostic_that_crashed_the_push_it_narrated.py -q
"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.git_push import remote_objection, supervised_push  # noqa: E402
from scripts.utils.stream_safe import safe_for_stream  # noqa: E402

# The raw directory-name bytes, and what each MUST look like once it has been
# rendered for a strict UTF-8 stream. The two rows are the two directions: the
# byte that no codec can encode has to become visible text, and the byte
# sequence that every UTF-8 stream carries perfectly well has to be left alone.
NOT_UTF8 = b"re\xffpo"
NOT_UTF8_RENDERED = "re\\udcffpo"

CYRILLIC = "репо".encode("utf-8")
CYRILLIC_RENDERED_UTF8 = "репо"
CYRILLIC_RENDERED_ASCII = "\\u0440\\u0435\\u043f\\u043e"


class _StrictStream(io.TextIOWrapper):
    """A text stream with a real encoding and no error handler to hide behind.

    This is production: `sys.stdout` in a normal locale encodes strictly and
    raises on anything its codec cannot take. It is NOT what pytest's default
    `--capture=fd` supplies, which is why this class exists rather than
    `capsys`.
    """

    def __init__(self, encoding: str):
        super().__init__(io.BytesIO(), encoding=encoding, errors="strict",
                         write_through=True)

    def text(self) -> str:
        self.flush()
        return self.buffer.getvalue().decode(self.encoding, "strict")


def _repo_named(base: Path, raw: bytes) -> Path:
    """A real git repository whose directory name is `raw`, or skip."""
    target = base / os.fsdecode(raw)
    try:
        target.mkdir(parents=True)
    except (OSError, ValueError):
        pytest.skip(f"{raw!r} is not a creatable directory name here")
    proc = subprocess.run(["git", "init", "-q", "-b", "main", str(target)],
                          capture_output=True)
    if proc.returncode != 0:
        pytest.skip(f"git refused to init at {raw!r}")
    return target


def _bare_remote(tmp_path: Path, work: Path) -> Path:
    """`work` wired to a local bare remote, with one commit on main."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                   check=True, capture_output=True)
    for args in (["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "Test"],
                 ["remote", "add", "origin", str(remote)]):
        subprocess.run(["git", "-C", str(work), *args], check=True,
                       capture_output=True)
    (work / "f.txt").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "init"], check=True,
                   capture_output=True)
    return remote


# ============================================================
# 1 - the wall's own NOTE reaches a strict stream, and says something
# ============================================================

def test_the_lower_ceiling_note_is_emitted_over_a_non_utf8_repository_name(tmp_path):
    """The reproduction, as an assertion about the MESSAGE rather than the push.

    `remote_objection` is the real entry point that prints it; the chokepoint
    below drives the same path through `supervised_push`.
    """
    work = _repo_named(tmp_path, NOT_UTF8)
    _bare_remote(tmp_path, work)

    stream = _StrictStream("utf-8")
    with contextlib.redirect_stdout(stream):
        objection = remote_objection(work, remote="origin")

    assert objection is None, objection
    printed = stream.text()

    assert printed.strip(), (
        "the wall reached its lower ceiling and said NOTHING; the operator is "
        "told a push proceeded on the offline check alone by silence")
    assert "NOTE:" in printed, printed
    assert NOT_UTF8_RENDERED in printed, (
        f"the repository name did not survive into the message: {printed!r}. A "
        f"path that lost its odd byte names a directory that does not exist, "
        f"which is what errors='replace' would have produced here")
    assert "�" not in printed, (
        f"the identifying byte was replaced rather than escaped: {printed!r}")


def test_the_chokepoint_emits_its_note_instead_of_crashing_the_push(tmp_path):
    """The end-to-end shape of the original crash: a real push, a strict stream.

    The sibling file's version of this runs under whatever stream pytest
    supplies, so it went green over the defect.
    """
    work = _repo_named(tmp_path, NOT_UTF8)
    remote = _bare_remote(tmp_path, work)

    stream = _StrictStream("utf-8")
    with contextlib.redirect_stdout(stream):
        verdict = supervised_push(work, remote="origin", branch="main",
                                  stall_window=15, log_dir=str(tmp_path))

    assert verdict["state"] == "ok", verdict
    head = subprocess.run(["git", "-C", str(remote), "rev-parse", "main"],
                          capture_output=True, text=True, errors="replace")
    assert head.returncode == 0, "the push was refused or lost"
    assert NOT_UTF8_RENDERED in stream.text(), stream.text()


# ============================================================
# 2 - ordinary non-ASCII is not collateral damage
# ============================================================

def test_cyrillic_reaches_a_utf8_stream_completely_unchanged(tmp_path):
    """The regression the sibling file cannot catch for anyone.

    Its `non-ascii-utf8` parameter asserts only that the push succeeds, so a
    fix that escaped every non-ASCII character would leave it green while
    turning every Cyrillic path in the operator's own messages into `\\u0440`
    soup.
    """
    work = _repo_named(tmp_path, CYRILLIC)
    _bare_remote(tmp_path, work)

    stream = _StrictStream("utf-8")
    with contextlib.redirect_stdout(stream):
        remote_objection(work, remote="origin")

    printed = stream.text()
    assert CYRILLIC_RENDERED_UTF8 in printed, printed
    assert "\\u04" not in printed, (
        f"Cyrillic a UTF-8 stream carries perfectly well was escaped: {printed!r}")


# ============================================================
# 3 - and it holds when stdout is NOT UTF-8
# ============================================================

def test_an_ascii_stdout_gets_an_escaped_message_rather_than_a_crash(tmp_path):
    """A redirected pipe under the C locale, or a Windows console.

    Both values are unencodable here, so both must be escaped and neither may
    raise. `errors="strict"` on the stream is the whole point: an ASCII stream
    that silently replaced them would pass a weaker version of this test.
    """
    work = _repo_named(tmp_path, CYRILLIC)
    _bare_remote(tmp_path, work)

    stream = _StrictStream("ascii")
    with contextlib.redirect_stdout(stream):
        remote_objection(work, remote="origin")

    printed = stream.text()
    assert printed.strip(), "an ASCII stdout silenced the wall's diagnostic"
    assert CYRILLIC_RENDERED_ASCII in printed, printed


# ============================================================
# 4 - the refusal a caller prints, not only the ones this module prints
# ============================================================

def test_the_not_a_root_refusal_can_be_printed_by_its_caller(tmp_path):
    """`safe-push.py`, `publish-service.py` and `push-all.py` print this string,
    and `promote-knowledge.py` calls `.encode()` on it with strict UTF-8. The
    wall must not hand them a value that crashes one frame up.
    """
    work = _repo_named(tmp_path, NOT_UTF8)
    _bare_remote(tmp_path, work)
    sub = work / "nested"
    sub.mkdir()

    stream = _StrictStream("utf-8")
    with contextlib.redirect_stdout(stream):
        verdict = supervised_push(sub, remote="origin", branch="main",
                                  stall_window=15, log_dir=str(tmp_path))

    assert verdict["state"] == "failed", verdict
    reason = verdict["reason"]
    assert "not a git repository root" in reason, reason
    assert NOT_UTF8_RENDERED in reason, (
        f"the refusal dropped the identifying path: {reason!r}")
    # What every caller does with it, asserted rather than assumed.
    print(reason, file=stream)
    reason.encode()


# ============================================================
# 5 - the helper itself, including the inputs that would move the crash
# ============================================================

def test_the_helper_returns_encodable_text_for_every_stream_shape():
    """Requirement four: a helper whose job is to make output safe, and which
    throws on some input, has moved the crash rather than removed it.
    """
    text = "re\udcffpo репо \U0001f600"
    shapes = {
        "utf-8": _StrictStream("utf-8"),
        "ascii": _StrictStream("ascii"),
        "latin-1": _StrictStream("latin-1"),
        "utf-16": _StrictStream("utf-16"),
        # No `.encoding` at all, an unknown codec name, and a name that is a
        # real codec but not a TEXT one: each would raise inside a naive helper.
        "no-encoding": io.StringIO(),
        "unknown-codec": type("S", (), {"encoding": "definitely-not-a-codec"})(),
        "non-text-codec": type("S", (), {"encoding": "base64"})(),
        "encoding-is-none": type("S", (), {"encoding": None})(),
    }
    assert len(shapes) == 8, "floor: the eight stream shapes measured 2026-09-05"

    for name, stream in shapes.items():
        rendered = safe_for_stream(text, stream)
        assert rendered, f"{name}: the helper emptied the message"
        enc = getattr(stream, "encoding", None)
        if isinstance(enc, str) and enc in ("utf-8", "ascii", "latin-1", "utf-16"):
            # The property that matters: writing it now cannot raise.
            rendered.encode(enc, "strict")


def test_the_helper_escapes_rather_than_drops_and_leaves_the_rest_alone():
    utf8 = _StrictStream("utf-8")
    assert safe_for_stream("re\udcffpo", utf8) == "re\\udcffpo"
    # Identity on the fast path, byte for byte.
    assert safe_for_stream(CYRILLIC_RENDERED_UTF8, utf8) == CYRILLIC_RENDERED_UTF8
    assert safe_for_stream("plain ascii", utf8) == "plain ascii"
    # Only the unencodable characters change; the neighbours do not.
    assert safe_for_stream("a\udcffb", _StrictStream("ascii")) == "a\\udcffb"
    assert (safe_for_stream("aрb", _StrictStream("ascii")) == "a\\u0440b")
    # A CR is encodable, so it must survive: the sibling file's `carriage-return`
    # case depends on the path comparing equal to itself.
    assert safe_for_stream("re\rpo", utf8) == "re\rpo"


def test_the_helper_never_silently_drops_the_identifying_value():
    """The negative direction, stated as its own test because both wrong fixes
    produce a string that does not raise.
    """
    rendered = safe_for_stream("re\udcffpo", _StrictStream("utf-8"))
    assert "�" not in rendered, f"replaced instead of escaped: {rendered!r}"
    assert rendered != "repo", "the odd byte was deleted, naming a different path"
    assert "udcff" in rendered, (
        f"the byte the operator needs in order to find the directory is gone: "
        f"{rendered!r}")
