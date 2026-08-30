"""Shard scripts-utils-03-p1: a promise about a YAML parser that was false in
both directions, and a guard a relaunch switched off for a whole process tree.

* `yamlio`'s docstring said the ONE divergence from `yaml.safe_load` was an
  unsupported `%YAML` version directive, and that the divergence direction was
  always STRICTER, "so every fail-closed handler built on `except
  yaml.YAMLError` fails toward the safe answer, never away from it". Measured
  2026-08-26: there are three divergences, the documented one is narrower than
  stated (a minor bump only; `%YAML 2.0` is rejected by both), and two of them
  run the LOOSER way. A tab between a key and its value is a ScannerError under
  the pure-Python SafeLoader and parses fine under CSafeLoader. The 14-case
  corpus behind the original claim contained no tab.

* `venv_guard.ensure_venv` set its exec-loop sentinel with
  `os.environ[...] = "1"`, which is putenv, so the flag was inherited by every
  DESCENDANT of a relaunched process rather than consumed by it. Any child
  spawned with a non-venv interpreter had the guard silently disabled and ran
  against system site-packages instead of the pinned set. Inside the relaunched
  process the sentinel was redundant: the `interpreter_identity` comparison two
  lines above already returns.

* Its docstring said `os.execv` makes the guard "correct even if some heavy
  modules were already imported before the call". True for imports; the re-exec
  restarts the script from line 1, so any SIDE EFFECT above the call runs twice,
  and output written above it is duplicated on an unbuffered stream and lost
  outright on a block-buffered one.

Run: python3 -m pytest tests/test_a_relaunch_that_disarmed_every_child.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import venv_guard as _venv  # noqa: E402
from scripts.utils import yamlio  # noqa: E402


# ============================================================
# The divergence that ran the other way
# ============================================================

TAB_CASES = [
    ("a tab between key and value",
     "default: engine\nrules:\n  crm/:\tprivate\n",
     {"default": "engine", "rules": {"crm/": "private"}}),
    ("a tab inside a scalar value",
     "default: engine\nrules:\n  crm/: pri\tvate\n",
     {"default": "engine", "rules": {"crm/": "pri\tvate"}}),
]


@pytest.mark.parametrize("label,src,expected",
                         TAB_CASES, ids=[c[0] for c in TAB_CASES])
def test_the_reference_loader_refuses_what_this_one_accepts(label, src, expected):
    """The finding, both halves in one assertion pair."""
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(src)

    assert yamlio.safe_load(src) == expected


@pytest.mark.parametrize("src", [
    "default: engine\nrules:\n\tcrm/: private\n",
    "default: engine\nrules:\n \tcrm/: private\n",
])
def test_a_tab_used_as_indentation_is_still_refused_by_both(src):
    """Not every tab diverges. YAML forbids a tab in indentation, and libyaml
    agrees, so the divergence is narrower than "tabs"."""
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(src)
    with pytest.raises(yaml.YAMLError):
        yamlio.safe_load(src)


def test_the_documented_divergence_is_a_minor_version_only():
    """The docstring said "an unsupported %YAML version directive". A major
    bump is refused by both, so the stricter case is narrower than claimed."""
    minor = "%YAML 1.3\n---\ndefault: engine\n"
    assert yaml.safe_load(minor) == {"default": "engine"}
    with pytest.raises(yaml.YAMLError):
        yamlio.safe_load(minor)

    for major in ("%YAML 2.0\n---\ndefault: engine\n",
                  "%YAML 9.9\n---\ndefault: engine\n"):
        with pytest.raises(yaml.YAMLError):
            yaml.safe_load(major)
        with pytest.raises(yaml.YAMLError):
            yamlio.safe_load(major)


def test_a_supported_version_directive_is_accepted_by_both():
    for directive in ("%YAML 1.1", "%YAML 1.2"):
        src = f"{directive}\n---\ndefault: engine\n"
        assert yaml.safe_load(src) == yamlio.safe_load(src) == {"default": "engine"}


def _flat(text: str) -> str:
    """One line, single spaces. A docstring wraps, so a phrase assertion that
    does not normalise fails on where the author happened to break the line."""
    return " ".join(text.split())


def test_the_docstring_no_longer_claims_one_stricter_divergence():
    """The retracted claim is gone AND the correction that replaced it is there.

    COMMENT CORRECTED 2026-08-30. It used to read "the old sentence is QUOTED in
    the correction, on purpose, so the test asserts the correction rather than
    the absence of the words" -- and the very next line asserted exactly that
    absence. Both could not be true: if the correction really quoted the old
    sentence verbatim, the assertion would fail on every run. MEASURED
    2026-08-30: `yamlio.__doc__` does NOT contain "The one divergence is an
    unsupported", so the comment's premise was the false half. The word
    "divergence" IS still in the docstring, in the corrected sentence, which is
    what makes the specific phrase a safe probe for the retracted claim rather
    than a ban on the vocabulary.

    The three positive assertions carry the weight; the absence catches a
    straight revert, which the positives alone would not.
    """
    doc = _flat(yamlio.__doc__)

    assert "The one divergence is an unsupported" not in doc
    assert "THREE known ways" in doc
    assert "LOOSER" in doc
    assert "the corpus contained no tab" in doc
    assert "divergence" in doc, (
        "the retracted phrasing was removed by deleting the whole subject, not "
        "by correcting it; this test can no longer tell a fix from a deletion")


def test_the_docstring_says_a_handler_must_carry_its_own_value_check():
    """The sentence that stops the next author leaning on the parse."""
    assert "do not add one that leans on the parse" in _flat(yamlio.__doc__)


# ============================================================
# The guard a relaunch switched off for a whole tree
# ============================================================

@pytest.fixture
def guard(monkeypatch, tmp_path):
    """A stand-in venv the guard will want to re-exec into, plus a clean flag."""
    fake = tmp_path / ".venv" / "bin"
    fake.mkdir(parents=True)
    target = fake / "python"
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target.chmod(0o755)
    calls = []
    monkeypatch.setattr(_venv, "venv_python", lambda: target)
    monkeypatch.setattr(_venv.os, "execv", lambda path, argv: calls.append(path))
    monkeypatch.setattr(_venv, "_SENTINEL_SEEN", False)
    monkeypatch.delenv(_venv._SENTINEL, raising=False)
    # A script for the guard to relaunch. These cases are about the SENTINEL,
    # so they used to inherit whatever `sys.argv[0]` the runner happened to
    # carry -- which is a real path under a plain `pytest` and the literal
    # "-c" inside an xdist worker, because execnet spawns workers that way.
    # `ensure_venv` now refuses to exec a path that is not a file, so the
    # borrowed argv decided the outcome of a test that is not about argv.
    # See tests/test_a_relaunch_that_had_no_script_to_relaunch.py.
    script = tmp_path / "entry.py"
    script.write_text("print('hi')\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(script)])
    return target, calls


def test_the_sentinel_is_removed_from_the_environment_as_it_is_read(guard,
                                                                     monkeypatch):
    """The finding. Left in place, it disabled this guard for every descendant
    process, which then ran against system site-packages."""
    monkeypatch.setenv(_venv._SENTINEL, "1")

    _venv.ensure_venv()

    assert _venv._SENTINEL not in os.environ


def test_the_opt_out_still_stops_the_relaunch(guard, monkeypatch):
    """conftest sets this on purpose, to keep pytest from re-execing itself.

    `monkeypatch.setenv`, never `os.environ[...] = `: a test module that sets
    the sentinel by hand is exactly the stray
    `test_venv_relaunch_guard.py::test_no_test_module_carries_its_own_copy_of_the_guard`
    refuses, because such a copy covers for a missing conftest line."""
    target, calls = guard
    monkeypatch.setenv(_venv._SENTINEL, "1")

    _venv.ensure_venv()

    assert calls == []


def test_the_opt_out_holds_across_repeated_calls(guard, monkeypatch):
    """Popping the variable must not make the SECOND call re-exec. About twenty
    scripts call this at module scope in one pytest process."""
    target, calls = guard
    monkeypatch.setenv(_venv._SENTINEL, "1")

    _venv.ensure_venv()
    _venv.ensure_venv()
    _venv.ensure_venv()

    assert calls == []


def test_the_module_flag_starts_clear_in_a_fresh_process():
    """Every test above patches this flag, so a wrong INITIAL value would be
    invisible here while the guard was dead in every real process. Asked in a
    subprocess, which is the only place the module's own starting state exists."""
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(ROOT)!r})
            from scripts.utils import venv_guard
            print("SEEN", venv_guard._SENTINEL_SEEN)
        """)], capture_output=True, text=True, timeout=60)

    assert proc.returncode == 0, proc.stderr
    assert "SEEN False" in proc.stdout


def test_without_the_opt_out_it_still_re_execs(guard):
    """The guard must not become a no-op."""
    target, calls = guard

    _venv.ensure_venv()

    assert calls == [str(target)]


def test_the_relaunch_still_marks_the_child(guard):
    """The exec-loop protection is the one thing the variable buys, and the
    child needs it before it runs its own identity check."""
    _venv.ensure_venv()

    try:
        assert os.environ.get(_venv._SENTINEL) == "1"
    finally:
        os.environ.pop(_venv._SENTINEL, None)


def test_a_descendant_of_a_relaunched_process_still_gets_the_guard(tmp_path):
    """End to end, with real processes. A relaunched script spawns a child with
    a NON-venv interpreter; that child must not find the guard disabled.

    Measured by what the child sees in its own environment, because the guard's
    only off-switch is that variable.

    The two probe scripts live in `tmp_path`, not beside this file. They used to
    be written into `tests/` under fixed names, which put two scratch files in
    the repository for the length of the run and made the pair collide with any
    second process running the same test. Nothing needs them there: the parent
    puts the repo on `sys.path` itself, so their location is irrelevant.
    """
    parent = tmp_path / "_relaunch_probe_parent.py"
    child = tmp_path / "_relaunch_probe_child.py"
    try:
        child.write_text(textwrap.dedent(f"""
            import os, sys
            sys.path.insert(0, {str(ROOT)!r})
            from scripts.utils.venv_guard import _SENTINEL
            print("CHILD_SEES", os.environ.get(_SENTINEL, "<absent>"))
        """), encoding="utf-8")
        parent.write_text(textwrap.dedent(f"""
            import subprocess, sys
            sys.path.insert(0, {str(ROOT)!r})
            from scripts.utils.venv_guard import ensure_venv
            ensure_venv()
            out = subprocess.run([sys.executable, {str(child)!r}],
                                 capture_output=True, text=True)
            print(out.stdout.strip())
        """), encoding="utf-8")

        # Launched WITH the sentinel already set, standing in for "this process
        # is the relaunched one". Before the fix the child inherited it.
        env = dict(os.environ, **{_venv._SENTINEL: "1"})
        proc = subprocess.run([sys.executable, str(parent)],
                              capture_output=True, text=True, env=env, timeout=60)

        assert proc.returncode == 0, proc.stderr
        assert "CHILD_SEES <absent>" in proc.stdout, proc.stdout
    finally:
        parent.unlink(missing_ok=True)
        child.unlink(missing_ok=True)


def test_the_docstring_warns_about_work_above_the_call():
    """It said the re-exec "is correct even if..." with no caveat, which is what
    tells the next author it is safe to put work above the guard."""
    doc = _flat(_venv.__doc__)

    assert "RESTARTS the script from line 1" in doc
    assert "lost outright on a block-buffered one" in doc
    assert "Put no work above the guard" in doc


def test_the_docstring_records_why_the_sentinel_is_popped():
    assert ("does not disable this guard for every descendant"
            in _flat(_venv.__doc__))
