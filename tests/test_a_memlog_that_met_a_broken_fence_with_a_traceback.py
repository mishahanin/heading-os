#!/usr/bin/env python3
"""`scripts/memlog.py` on a `.memlog.md` whose frontmatter fence is broken.

The finding was "missing or malformed", and only the missing half landed:
`cmd_append` and `cmd_set` print `error: ... does not exist; run init first` and
return 2 for a file that is not there, then read the file that IS there straight
through `split`, which raises `ValueError` on an absent or unterminated fence.
`main()` caught nothing, so the operator got eight frames of traceback out of a
script whose every other refusal is one printed line and exit 2.

MEASURED 2026-09-02 on a scratch memlog holding the single line `no fence
here`: `append --text hello` ended in `ValueError: .memlog.md has no
frontmatter`.

A `.memlog.md` is hand-editable by design -- the module docstring says the host
skill re-reads it on resume -- so a broken fence is ordinary bad input, not a
corrupted internal store.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import memlog  # noqa: E402

MEMLOG = ".memlog.md"

# Both refusals `split` can raise, each reached by a different malformation.
BROKEN = {
    "no-fence-at-all": "no fence here\n- an entry\n",
    "unterminated-fence": "---\ntopic: a thing\nstatus: active\n\n- an entry\n",
}


def write(ws: Path, text: str) -> Path:
    path = ws / MEMLOG
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("shape", sorted(BROKEN))
def test_append_refuses_a_broken_fence_with_a_line_and_exit_2(tmp_path, shape, capsys):
    path = write(tmp_path, BROKEN[shape])
    rc = memlog.main(["append", "--workspace", str(tmp_path), "--text", "hello"])
    assert rc == 2, f"{shape} was not refused"
    err = capsys.readouterr().err
    assert err.startswith("error: "), err
    assert str(path) in err, err
    assert "Traceback" not in err, err
    # And the refusal left the operator's file exactly as it found it.
    assert path.read_text(encoding="utf-8") == BROKEN[shape]


@pytest.mark.parametrize("shape", sorted(BROKEN))
def test_set_refuses_a_broken_fence_with_a_line_and_exit_2(tmp_path, shape, capsys):
    path = write(tmp_path, BROKEN[shape])
    rc = memlog.main(
        ["set", "--workspace", str(tmp_path), "--key", "status", "--value", "done"]
    )
    assert rc == 2, f"{shape} was not refused"
    err = capsys.readouterr().err
    assert err.startswith("error: "), err
    assert str(path) in err, err
    assert path.read_text(encoding="utf-8") == BROKEN[shape]


def test_the_two_malformations_are_actually_different_refusals():
    """Without this, one `BROKEN` entry could be a duplicate of the other."""
    messages = set()
    for text in BROKEN.values():
        with pytest.raises(ValueError) as caught:
            memlog.split(text)
        messages.add(str(caught.value))
    assert len(messages) == 2, messages


# --- anchors: a guard that refused every memlog would pass everything above ---

def test_anchor_append_still_works_on_a_well_formed_memlog(tmp_path, capsys):
    assert memlog.main(
        ["init", "--workspace", str(tmp_path), "--field", "topic=a thing"]
    ) == 0
    assert memlog.main(
        ["append", "--workspace", str(tmp_path), "--text", "hello"]
    ) == 0
    assert capsys.readouterr().err == ""
    assert "- hello" in (tmp_path / MEMLOG).read_text(encoding="utf-8")


def test_anchor_set_still_works_on_a_well_formed_memlog(tmp_path):
    assert memlog.main(
        ["init", "--workspace", str(tmp_path), "--field", "topic=a thing"]
    ) == 0
    assert memlog.main(
        ["set", "--workspace", str(tmp_path), "--key", "status", "--value", "done"]
    ) == 0
    meta, _ = memlog.split((tmp_path / MEMLOG).read_text(encoding="utf-8"))
    assert meta["status"] == "done"


def test_anchor_the_missing_half_of_the_finding_is_still_refused(tmp_path, capsys):
    """The half that was already fixed, kept measured beside the new one."""
    rc = memlog.main(["append", "--workspace", str(tmp_path), "--text", "hello"])
    assert rc == 2
    assert "run init first" in capsys.readouterr().err
