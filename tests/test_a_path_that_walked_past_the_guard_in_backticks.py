"""Shard scripts-utils-00-p1: three guards that were narrower than their words.

- `canopus_note._LEAK` is the wall between a private absolute path and a PUBLIC
  git repository. Its POSIX alternative fired only when the slash sat at the
  start of the string or after a space, so a path written the way this
  repository normally writes one -- in backticks, in parentheses, after a colon,
  as a markdown link target -- walked straight through, while the comment above
  it said the whole point was to match anywhere.
- `write_note` accepted a slug with a path separator and wrote a note that
  `note_paths` does not enumerate, so `scripts/canopus_check.py` would report
  "0 report(s)" over a note no clause ever opened.
- `alert._post_card` returned False in silence when the card channel was never
  wired, which is every process except the bridge daemon.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import alert  # noqa: E402
from scripts.utils.canopus_note import (  # noqa: E402
    NoteError, _LEAK, note_paths, read_note, write_note,
)

VALID = {
    "value": "the slice earns its keep",
    "approval_sha": "abc1234",
    "contract": "tests/test_example.py",
    "plan_digest": "sha256:" + "a" * 64,
    "scrutinize_plan": "clean",
    "scrutinize_built": "clean",
    "undo": "revert the commit",
}


def _fields(slug: str, **over) -> dict:
    return {**VALID, "slug": slug, **over}


# ============================================================
# The leak guard -- a path is a path wherever it is written
# ============================================================

@pytest.mark.parametrize("text", [
    "the plan `/home/operator/private/plan.md` was approved",
    "see (/home/operator/private/plan.md)",
    'quoted "/home/operator/a/b.md" here',
    "path:/home/operator/x/plan.md",
    "[the plan](/home/operator/user/x.md)",
    "plain /home/operator/x/plan.md mid sentence",
    "/leading.md",
    "'/etc/passwd'",
    "<" + "/srv/data/file.txt>",
])
def test_an_absolute_path_is_caught_wherever_it_sits(text):
    """Five of these nine passed before. This repository is public and the note
    is committed to it, so each one was a private path one commit from being
    published."""
    assert _LEAK.search(text), f"leak guard missed: {text!r}"


@pytest.mark.parametrize("text", [
    "~/plans/x.md",
    "back`~/plans/x.md`tick",
    "C:\\Users\\operator",
    "went ../up/one",
    "inside .heading-os-data somewhere",
    # Only the `~/` alternative catches these: the widened POSIX branch needs a
    # path segment after the slash, and neither of these has one. Without a case
    # of this shape, deleting `~/` from the pattern changes no test result --
    # the branch would look like dead weight and get removed by the next reader.
    "kept under ~/ somewhere",
    "it lives in ~/ and nowhere else",
])
def test_the_other_alternatives_still_fire(text):
    """These already matched anywhere. Widening the POSIX branch must not have
    disturbed them."""
    assert _LEAK.search(text)


@pytest.mark.parametrize("text", [
    "no path here at all",
    "a ratio 3/4 in prose",
    "and/or",
    "use the a/b test",
    "the 50/50 split",
    "TCP/IP",
    "read/write",
    "24/7 coverage",
    "see https://example.com/page for detail",
    # A URL with a port and a path, which is the shape that matters here. It is
    # deliberately NOT the CLIProxyAPI address: `tests/test_no_code_reaches_a_
    # model_provider_directly.py` reads any file carrying that host beside a
    # completion path as a second model client, and it is right to.
    "http://localhost:1234/api/list",
    "ftp://host/dir/file.txt",
])
def test_ordinary_prose_and_urls_are_not_paths(text):
    """The widening is only worth having if it does not refuse normal writing.
    A guard that fires on "24/7" gets switched off."""
    assert not _LEAK.search(text), f"false positive on: {text!r}"


def test_a_note_carrying_a_backticked_path_is_refused(tmp_path):
    """End to end through the public API, not just the regex."""
    with pytest.raises(NoteError, match="carries a path"):
        write_note(tmp_path, "demo",
                   _fields("demo", value="see the plan at `/home/operator/private/p.md`"))


def test_a_note_body_carrying_a_path_is_refused_too(tmp_path):
    """`body` is free prose and is validated like every other value."""
    with pytest.raises(NoteError, match="carries a path"):
        write_note(tmp_path, "demo",
                   _fields("demo", body="context: /home/operator/private/notes.md"))


def test_a_clean_note_still_writes(tmp_path):
    """The guard must not have become a wall."""
    path = write_note(tmp_path, "demo", _fields("demo"))
    assert path.exists()
    assert read_note(tmp_path, "demo")["slug"] == "demo"


# ============================================================
# The slug -- one file name, because one reader globs one level
# ============================================================

@pytest.mark.parametrize("slug", ["sub/hidden", "a/b/c", "win\\sub", ".", "..", ""])
def test_a_slug_that_is_a_path_is_refused(tmp_path, slug):
    """It used to be written and returned, and then be invisible to
    `note_paths` -- the entire population `canopus_check` iterates. The run
    would print "0 report(s)" and exit 0 over a note nothing had read."""
    with pytest.raises(NoteError, match="single file name|missing required"):
        write_note(tmp_path, slug, _fields(slug))


def test_every_written_note_is_enumerable(tmp_path):
    """The property the refusal exists to protect."""
    for slug in ("alpha", "beta", "gamma"):
        write_note(tmp_path, slug, _fields(slug))

    found = {p.stem for p in note_paths(tmp_path)}

    assert found == {"alpha", "beta", "gamma"}


# ============================================================
# The alert card channel -- "not wired" must not look like "fine"
# ============================================================

def test_an_unwired_card_channel_says_so(monkeypatch, caplog):
    """`alert.init` is called in exactly one process. Everywhere else the card
    channel documented in the module's routing table was never there, and the
    log did not distinguish that from a channel that failed."""
    monkeypatch.setattr(alert, "_aq_append_fn", None)
    monkeypatch.setattr(alert.telegram_notify, "notify", lambda *a, **k: False)

    with caplog.at_level(logging.WARNING, logger="x31c.alert"):
        fired = alert.alert("warning", "disk almost full", "detail", source="test")

    assert fired["card"] is False
    assert any("alert.init was never called" in r.getMessage() for r in caplog.records)


def test_a_wired_channel_that_raises_still_logs_its_own_reason(monkeypatch, caplog):
    """The sibling path already logged. Both must stay distinguishable."""
    def _boom(*a, **k):
        raise RuntimeError("queue file locked")

    monkeypatch.setattr(alert, "_aq_append_fn", _boom)
    monkeypatch.setattr(alert.telegram_notify, "notify", lambda *a, **k: False)

    with caplog.at_level(logging.WARNING, logger="x31c.alert"):
        fired = alert.alert("warning", "disk almost full", "detail", source="test")

    assert fired["card"] is False
    messages = [r.getMessage() for r in caplog.records]
    assert any("card append raised" in m for m in messages)
    assert not any("alert.init was never called" in m for m in messages)


def test_a_wired_channel_that_works_logs_neither(monkeypatch, caplog):
    """A working channel must stay quiet, or the warning becomes noise and gets
    filtered out by whoever reads the journal."""
    monkeypatch.setattr(alert, "_aq_append_fn",
                        lambda *a, **k: {"ok": True, "added": 1})
    monkeypatch.setattr(alert.telegram_notify, "notify", lambda *a, **k: False)

    with caplog.at_level(logging.WARNING, logger="x31c.alert"):
        fired = alert.alert("warning", "disk almost full", "detail", source="test")

    assert fired["card"] is True
    assert not any("alert.init was never called" in r.getMessage()
                   for r in caplog.records)
