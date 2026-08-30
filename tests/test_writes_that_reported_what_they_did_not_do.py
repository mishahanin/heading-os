"""Three write paths that told the operator something other than what happened.

scripts/memlog.py
  - `append` and `set` on a workspace with no `.memlog.md` died with a raw
    FileNotFoundError, in a script whose sibling `init` already refuses its own
    absent/present case with a printed line and exit 2.
  - `render` neutralizes newlines in VALUES only, so an unchecked KEY could
    rewrite the frontmatter from inside the fence. Measured: `set --key
    $'note\\nstatus' --value done` wrote a `status: done` line into the
    frontmatter of an active memlog, and the ack the command printed in the
    same breath still said `"status": "active"`.

scripts/memory-auto-retire.py
  - the index-rewrite line printed `len(names)`, the number of MEMORIES
    retired, under the words "removed N pointer(s)". They are different numbers
    by design: a pointer that names a path rather than a bare filename is left
    alone. This is the audit trail of a destructive edit to an operator-curated
    index, so overstating a deletion is the worst direction to be wrong in.

Run: .venv/bin/python -m pytest tests/test_writes_that_reported_what_they_did_not_do.py -q
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import memlog  # noqa: E402
from scripts.utils import memory_stores  # noqa: E402
from scripts.utils.memory_expiry import strip_index_pointers  # noqa: E402
from scripts.utils.workspace import get_auto_memory_dir  # noqa: E402

_retire_spec = importlib.util.spec_from_file_location(
    "memory_auto_retire", ROOT / "scripts" / "memory-auto-retire.py")
retire = importlib.util.module_from_spec(_retire_spec)
_retire_spec.loader.exec_module(retire)

MEMLOG = ".memlog.md"
_ANSI = re.compile(r"\033\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


# ============================================================
# memlog: a refusal, not a traceback
# ============================================================

def _init(ws: Path) -> None:
    assert memlog.main(["init", "--workspace", str(ws),
                        "--field", "topic=Reinvent the lunchbox",
                        "--field", "goal=ideas for a pitch"]) == 0


@pytest.mark.parametrize("argv_tail", [
    ["append", "--text", "the counterpart moved first"],
    ["set", "--key", "status", "--value", "done"],
])
def test_append_and_set_refuse_a_workspace_with_no_memlog(tmp_path, capsys, argv_tail):
    cmd, *rest = argv_tail
    rc = memlog.main([cmd, "--workspace", str(tmp_path), *rest])
    err = capsys.readouterr().err

    assert rc == 2
    assert str(tmp_path / MEMLOG) in err
    assert "run init first" in err
    assert not (tmp_path / MEMLOG).exists()  # a refusal creates nothing


def test_the_refusal_uses_the_same_exit_code_as_init(tmp_path, capsys):
    """Derived, not typed: init's guard is the shape the other two copy.

    init refuses a memlog that is already there; append and set refuse one that
    is not. Whatever exit code init uses for its half, these use for theirs.
    """
    absent = memlog.main(["append", "--workspace", str(tmp_path), "--text", "x"])
    _init(tmp_path)
    already_there = memlog.main(["init", "--workspace", str(tmp_path)])
    capsys.readouterr()

    assert already_there == absent


# ============================================================
# memlog: a key that rewrites the fence
# ============================================================

HOSTILE_KEYS = [
    "note\nstatus",     # a newline splits one field into two
    "note\rstatus",     # and so does a bare carriage return
    "sta:tus",          # a colon re-files the tail under a shorter key
    "",                 # an empty key renders a nameless line
]
# `set` takes the key raw, so it must refuse padding too; `init --field` strips
# the key before it ever gets here, so that door cannot see this case.
SET_ONLY_KEYS = [" topic "]


@pytest.mark.parametrize("key", HOSTILE_KEYS + SET_ONLY_KEYS)
def test_set_refuses_a_key_that_would_rewrite_the_frontmatter(tmp_path, capsys, key):
    _init(tmp_path)
    path = tmp_path / MEMLOG
    before = path.read_text(encoding="utf-8")
    capsys.readouterr()

    rc = memlog.main(["set", "--workspace", str(tmp_path), "--key", key, "--value", "done"])
    err = capsys.readouterr().err

    assert rc == 2
    assert "--key" in err
    assert path.read_text(encoding="utf-8") == before  # byte-for-byte untouched


@pytest.mark.parametrize("key", HOSTILE_KEYS)
def test_init_refuses_the_same_keys_through_field(tmp_path, capsys, key):
    rc = memlog.main(["init", "--workspace", str(tmp_path), "--field", f"{key}=done"])
    err = capsys.readouterr().err

    assert rc == 2
    assert "--field key" in err
    assert not (tmp_path / MEMLOG).exists()


def test_a_hostile_key_cannot_flip_the_status_field(tmp_path, capsys):
    """The concrete harm, stated as the operator would meet it.

    `status` is the field the host skill reads to decide whether a piece of
    work is still live. The injected key moved it without ever naming it.
    """
    _init(tmp_path)
    path = tmp_path / MEMLOG
    memlog.main(["set", "--workspace", str(tmp_path), "--key", "note\nstatus",
                 "--value", "done"])
    capsys.readouterr()

    meta, _body = memlog.split(path.read_text(encoding="utf-8"))
    assert meta["status"] == "active"


def test_an_ordinary_key_still_sets(tmp_path, capsys):
    """The guard refuses shapes, not use."""
    _init(tmp_path)
    rc = memlog.main(["set", "--workspace", str(tmp_path), "--key", "stage",
                      "--value", "pricing round"])
    capsys.readouterr()
    meta, _body = memlog.split((tmp_path / MEMLOG).read_text(encoding="utf-8"))

    assert rc == 0
    assert meta["stage"] == "pricing round"
    assert meta["status"] == "active"


def test_a_multi_line_value_is_still_accepted_and_flattened(tmp_path, capsys):
    """Values were never the problem and must not become one."""
    _init(tmp_path)
    rc = memlog.main(["set", "--workspace", str(tmp_path), "--key", "goal",
                      "--value", "hold the discount\ntrade on scope"])
    capsys.readouterr()
    meta, _body = memlog.split((tmp_path / MEMLOG).read_text(encoding="utf-8"))

    assert rc == 0
    assert meta["goal"] == "hold the discount trade on scope"


# ============================================================
# memory-auto-retire: a count that is measured
# ============================================================

EXPIRED = "---\nexpires: 2020-01-01\n---\n\nA fact with a date on it.\n"
# One line, two pointers, one of them path-qualified. `strip_index_pointers`
# leaves a path-style target alone by design (memory_expiry:160-164), so
# retiring both memories removes exactly one pointer.
INDEX = "# Memory index\n\n- Group: [dated fact](threads/foo.md) · [other fact](bar.md)\n"
RETIRED = ("foo.md", "bar.md")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """An auto-memory dir the run cannot escape from."""
    data_root = tmp_path / "data"
    mem = data_root / "auto-memory"
    mem.mkdir(parents=True)
    for name in RETIRED:
        (mem / name).write_text(EXPIRED, encoding="utf-8")
    (mem / "MEMORY.md").write_text(INDEX, encoding="utf-8")

    monkeypatch.setenv("HEADING_OS_DATA", str(data_root))
    monkeypatch.setenv("HEADING_OS_TZ", "UTC")
    # The real .env must not reach os.environ from inside a test, and the audit
    # log must not reach the engine's .logs/.
    monkeypatch.setattr(retire, "load_env", lambda *a, **k: None)
    monkeypatch.setattr(retire, "LOG_PATH", tmp_path / "audit.log")
    # retire_memory deletes `name` from EVERY store, including the operator's
    # real per-launch harness stores under ~/.claude/projects. Not from a test.
    # A named zero-argument stub, not `list`. Ruff's PIE807 autofix here is
    # `list`, and `list` would be a WORSE stub: the real function takes no
    # arguments, but `list("abc")` happily returns `['a', 'b', 'c']`, so a future
    # caller passing an argument would get a plausible answer instead of the
    # TypeError the real function raises.
    def _no_native_stores():
        return []

    monkeypatch.setattr(memory_stores, "iter_native_memory_stores", _no_native_stores)
    # main() parses sys.argv, which under pytest is pytest's own command line.
    monkeypatch.setattr(sys, "argv", ["memory-auto-retire.py"])

    assert get_auto_memory_dir() == mem.resolve(), "the sandbox did not take"
    return mem


def _links(text: str) -> int:
    """Every markdown link in the text, counted independently of the code."""
    return text.count("](")


def test_the_index_line_reports_the_pointers_it_actually_removed(sandbox, capsys):
    before = (sandbox / "MEMORY.md").read_text(encoding="utf-8")

    assert retire.main() == 0
    out = _plain(capsys.readouterr().out)

    after = (sandbox / "MEMORY.md").read_text(encoding="utf-8")
    actually_removed = _links(before) - _links(after)

    reported = int(re.search(r"removed (\d+) pointer", out).group(1))
    assert reported == actually_removed
    # And the premise: fewer pointers went than memories did, which is the gap
    # the old `len(names)` hid. Both sides measured, neither typed.
    assert 0 < actually_removed < len(RETIRED)
    assert f"removed {len(RETIRED)} pointer" not in out


def test_the_path_style_pointer_survives_the_rewrite(sandbox, capsys):
    assert retire.main() == 0
    capsys.readouterr()
    after = (sandbox / "MEMORY.md").read_text(encoding="utf-8")

    assert "](threads/foo.md)" in after
    assert "](bar.md)" not in after


def test_the_retired_memories_are_gone_from_the_store(sandbox, capsys):
    assert retire.main() == 0
    capsys.readouterr()

    for name in RETIRED:
        assert not (sandbox / name).exists()


def test_the_helper_agrees_with_an_independent_count(sandbox):
    """The reported number, checked against the rewriter's own output.

    `_pointers_removed` reads link targets; `_links` counts link openings. Two
    methods, one answer, so a defect in either shows up as a disagreement.
    """
    before = (sandbox / "MEMORY.md").read_text(encoding="utf-8")
    after = strip_index_pointers(before, list(RETIRED))

    assert retire._pointers_removed(before, after) == _links(before) - _links(after)


def test_a_name_pointed_at_twice_counts_twice(sandbox):
    """Occurrences, not distinct targets: two pointers gone is two, not one."""
    before = ("- Group one: [a](bar.md) · [b](keep.md)\n"
              "- Group two: [c](bar.md)\n")
    after = strip_index_pointers(before, ["bar.md"])

    assert retire._pointers_removed(before, after) == _links(before) - _links(after)
