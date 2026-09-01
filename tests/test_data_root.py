import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import paths

ROOT = Path(__file__).resolve().parent.parent


# ---------- Task 1: get_data_root resolution + demo flag ----------

def test_env_override_wins(tmp_path, monkeypatch):
    d = tmp_path / "mydata"
    d.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(d))
    assert paths.get_data_root() == d.resolve()


def test_the_env_override_is_normalised_not_taken_verbatim(tmp_path, monkeypatch):
    """`env_data_root` ends `return cand.resolve()`, and no case could tell.

    Every override in this file is a `tmp_path` subdirectory, which is already
    resolved, so `return cand` and `return cand.resolve()` produce the identical
    object and the normalisation was free to disappear. Measured 2026-09-01:
    dropping `.resolve()` left this file and all 29 files naming the seam green.

    An unnormalised data root is not cosmetic. It becomes the prefix of every
    `get_*_dir()` answer, and the two questions the seam is asked -- am I in demo
    mode, is there a real overlay -- are both EQUALITY tests against a resolved
    path (`data_root_is_demo`, `data_overlay_present`). A root spelled with a
    climb compares unequal to the same directory spelled plainly, so those
    answers flip while the path still opens the right files. The value arrives
    from `.env` and from systemd units, where a `..` or a trailing `/.` is
    exactly the kind of thing a hand-written path carries.
    """
    real = tmp_path / "overlay"
    (real / "sub").mkdir(parents=True)
    detour = real / "sub" / ".."          # names `real`, spelled with a climb
    monkeypatch.setenv("HEADING_OS_DATA", str(detour))

    resolved = paths.get_data_root()
    assert resolved == real.resolve()
    assert ".." not in resolved.parts, f"the climb survived into {resolved}"


def test_sibling_data_root(tmp_path, monkeypatch):
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    ws = tmp_path / ".heading-os"
    ws.mkdir()
    sib = tmp_path / ".heading-os-data"
    sib.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    assert paths.get_data_root() == sib.resolve()


def test_legacy_in_tree(tmp_path, monkeypatch):
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    ws = tmp_path / "ceo-main"
    (ws / "crm" / "contacts").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    assert paths.get_data_root() == ws.resolve()


def test_in_tree_wins_over_sibling(tmp_path, monkeypatch):
    # Transitional ceo-main protection: when a workspace has BOTH its own
    # in-tree data AND a sibling .heading-os-data, the in-tree data wins, so
    # building the data repo does not flip live ceo-main onto it (Plan 4 D2).
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    ws = tmp_path / "ceo-main"
    (ws / "crm" / "contacts").mkdir(parents=True)
    sib = tmp_path / ".heading-os-data"
    sib.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    assert paths.get_data_root() == ws.resolve()


def test_demo_fallback_and_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    ws = tmp_path / "engine"
    ws.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    assert paths.get_data_root() == (ws / "examples").resolve()
    assert paths.data_root_is_demo() is True


# ---------- data_overlay_present: the narrower question ----------


def test_overlay_present_on_a_real_sibling(tmp_path, monkeypatch):
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    ws = tmp_path / ".heading-os"
    ws.mkdir()
    (tmp_path / ".heading-os-data").mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    assert paths.data_overlay_present() is True


def test_overlay_absent_on_a_demo_clone(tmp_path, monkeypatch):
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    ws = tmp_path / "engine"
    ws.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    assert paths.data_overlay_present() is False


def test_overlay_absent_when_the_engine_clone_wears_the_data_root(tmp_path, monkeypatch):
    """The case an external contributor hit against v0.8.0.

    One stray `knowledge/` inside an engine clone flips the in-tree heuristic, so
    `data_root_is_demo()` answers False and every guard gated on it starts
    asserting CEO documents against a public checkout. The overlay question must
    answer False here even though the demo question does not.
    """
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    ws = tmp_path / ".heading-os"
    (ws / "knowledge").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))

    assert paths.get_data_root() == ws.resolve()
    assert paths.data_root_is_demo() is False   # the old, too-wide gate
    assert paths.data_overlay_present() is False  # the one the guards now ask


# ---------- Task 2: schema-version handshake ----------

def test_schema_missing_marker_is_compatible(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))  # no .schema-version
    ok, _ = paths.check_schema_compatible()
    assert ok is True


def test_a_marker_that_is_not_a_number_reads_as_current(tmp_path, monkeypatch):
    """`read_data_schema_version`'s `ValueError` arm, which nothing drove.

    The docstring says "Missing/unreadable -> assume current" and the handler is
    `except (OSError, ValueError)`, but every case in this file supplies either
    no file (OSError) or a clean integer. Measured 2026-09-01: narrowing the
    handler to `except OSError` left this file and all 29 files naming the seam
    green, while a `.schema-version` holding anything non-numeric raised a bare
    ValueError.

    Where it raises is the point. `check_schema_compatible()` and
    `require_writable_data_root()` both call this, and the second is the guard
    every write path goes through -- so a single stray character in a
    one-line marker file would stop every workspace write with a traceback
    rather than a refusal, on a machine whose data is entirely intact.
    """
    (tmp_path / ".schema-version").write_text("not-a-number\n", encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))

    assert paths.read_data_schema_version() == paths.DATA_SCHEMA_VERSION
    assert paths.check_schema_compatible()[0] is True
    assert paths.require_writable_data_root() == tmp_path.resolve()


def test_an_undecodable_marker_reads_as_current(tmp_path, monkeypatch):
    """The same arm, reached by the byte rather than by the character.

    `UnicodeDecodeError` subclasses `ValueError`, so the existing handler
    already covers this -- but it is a SIBLING of the JSON and YAML decode
    errors, not a subclass, and it fails inside `read_text()` before any parsing
    happens. That combination is what makes it slip past a handler written for
    the parse. Held here so the pair cannot be narrowed to `except ValueError`
    reasoning about `int()` alone and lose the read.
    """
    (tmp_path / ".schema-version").write_bytes(b"\xff\xfe1\n")
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))

    assert paths.read_data_schema_version() == paths.DATA_SCHEMA_VERSION
    assert paths.require_writable_data_root() == tmp_path.resolve()


def test_an_empty_marker_reads_as_current(tmp_path, monkeypatch):
    """The shape a truncated or interrupted write leaves behind."""
    (tmp_path / ".schema-version").write_text("", encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))

    assert paths.read_data_schema_version() == paths.DATA_SCHEMA_VERSION


def test_schema_older_data_is_incompatible(tmp_path, monkeypatch):
    (tmp_path / ".schema-version").write_text("0\n", encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    ok, msg = paths.check_schema_compatible()
    assert ok is False
    assert "migration" in msg.lower()


def test_schema_equal_is_compatible(tmp_path, monkeypatch):
    (tmp_path / ".schema-version").write_text(str(paths.DATA_SCHEMA_VERSION), encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    ok, _ = paths.check_schema_compatible()
    assert ok is True


# ---------- Task 3: fail-closed write guard ----------

def test_require_writable_raises_in_demo(tmp_path, monkeypatch):
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    ws = tmp_path / "engine"
    ws.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    with pytest.raises(paths.DataRootError):
        paths.require_writable_data_root()


def test_require_writable_returns_path_when_real(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    assert paths.require_writable_data_root() == tmp_path.resolve()


# ---------- Task 4: private helpers resolve under the data root ----------

from scripts.utils import workspace


def test_corporate_content_vs_engine_dirs_split(tmp_path, monkeypatch):
    # Plan 4 T2: for the CEO, corporate CONTENT helpers (datastore, context,
    # shared-knowledge, crm-config) resolve under the data root, while ENGINE
    # dirs (reference, config) stay on the workspace/engine root -- even when an
    # explicit data root differs from the workspace root.
    ws = tmp_path / "engine"
    ws.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("HEADING_OS_DATA", str(data))
    workspace._reset_identity_cache()
    dr, wr = str(data.resolve()), str(ws.resolve())
    # corporate content -> data root
    assert str(workspace.get_datastore_dir()).startswith(dr)
    assert str(workspace.get_context_dir()).startswith(dr)
    assert str(workspace.get_shared_knowledge_dir()).startswith(dr)
    assert str(workspace.get_crm_config_path()).startswith(dr)
    # engine dirs -> workspace/engine root, NOT the data root
    assert str(workspace.get_reference_dir()).startswith(wr)
    assert str(workspace.get_config_dir()).startswith(wr)
    assert not str(workspace.get_reference_dir()).startswith(dr)
    assert not str(workspace.get_config_dir()).startswith(dr)


def test_private_helpers_resolve_under_data_root(tmp_path, monkeypatch):
    # With an explicit data root, CEO private helpers must resolve UNDER it,
    # not under the engine/workspace root. Guards against a future helper that
    # hardcodes a path back into the engine tree.
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    workspace._reset_identity_cache()
    dr = str(tmp_path.resolve())
    assert str(workspace.get_crm_contacts_dir()).startswith(dr)
    assert str(workspace.get_knowledge_dir()).startswith(dr)
    assert str(workspace.get_outputs_dir()).startswith(dr)
    assert str(workspace.get_personal_context_dir()).startswith(dr)
    assert str(workspace.get_people_file()).startswith(dr)


# ---------- Task 5: init-data.py scaffolder ----------

def test_init_data_scaffolds_data_root(tmp_path):
    target = tmp_path / ".heading-os-data"
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "init-data.py"), "--path", str(target)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert (target / ".schema-version").read_text().strip() == str(paths.DATA_SCHEMA_VERSION)
    assert (target / "crm" / "contacts").is_dir()
    assert (target / "outputs").is_dir()
    assert (target / "threads" / "business").is_dir()
    assert (target / "knowledge").is_dir()


def test_init_data_refuses_nonempty(tmp_path):
    target = tmp_path / ".heading-os-data"
    (target / "crm").mkdir(parents=True)
    (target / "crm" / "x.md").write_text("existing", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "init-data.py"), "--path", str(target)],
        capture_output=True, text=True,
    )
    assert r.returncode == 1
    assert "not empty" in r.stdout.lower()
