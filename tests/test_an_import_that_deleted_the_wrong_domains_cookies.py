"""Shard scripts-utils-00-p3: six guards that were narrower than their words.

The cookie half destroys data.

- `_merge_playwright` promises "cookies for other domains are preserved" and
  matched by bare string suffix. `"netflix.com".endswith("x.com")` is True, and
  `x.com` is a live target of this workspace through /x-pulse, so importing one
  domain deleted the stored session of unrelated ones.
- With `--out --playwright`, a run that read ZERO cookies still truncated the
  file, dropped that domain's entries, and printed a large green number - the
  size of the whole store, which says nothing about whether the import worked.
- The documented CLI line could not run at all outside the editable venv: a
  workspace import sat above the `sys.path` bootstrap that exists to resolve it.
- Every failed snapshot leaked a temp file that no caller could clean up.

The checkpoint half quietly disarms a control the operator relies on.

- A second `raise_unattended` overwrote the record of who turned `session_auto`
  on, so the following `--unattended off` printed "a pause waits for you again"
  while auto stayed on for the rest of the session.
- `prune_state_dir` globs `checkpoint-*.json`, which cannot match
  `checkpoint-x.json.lock`, so the lock sidecars grew without bound in the very
  directory the function exists to bound. Measured: 25 state files (the cap)
  beside 22 orphan locks.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import checkpoint_paths as CP  # noqa: E402
from scripts.utils import chromium_cookies as CC  # noqa: E402


# ============================================================
# The cookie store -- importing one domain may not delete another
# ============================================================

def _store(tmp_path: Path, entries) -> Path:
    p = tmp_path / "cookies.json"
    p.write_text(json.dumps(entries))
    return p


# The per-cookie attribute bag `_read_cookies` carries beside the host and the
# value since 2026-08-30, when the Playwright export stopped stamping four
# constants over the DB's real `path` / `is_secure` / `is_httponly` / `samesite`.
# These tests pin the EVICTION rule and care about none of it, so they hand it
# the neutral bag; the attributes are measured in
# `tests/test_an_exporter_that_stamped_constants_over_the_real_flags.py`.
ATTRS = {"path": "/", "secure": False, "httpOnly": False, "sameSite": "Lax"}


def _fresh(domain: str, **names) -> dict:
    """`_merge_playwright`'s input shape: {name: (host_key, value, attrs)}.

    It took a flat {name: value} until 2026-08-28 and stamped every entry with
    `.{domain}`, which widened a host-only cookie to all of that domain's
    subdomains. The host now travels with the value. These tests pin the
    EVICTION rule, not the scoping, so they hand it the domain cookie for the
    domain being imported and keep asserting exactly what they asserted before.
    """
    return {n: (f".{domain.lstrip('.')}", v, ATTRS) for n, v in names.items()}


@pytest.mark.parametrize("kept_domain,imported", [
    (".netflix.com", "x.com"),          # "netflix.com".endswith("x.com")
    (".linux.com", "x.com"),
    (".myyoutube.com", "youtube.com"),
    (".notgoogle.com", "google.com"),
])
def test_a_domain_that_merely_ends_the_same_is_not_deleted(tmp_path, kept_domain, imported):
    """The docstring's promise, measured. Each of these was destroyed by an
    import of an unrelated domain."""
    store = _store(tmp_path, [{"name": "keep", "domain": kept_domain}])

    merged = CC._merge_playwright(store, imported, _fresh(imported, fresh="v"))

    assert kept_domain in {c["domain"] for c in merged}


@pytest.mark.parametrize("stale", [".x.com", "x.com", "sub.x.com", ".sub.x.com"])
def test_this_domain_and_its_subdomains_are_still_replaced(tmp_path, stale):
    """The behaviour the loose match was reaching for, kept: a stale value for
    a name we just re-read is wrong."""
    store = _store(tmp_path, [{"name": "old", "domain": stale}])

    merged = CC._merge_playwright(store, "x.com", _fresh("x.com", fresh="v"))

    assert [c["name"] for c in merged] == ["fresh"]


def test_an_unparseable_store_is_still_treated_as_empty(tmp_path):
    """Documented behaviour: refusing to import is worse than losing a store
    that was already unusable."""
    store = tmp_path / "cookies.json"
    store.write_text("{not json")

    merged = CC._merge_playwright(store, "x.com", _fresh("x.com", fresh="v"))

    assert [c["name"] for c in merged] == ["fresh"]


# ============================================================
# An empty read must not overwrite the file
# ============================================================

def _run_main(monkeypatch, capsys, argv, cookies):
    detailed = {n: (".example.com", v, ATTRS) for n, v in cookies.items()}
    monkeypatch.setattr(CC, "_read_cookies", lambda *a, **k: (detailed, []))
    monkeypatch.setattr(sys, "argv", ["chromium_cookies.py", *argv])
    code = CC._main()
    return code, capsys.readouterr()


def test_an_empty_read_leaves_the_store_untouched(tmp_path, monkeypatch, capsys):
    """A wrong profile, a wrong domain, or every v11 blob failing to decrypt all
    produce an empty dict. The merge then dropped this domain's entries and the
    `O_TRUNC` open rewrote the file, while the green line reported the size of
    what survived."""
    store = _store(tmp_path, [{"name": "session", "domain": ".youtube.com"}])
    before = store.read_bytes()

    code, out = _run_main(monkeypatch, capsys,
                          ["youtube.com", "--out", str(store), "--playwright"], {})

    assert code == 1
    assert store.read_bytes() == before, "an empty read destroyed the store"
    assert "left untouched" in out.err


def test_a_real_read_reports_what_it_imported_not_just_the_store_size(
        tmp_path, monkeypatch, capsys):
    """"36 cookie(s) in the store" was printed whether the import added 36 or
    zero. The caller's only question is whether THIS import worked."""
    store = _store(tmp_path, [{"name": "other", "domain": ".google.com"}])

    code, out = _run_main(monkeypatch, capsys,
                          ["youtube.com", "--out", str(store), "--playwright"],
                          {"SID": "abc", "HSID": "def"})

    assert code == 0
    assert "2 cookie(s) imported for youtube.com" in out.out
    assert "3 in the store" in out.out


def test_a_plain_out_write_still_works(tmp_path, monkeypatch, capsys):
    """The non-playwright path is untouched apart from the empty guard."""
    out_file = tmp_path / "plain.json"

    code, out = _run_main(monkeypatch, capsys,
                          ["youtube.com", "--out", str(out_file)], {"SID": "abc"})

    assert code == 0
    assert json.loads(out_file.read_text()) == {"SID": "abc"}
    assert "1 cookie(s)" in out.out


# ============================================================
# The module must run the way its own docstring says to run it
# ============================================================

def test_the_documented_cli_line_runs_on_a_bare_interpreter():
    """The workspace import sat ABOVE the `sys.path.insert` that resolves it, so
    this died with ModuleNotFoundError anywhere the repo root was not already on
    the path. It worked under `.venv/bin/python` only because the editable
    install drops a `.pth` there, and `requirements.txt` installs no such thing.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    res = subprocess.run(
        [sys.executable, "-S", "-c",
         "import sys; sys.path = [p for p in sys.path if 'heading-os' not in p]; "
         f"exec(open({str(ROOT / 'scripts/utils/chromium_cookies.py')!r}).read(), "
         "{'__name__': '__not_main__', '__file__': "
         f"{str(ROOT / 'scripts/utils/chromium_cookies.py')!r}}})"],
        capture_output=True, text=True, env=env, cwd=str(ROOT.parent))

    assert res.returncode == 0, res.stderr
    assert "ModuleNotFoundError" not in res.stderr


def test_the_path_bootstrap_precedes_every_workspace_import():
    """Read as source, because the order is the whole defect and a successful
    import under the editable venv proves nothing about it."""
    lines = (ROOT / "scripts" / "utils" / "chromium_cookies.py").read_text().splitlines()
    bootstrap = next(i for i, ln in enumerate(lines) if "sys.path.insert" in ln)
    imports = [i for i, ln in enumerate(lines)
               if ln.startswith(("from scripts.", "import scripts."))]

    assert imports, "the anchor is gone; this test would pass over nothing"
    assert min(imports) > bootstrap, (
        "a workspace import runs before the sys.path bootstrap that resolves it")


# ============================================================
# A failed snapshot must not leak
# ============================================================

def test_a_failed_snapshot_removes_its_own_temp_file(tmp_path):
    """The caller's cleanup begins only once this returns, so a failure in here
    left an orphan nobody could collect. The CLI's own advice ("close the
    browser fully and retry") means the path is hit repeatedly."""
    not_a_db = tmp_path / "fake.sqlite"
    not_a_db.write_text("not a database")
    before = set(Path(tempfile.gettempdir()).glob("chromium_cookies_*"))

    with pytest.raises(sqlite3.DatabaseError):
        CC._snapshot_db(not_a_db)

    assert set(Path(tempfile.gettempdir()).glob("chromium_cookies_*")) == before


def test_a_good_snapshot_still_returns_a_readable_copy(tmp_path):
    """The cleanup must not have become a delete-on-success."""
    src = tmp_path / "real.sqlite"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE cookies (name TEXT)")
    conn.execute("INSERT INTO cookies VALUES ('SID')")
    conn.commit()
    conn.close()

    snap = CC._snapshot_db(src)
    try:
        assert snap.exists()
        got = sqlite3.connect(snap).execute("SELECT name FROM cookies").fetchall()
        assert got == [("SID",)]
    finally:
        snap.unlink(missing_ok=True)


# ============================================================
# `--unattended off` must actually turn it off
# ============================================================

def test_a_second_raise_does_not_pin_auto_on_forever(monkeypatch):
    """The second raise read the `session_auto = True` the FIRST raise wrote and
    filed it as an operator-held prior, so lowering restored True. Two raises is
    the ordinary path: the CLI has no already-on guard, and an accepted
    `--compact-at N` raises the mode as well."""
    monkeypatch.delenv("CLAUDE_HANDOFF_AUTO", raising=False)
    state: dict = {}

    CP.raise_unattended(state)
    CP.raise_unattended(state)
    CP.lower_unattended(state)

    assert CP.auto_mode(state) is False
    assert "session_auto" not in state, "the session must go back to deferring"


def test_a_single_raise_and_lower_is_unchanged(monkeypatch):
    monkeypatch.delenv("CLAUDE_HANDOFF_AUTO", raising=False)
    state: dict = {}

    CP.raise_unattended(state)
    CP.lower_unattended(state)

    assert CP.auto_mode(state) is False


def test_an_operator_held_auto_survives_any_number_of_raises(monkeypatch):
    """The restore must stay a restore. Lowering may not switch off an auto the
    operator turned on themselves."""
    monkeypatch.delenv("CLAUDE_HANDOFF_AUTO", raising=False)
    state = {"session_auto": True}

    CP.raise_unattended(state)
    CP.raise_unattended(state)
    CP.raise_unattended(state)
    CP.lower_unattended(state)

    assert state["session_auto"] is True


def test_a_deliberate_false_is_restored_not_dropped(monkeypatch):
    """`False` and absent are different: absent defers to the environment."""
    monkeypatch.delenv("CLAUDE_HANDOFF_AUTO", raising=False)
    state = {"session_auto": False}

    CP.raise_unattended(state)
    CP.raise_unattended(state)
    CP.lower_unattended(state)

    assert state["session_auto"] is False


def test_the_switch_itself_still_goes_off(monkeypatch):
    monkeypatch.delenv("CLAUDE_HANDOFF_AUTO", raising=False)
    state: dict = {}

    CP.raise_unattended(state)
    CP.raise_unattended(state)
    CP.lower_unattended(state)

    assert state["session_unattended"] is False


# ============================================================
# The pruner must bound the directory it owns
# ============================================================

def _state_pair(d: Path, name: str) -> tuple[Path, Path]:
    js = d / f"checkpoint-{name}.json"
    js.write_text("{}")
    lock = d / f"checkpoint-{name}.json.lock"
    lock.write_text("")
    return js, lock


def test_a_pruned_state_file_takes_its_lock_with_it(tmp_path):
    """`checkpoint-x.json.lock` never matched the `checkpoint-*.json` glob, so
    the JSON half pruned at the cap while the lock half grew forever."""
    made = [_state_pair(tmp_path, f"s{i:03d}") for i in range(CP.KEEP_MAX + 5)]
    keep = made[-1][0].name

    CP.prune_state_dir(tmp_path, keep)

    survivors = sorted(p.name for p in tmp_path.glob("checkpoint-*.json"))
    locks = sorted(p.name for p in tmp_path.glob("checkpoint-*.json.lock"))
    # KEEP_MAX of the prunable ones, plus the live session's own file, which is
    # excluded from the candidate list rather than counted against the cap.
    assert len(survivors) == CP.KEEP_MAX + 1
    # The property that matters, and the one that was false: no lock outlives
    # the state file it belongs to.
    assert [f"{s}.lock" for s in survivors] == locks


def test_an_orphan_lock_from_before_the_fix_is_collected(tmp_path):
    """22 of these exist on this workspace right now. Nothing else will ever
    remove them, so the pruner has to."""
    (tmp_path / "checkpoint-gone.json.lock").write_text("")
    keep_js, _ = _state_pair(tmp_path, "live")

    CP.prune_state_dir(tmp_path, keep_js.name)

    assert not (tmp_path / "checkpoint-gone.json.lock").exists()
    assert (tmp_path / f"{keep_js.name}.lock").exists(), "the live lock stays"


def test_the_kept_session_keeps_its_lock(tmp_path):
    """Deleting the running session's lock would be worse than leaving orphans:
    it is the file `locked_state` is holding."""
    keep_js, keep_lock = _state_pair(tmp_path, "live")

    CP.prune_state_dir(tmp_path, keep_js.name)

    assert keep_js.exists()
    assert keep_lock.exists()
