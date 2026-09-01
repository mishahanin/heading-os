"""Build numbering must report what is true, including "this never ran".

Measured 2026-08-23: `BUILD.json` exists in no repo at all. Not in
`../heading-os-corporate/`, and not in any of the four exec overlays under
`corporate/BUILD.json`. Corporate has been published three times, the last on
2026-06-27, and `publish-corporate.py --bump-build` is opt-in, so it was never
passed. The numbering subsystem has never produced a number.

That is a legitimate state. Reporting it wrongly is not, and two places did:

  1. `workspace-health.py:check_build_sync` warned "corporate repo may not be
     cloned locally". The repo IS cloned; only the file is absent. A reader
     acting on that message goes looking for a clone problem that does not
     exist, which is the failure `.claude/rules/scope-claims.md` is about.
  2. The same function printed `data.get("last_updated")`, while `bump_build`
     writes the key `timestamp`. So even after a first real bump the line would
     have read `last_updated: ?` forever. `check-build.py` reads `timestamp`
     and is correct, which is what makes the mismatch visible.

A third, smaller one: `check-build.py` indexed `corp["build"]` directly, so a
BUILD.json missing that key raised a KeyError traceback instead of saying what
was wrong with the file.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HEALTH = ROOT / "scripts" / "workspace-health.py"
CHECK_BUILD = ROOT / "scripts" / "check-build.py"
PUBLISH = ROOT / "scripts" / "publish-corporate.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def health():
    return _load(HEALTH, "workspace_health_mod")


@pytest.fixture(scope="module")
def publish():
    return _load(PUBLISH, "publish_corporate_mod")


# --- the reader and the writer must agree on the key --------------------------

def test_the_health_check_reads_the_timestamp_key_the_writer_writes(health, publish,
                                                                    tmp_path, monkeypatch,
                                                                    capsys):
    """The end-to-end contract: bump writes it, health prints it."""
    monkeypatch.setattr(publish, "CORPORATE_ROOT", tmp_path)
    publish.bump_build(summary="test bump", files_changed=1)
    written = json.loads((tmp_path / "BUILD.json").read_text(encoding="utf-8"))

    monkeypatch.setattr(health, "WORKSPACE", tmp_path / "heading-os")
    corp = tmp_path / "heading-os-corporate"
    corp.mkdir(parents=True, exist_ok=True)
    (corp / "BUILD.json").write_text(json.dumps(written), encoding="utf-8")

    health.check_build_sync()
    out = capsys.readouterr().out
    assert written["timestamp"][:10] in out, (
        f"the health check did not print the timestamp the bump wrote. "
        f"Output was: {out!r}"
    )
    assert "?" not in out.split("\n")[-2], "a key mismatch is printing a '?' placeholder"


# --- an absent file must be diagnosed correctly -------------------------------

def test_an_absent_build_file_is_not_blamed_on_a_missing_clone(health, tmp_path,
                                                               monkeypatch, capsys):
    """The repo exists; the file was never created. Say that, not the other thing."""
    monkeypatch.setattr(health, "WORKSPACE", tmp_path / "heading-os")
    (tmp_path / "heading-os-corporate").mkdir(parents=True)   # cloned, but no BUILD.json

    rc = health.check_build_sync()
    out = capsys.readouterr().out
    assert rc == 0, "a never-bumped build number is a state, not a health failure"
    assert "not be cloned" not in out, (
        f"the check still blames a missing clone for a missing file: {out!r}"
    )
    assert "bump-build" in out, (
        "the message should name the thing that creates the file, so the reader "
        f"can act on it. Output was: {out!r}"
    )


def test_a_genuinely_missing_clone_is_still_called_out(health, tmp_path,
                                                       monkeypatch, capsys):
    """The opposite error would be just as bad: silence when the repo IS absent."""
    monkeypatch.setattr(health, "WORKSPACE", tmp_path / "heading-os")
    rc = health.check_build_sync()
    out = capsys.readouterr().out
    assert rc == 0
    assert "not cloned" in out or "not found" in out


# --- check-build must not traceback on a malformed file -----------------------

@pytest.fixture(scope="module")
def check_build():
    return _load(CHECK_BUILD, "check_build_mod")


def test_check_build_reports_a_malformed_file_instead_of_a_traceback(check_build,
                                                                     tmp_path,
                                                                     monkeypatch,
                                                                     capsys):
    """A BUILD.json with no `build` key used to raise KeyError at line 56."""
    bad = tmp_path / "BUILD.json"
    bad.write_text('{"version": "1.0.0"}', encoding="utf-8")   # no "build"
    monkeypatch.setattr(check_build, "CORPORATE_BUILD", bad)

    with pytest.raises(SystemExit) as exc:
        check_build.main()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "missing" in out and "build" in out, (
        f"the error did not name what was wrong with the file: {out!r}"
    )


def test_check_build_still_reports_an_absent_file_plainly(check_build, tmp_path,
                                                          monkeypatch, capsys):
    monkeypatch.setattr(check_build, "CORPORATE_BUILD", tmp_path / "nope.json")
    with pytest.raises(SystemExit) as exc:
        check_build.main()
    assert exc.value.code == 1
    assert "Cannot read" in capsys.readouterr().out


# --- a byte that is not UTF-8 -------------------------------------------------
#
# Both readers of BUILD.json carry a handler widened for the decode class, and
# neither had a test standing on it. MEASURED 2026-09-01, over the 194 tests in
# the seven files that name these two scripts: narrowing
# `workspace-health.check_build_sync` back to `(OSError, json.JSONDecodeError)`
# survived, and so did narrowing `check-build.load_json` back to `(OSError,
# json.JSONDecodeError)`. `Path.read_text(encoding="utf-8")` raises
# `UnicodeDecodeError` from inside the READ, before either parser is handed
# anything, and that exception is a SIBLING of `JSONDecodeError` under
# `ValueError` rather than a subclass of it - so the narrow tuple lets it through
# and the whole health run dies on one byte in a file it was only reporting on.

BAD_BYTE = b"\xff"


def test_the_health_check_survives_an_undecodable_build_file(health, tmp_path,
                                                             monkeypatch, capsys):
    monkeypatch.setattr(health, "WORKSPACE", tmp_path / "heading-os")
    corp = tmp_path / "heading-os-corporate"
    corp.mkdir(parents=True)
    (corp / "BUILD.json").write_bytes(b'{"build": 3, "note": "' + BAD_BYTE + b'"}')

    rc = health.check_build_sync()

    assert rc == 1, "an unusable BUILD.json is a reported issue, not a pass"
    out = capsys.readouterr().out
    assert "parse failed" in out, (
        f"the failure was not reported as a parse problem: {out!r}"
    )


def test_check_build_survives_an_undecodable_build_file(check_build, tmp_path,
                                                        monkeypatch, capsys):
    bad = tmp_path / "BUILD.json"
    bad.write_bytes(b'{"build": 3, "version": "1.0.0", "n": "' + BAD_BYTE + b'"}')
    monkeypatch.setattr(check_build, "CORPORATE_BUILD", bad)

    with pytest.raises(SystemExit) as exc:
        check_build.main()

    assert exc.value.code == 1
    assert "Cannot read" in capsys.readouterr().out


def test_a_decodable_build_file_is_still_read(check_build, tmp_path, monkeypatch,
                                              capsys):
    """The negative case. A reader that treated every file as unreadable would
    satisfy both tests above while reporting nothing at all."""
    good = tmp_path / "BUILD.json"
    good.write_text(json.dumps({"build": 3, "version": "1.0.0"}), encoding="utf-8")
    monkeypatch.setattr(check_build, "CORPORATE_BUILD", good)

    assert check_build.load_json(good) == {"build": 3, "version": "1.0.0"}
    assert capsys.readouterr().out == ""


# --- the live state, pinned so a future reader does not re-derive it ----------

def test_the_live_corporate_repo_still_has_no_build_number():
    """Documents the measurement above. If this ever fails, someone bumped for
    the first time and the numbering subsystem is finally live - update the
    module docstring rather than deleting the test."""
    live = ROOT.parent / "heading-os-corporate"
    if not live.exists():
        pytest.skip("no local corporate clone on this machine")
    assert not (live / "BUILD.json").exists(), (
        "BUILD.json now exists. Build numbering has produced its first number; "
        "revisit the 'never bumped' claims in this file's docstring."
    )
