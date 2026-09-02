"""A task key built from a 64-character prefix was not an identifier.

`_task_key` was `'{captured}|{priority}|{description[:64]}'`. Two tasks captured
on the same day at the same priority whose descriptions agree for 64 characters
therefore produced ONE key, and the done log is keyed by exactly that string. So
marking either one done hid BOTH rows from /tasks, and `done_filtered` reported
two rows suppressed for one action. Nothing logged it; the second task simply
stopped being shown.

On-disk compatibility, which is why the fix is conditional rather than uniform:
the key is persisted verbatim as the `task_key` field of every line in
`outputs/operations/viraid/_done-log.jsonl`, and matching is string equality
against a key recomputed from tasks.md. A key that changes re-surfaces an
already-done row. Descriptions of 64 characters or fewer therefore keep a
byte-identical key - the anchor below spells the legacy format out literally, so
a uniform "hash everything" rewrite fails here rather than in the operator's
listing a week later.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon.sources import tasks  # noqa: E402

N = tasks.TASK_KEY_DESC_PREFIX

# 64 shared characters, then a divergence. The shape the finding describes.
SHARED = "Follow up with the regional procurement office about the pilot c"
LONG_A = SHARED + "ontract for the northern region"
LONG_B = SHARED + "ontract for the southern region"


def test_the_fixture_really_is_the_colliding_shape():
    """If the prefixes ever stop matching, every assertion below is vacuous."""
    assert len(SHARED) == N
    assert LONG_A[:N] == LONG_B[:N]
    assert LONG_A != LONG_B


def test_two_descriptions_sharing_the_prefix_get_different_keys():
    """THE FINDING."""
    key_a = tasks._task_key("2026-09-01", "P2", LONG_A)
    key_b = tasks._task_key("2026-09-01", "P2", LONG_B)
    assert key_a != key_b, (
        "two distinct tasks resolve to one key; marking either done hides both"
    )


def test_a_short_description_keeps_the_exact_legacy_key():
    """ANCHOR, and the on-disk contract.

    Every done-log entry already written for a description this length must keep
    matching, or previously-done tasks re-surface as active on upgrade.
    """
    assert tasks._task_key("2026-09-01", "P1", "Call the auditor") == \
        "2026-09-01|P1|Call the auditor"


def test_a_description_of_exactly_the_prefix_length_keeps_the_legacy_key():
    """ANCHOR at the boundary: N is not truncation, N+1 is."""
    exactly = "x" * N
    assert tasks._task_key("2026-09-01", "P1", exactly) == f"2026-09-01|P1|{exactly}"
    assert tasks._task_key("2026-09-01", "P1", exactly + "y") != \
        f"2026-09-01|P1|{exactly}"


def test_the_key_is_still_stable_and_still_bounded():
    """ANCHOR. A key salted per call, or an unbounded one, would also pass above.

    Unbounded matters concretely: `mark_done` refuses a key over 500 characters,
    so a key carrying the whole description would make a long task un-markable.
    """
    assert tasks._task_key("2026-09-01", "P2", LONG_A) == \
        tasks._task_key("2026-09-01", "P2", LONG_A)
    assert tasks._task_key("2026-09-01", "P2", "y" * 5000) == \
        tasks._task_key("2026-09-01", "P2", "y" * 5000)
    assert len(tasks._task_key("2026-09-01", "P2", "y" * 5000)) < 200


def test_identical_rows_still_collapse_to_one_key():
    """ANCHOR. The key's whole job is to be the SAME across re-parses."""
    assert tasks._task_key("2026-09-01", "P2", "  Ship it  ") == \
        tasks._task_key("2026-09-01", "P2", "Ship it")


def _tasks_md(root: Path, rows: list[str]) -> None:
    path = root / "outputs" / "operations" / "viraid" / "tasks.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"- [ ] **2026-09-01** | `P2` | {r} | *Task*" for r in rows)
    path.write_text(f"## Active\n\n{body}\n", encoding="utf-8")


def test_marking_one_of_the_pair_done_hides_only_that_one(tmp_path):
    """End to end, through the surface the operator actually drives."""
    _tasks_md(tmp_path, [LONG_A, LONG_B])

    before = tasks.list_active_tasks(tmp_path)
    assert len(before["tasks"]) == 2, "the two rows did not both parse"
    key_a = next(t["task_key"] for t in before["tasks"]
                 if t["description"] == LONG_A)

    assert tasks.mark_done(tmp_path, key_a)["ok"] is True

    after = tasks.list_active_tasks(tmp_path)
    assert [t["description"] for t in after["tasks"]] == [LONG_B], (
        "marking one task done also hid its prefix-sharing sibling"
    )
    assert after["done_filtered"] == 1, (
        f"done_filtered={after['done_filtered']} for one mark-done: the count "
        f"double-reported because both rows matched one key"
    )


def test_the_undo_still_restores_exactly_that_row(tmp_path):
    """ANCHOR. A key change must not orphan the tombstone path."""
    _tasks_md(tmp_path, [LONG_A, LONG_B])
    key_a = next(t["task_key"] for t in tasks.list_active_tasks(tmp_path)["tasks"]
                 if t["description"] == LONG_A)

    tasks.mark_done(tmp_path, key_a)
    assert tasks.undo_done(tmp_path, key_a)["ok"] is True

    after = tasks.list_active_tasks(tmp_path)
    assert sorted(t["description"] for t in after["tasks"]) == sorted([LONG_A, LONG_B])
    assert after["done_filtered"] == 0


def test_the_recently_done_label_drops_the_digest(tmp_path):
    """The footer is a human label, so it shows the description, not the digest."""
    _tasks_md(tmp_path, [LONG_A])
    key_a = tasks.list_active_tasks(tmp_path)["tasks"][0]["task_key"]
    tasks.mark_done(tmp_path, key_a, note="done on the call")

    rows = tasks.done_log_recent(tmp_path)
    assert len(rows) == 1
    assert rows[0]["description"] == LONG_A[:N]
    assert rows[0]["priority"] == "P2"
    assert rows[0]["note"] == "done on the call"


@pytest.mark.parametrize("description", ["Short one", LONG_A])
def test_every_key_still_round_trips_through_the_done_log(tmp_path, description):
    """ANCHOR across both key shapes: what is written must be what is matched."""
    _tasks_md(tmp_path, [description])
    key = tasks.list_active_tasks(tmp_path)["tasks"][0]["task_key"]

    assert len(key) <= 500, "mark_done refuses a key this long"
    assert tasks.mark_done(tmp_path, key)["ok"] is True
    assert key in tasks.read_done_log(tmp_path)
    assert tasks.list_active_tasks(tmp_path)["tasks"] == []
