"""The engine leak wall decoded git's path bytes with the host's locale.

`engine_guard.repo_carried_paths` enumerates every file git would carry out of
the engine clone, and both content gates -- `scripts/content-guard.py` and the
UNBYPASSABLE `push-all.engine_content_scan` -- select their targets from it. It
ran `subprocess.run(..., text=True)` with no `encoding=`, which decodes stdout
with `locale.getpreferredencoding(False)`. The `-z` comment directly above it
explains that this is a bilingual RU/EN workspace where a Cyrillic filename is
ordinary; the missing encoding handed those bytes to whatever locale the host
booted with.

Measured 2026-08-30 in a scratch repo holding `docs/план.md`:

  * under the process default (UTF-8) the path came back intact;
  * under `LC_ALL=C` with UTF-8 mode off -- preferred encoding
    `ANSI_X3.4-1968` -- the call raised `UnicodeDecodeError` and took the entire
    scan with it, at the one moment a push is being screened;
  * on a host whose preferred encoding decodes every byte to SOMETHING (stock
    Windows Python, cp1252) the same path returns as mojibake instead, so
    `engine_text_files`' `is_file()` is False and the file is dropped from the
    content gates in silence. That third host cannot be produced here, which is
    exactly why the fix is to stop asking the locale at all rather than to test
    each locale.

One thing the fix does NOT reach, measured the same day and recorded here so it
is not rediscovered as new: under `LC_ALL=C` with both PEP 538 coercion and
UTF-8 mode disabled, `sys.getfilesystemencoding()` is `ascii`, and then the
interpreter cannot NAME the file at all -- `os.fsencode('docs/план.md')` raises
`UnicodeEncodeError`, `Path.is_file()` swallows it and answers False, and
`engine_text_files` drops the path from the content gates in silence. That is a
property of the interpreter's filesystem encoding rather than of this module,
and reaching it takes two deliberate overrides (CPython coerces the C locale to
C.UTF-8 on its own, and Windows has used UTF-8 for filesystem paths since PEP
529). The tests below therefore assert enumeration and routing in both
environments, and content-gate selection only where the filesystem encoding can
express the name.

These tests drive the guard in a CHILD process, because the locale is read from
the environment at decode time. They assert the wall's real answers about a real
git repository; nothing greps the source.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# A Cyrillic name and a name whose bytes are not UTF-8 at all. The first is the
# ordinary workspace case; the second is what `errors="surrogateescape"` is for.
CYRILLIC_REL = "docs/план.md"
CYRILLIC_PRIVATE_REL = "crm/contacts/иван-петров.md"

_PROBE = r"""
import json, sys
sys.path.insert(0, {repo!r})
from pathlib import Path
from scripts.utils.engine_guard import (
    engine_text_files, find_data_artifacts, repo_carried_paths,
)
root = Path(sys.argv[1])
try:
    carried = repo_carried_paths(root)
except Exception as exc:
    print(json.dumps({{"error": type(exc).__name__ + ": " + str(exc)}}))
    raise SystemExit(0)
print(json.dumps({{
    "carried": sorted(carried),
    "text": sorted(engine_text_files(root, carried)),
    "flagged": sorted(find_data_artifacts(carried)),
}}))
"""


@pytest.fixture
def clone(tmp_path):
    """A git repo carrying one engine-routed and one private-routed Cyrillic file."""
    root = tmp_path / "clone"
    (root / "docs").mkdir(parents=True)
    (root / "crm" / "contacts").mkdir(parents=True)
    (root / CYRILLIC_REL).write_text("# заметка\n", encoding="utf-8")
    (root / CYRILLIC_PRIVATE_REL).write_text("# Ivan Petrov\n", encoding="utf-8")
    (root / "README.md").write_text("ascii\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


def _run_guard(root: Path, env_overrides: dict[str, str]) -> dict:
    env = dict(os.environ)
    env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE.format(repo=str(REPO)), str(root)],
        capture_output=True, text=True, encoding="utf-8", env=env, check=True,
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


# The environments the wall must give the SAME answer in. The second is the one
# that used to break it; `PYTHONCOERCECLOCALE=0` stops CPython's PEP 538
# coercion from quietly repairing the C locale into C.UTF-8.
LOCALES = {
    "utf8_default": {},
    "ascii_c_locale": {
        "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
    },
}


@pytest.mark.skipif(os.name != "posix", reason="locale probe is POSIX-only")
def test_the_ascii_locale_probe_really_is_a_different_decoder(clone):
    """Guard the guard: if both environments decode identically, this file proves
    nothing. Assert the probe environment actually changes the answer."""
    encodings = {}
    for name, overrides in LOCALES.items():
        env = dict(os.environ)
        env.update(overrides)
        proc = subprocess.run(
            [sys.executable, "-c",
             "import locale; print(locale.getpreferredencoding(False))"],
            capture_output=True, text=True, env=env, check=True,
        )
        encodings[name] = proc.stdout.strip()
    assert encodings["utf8_default"].lower().replace("-", "") == "utf8"
    assert encodings["ascii_c_locale"].lower().replace("-", "") != "utf8"


@pytest.mark.skipif(os.name != "posix", reason="locale probe is POSIX-only")
@pytest.mark.parametrize("locale_name", sorted(LOCALES))
def test_the_wall_enumerates_a_cyrillic_path_whatever_the_host_locale_is(
    clone, locale_name
):
    result = _run_guard(clone, LOCALES[locale_name])
    assert "error" not in result, result.get("error")
    assert result["carried"], "the corpus is empty; the fixture did not commit"
    assert CYRILLIC_REL in result["carried"]
    assert CYRILLIC_PRIVATE_REL in result["carried"]


@pytest.mark.skipif(os.name != "posix", reason="locale probe is POSIX-only")
def test_a_cyrillic_engine_file_still_reaches_the_content_gates(clone):
    """`engine_text_files` is how both content gates pick their targets, and it
    filters on `is_file()`. A mis-decoded name does not exist on disk, so the
    file is scanned by nothing at all.

    UTF-8 only, and the module docstring says why: where the FILESYSTEM encoding
    cannot express the name either, no decode fix in this module can make the
    path resolvable.
    """
    result = _run_guard(clone, LOCALES["utf8_default"])
    assert "error" not in result, result.get("error")
    assert result["text"], "the corpus is empty; nothing was selected to scan"
    assert CYRILLIC_REL in result["text"]


@pytest.mark.skipif(os.name != "posix", reason="locale probe is POSIX-only")
@pytest.mark.parametrize("locale_name", sorted(LOCALES))
def test_a_cyrillic_private_path_is_still_flagged_as_a_data_artifact(
    clone, locale_name
):
    """The routing half of the wall, driven by the same enumeration. Its prefixes
    are ASCII, so it survives a mangled tail -- but it cannot survive the
    enumeration raising, and under the C locale that is what happened."""
    result = _run_guard(clone, LOCALES[locale_name])
    assert "error" not in result, result.get("error")
    assert CYRILLIC_PRIVATE_REL in result["flagged"]
    assert CYRILLIC_REL not in result["flagged"]


@pytest.mark.skipif(os.name != "posix", reason="undecodable bytes need POSIX")
def test_a_filename_that_is_not_utf8_at_all_is_carried_rather_than_lost(tmp_path):
    """`errors="surrogateescape"`, not `errors="replace"`, and the difference is
    a leak. A filesystem may hold bytes that are not valid UTF-8; `replace`
    would turn them into U+FFFD, which names a different file that does not
    exist, and the wall would skip the real one. Surrogates round-trip through
    `os.fsencode`, so the path still resolves."""
    root = tmp_path / "latin"
    (root / "docs").mkdir(parents=True)
    raw = os.fsdecode(b"docs/caf\xe9.md")          # latin-1 bytes, invalid UTF-8
    (root / raw).write_text("x", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)

    result = _run_guard(root, {})
    assert "error" not in result, result.get("error")
    assert result["carried"], "the corpus is empty; the fixture did not commit"
    # The point is not the spelling of the name but that the file survived the
    # trip and is still resolvable on disk.
    assert len(result["carried"]) == 1
    assert (root / result["carried"][0]).is_file()
    assert result["text"] == result["carried"]
