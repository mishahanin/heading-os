"""Shard scripts-utils-01-p3: the memory index, and four tools that reported a
number they had not measured.

* ``memory_expiry.strip_index_pointers`` removed the whole MEMORY.md LINE a match
  sat on. The index groups related memories onto one line by design - 37 lines of
  the live index carry more than one pointer - so retiring one dated memory
  deleted the index entry of every memory beside it. Measured 2026-08-25: a
  three-pointer line, one name passed in, the line gone. The operator's standing
  rule is that nothing leaves that index without him saying so.

* ``memory_stores.retire_memory`` swallowed a failed unlink, so a memory still on
  disk was reported as retired, logged as retired, and had its index pointer
  stripped - leaving an orphan the newest-wins reconcile copies back.

* ``memory_health.compute_memory_defects`` skipped the orphan check entirely when
  MEMORY.md was absent and returned ``orphans: []`` under ``status: "ok"``. That
  is the state where EVERY fact file is unreferenced, reported as none.

* ``impeccable_engine.relative_path`` used ``lstrip("./")``, a CHARACTER SET, so
  ``.git/x.html`` became ``git/x.html``: the configured ``/.git/`` out-of-scope
  fragment could never match, and the detail command the report printed named a
  path that does not resolve.

* ``impeccable_engine.report_for_artifact`` treated a non-fatal profile-config
  warning as "nothing was measured" and returned 0, throwing away findings it had
  already computed - in a module whose docstring promises every failure path
  degrades toward reporting MORE.

* ``impeccable_engine.record_baseline`` wrote the ratchet file non-atomically, and
  ``load_baseline`` reads a truncated one as an empty freeze.

* ``html_text.strip_html`` emitted no separator at block boundaries, so
  ``<div>Hello</div><div>World</div>`` became ``HelloWorld`` in the plaintext the
  mail daemons hand to a model.

Run: python3 -m pytest tests/test_a_retirement_that_took_the_neighbours_with_it.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import impeccable_engine as ie  # noqa: E402
from scripts.utils import memory_stores  # noqa: E402
from scripts.utils.html_text import strip_html  # noqa: E402
from scripts.utils.memory_expiry import strip_index_pointers  # noqa: E402
from scripts.utils.memory_health import compute_memory_defects  # noqa: E402


# ============================================================
# The retirement that took the neighbours with it
# ============================================================

_GROUP = "- Memory: [a](a.md) · [b](b.md) · [c](c.md)\n"


@pytest.mark.parametrize("name,expected", [
    ("b.md", "- Memory: [a](a.md) · [c](c.md)\n"),
    ("a.md", "- Memory: [b](b.md) · [c](c.md)\n"),
    ("c.md", "- Memory: [a](a.md) · [b](b.md)\n"),
])
def test_only_the_named_pointer_leaves_the_line(name, expected):
    """Every neighbour on the line survives, wherever the match sat."""
    assert strip_index_pointers(_GROUP, [name]) == expected


def test_two_of_three_leaves_the_third():
    assert strip_index_pointers(_GROUP, ["a.md", "c.md"]) == "- Memory: [b](b.md)\n"


def test_a_line_whose_every_pointer_matched_is_removed():
    """Otherwise a bare group label would be left behind pointing at nothing."""
    assert strip_index_pointers("- Memory: [a](a.md) · [b](b.md)\n",
                                ["a.md", "b.md"]) == ""


def test_a_single_pointer_line_still_goes_whole():
    assert strip_index_pointers("- [Solo](solo.md) - a hook\n", ["solo.md"]) == ""


def test_a_trailing_note_leaves_with_its_own_pointer():
    assert strip_index_pointers("- G: [a](a.md) - why · [b](b.md) - because\n",
                                ["a.md"]) == "- G: [b](b.md) - because\n"


def test_an_unmatched_line_passes_through_byte_for_byte():
    assert strip_index_pointers(_GROUP, ["zzz.md"]) == _GROUP


def test_a_managed_thread_path_is_not_hit_by_a_bare_name():
    line = "- [T](threads/business/drop.md) - active\n"
    assert strip_index_pointers(line, ["drop.md"]) == line


def test_the_separators_survive_the_removal():
    """The pointer pattern eats the space before a separator; it is put back."""
    out = strip_index_pointers(_GROUP, ["b.md"])
    assert " · " in out
    assert ")·" not in out
    assert not out.rstrip("\n").endswith("·")


def test_the_real_index_loses_exactly_the_pointers_it_should():
    """Read-only against the operator's own index, which is the thing at risk.

    Both expectations are now DERIVED from the file rather than assumed about
    it. It used to assert a delta of exactly 1 and an unchanged newline count,
    which is two claims about content nobody checked:

      * a pointer that appears on two lines is correctly removed twice, and the
        delta is 2;
      * a line whose ONLY pointer is the target is correctly removed whole - the
        behaviour `test_a_single_pointer_line_still_goes_whole` in this same
        file mandates - and the newline count drops.

    Either shape turned this red against correct code, on the operator's live
    index, where nobody can edit the fixture to make it green.
    """
    index = ROOT.parent / ".heading-os-data" / "auto-memory" / "MEMORY.md"
    if not index.is_file():
        pytest.skip("no data overlay on this clone")
    before = index.read_text(encoding="utf-8")
    target = "memory-auto-retire-expires-field.md"
    if f"]({target})" not in before:
        pytest.skip("the sample pointer is no longer in the index")

    pointer_re = re.compile(r"\]\([^)]+\)")
    occurrences = before.count(f"]({target})")
    # A line goes whole only when every pointer on it is the target.
    lines_removed = sum(
        1 for line in before.splitlines()
        if f"]({target})" in line
        and len(pointer_re.findall(line)) == line.count(f"]({target})")
    )

    after = strip_index_pointers(before, [target])
    lost = len(pointer_re.findall(before)) - len(pointer_re.findall(after))
    assert lost == occurrences, (
        f"expected {occurrences} pointer(s) to go and {lost} did; a neighbour's "
        f"pointer was taken too")
    assert before.count("\n") - after.count("\n") == lines_removed, (
        f"{lines_removed} line(s) carried only the target and should go whole")
    assert f"]({target})" not in after


# ============================================================
# The delete that did not stick and was called retired
# ============================================================

def test_a_failed_unlink_is_returned_not_swallowed(tmp_path, capsys,
                                                   monkeypatch):
    """The branch, injected at the call rather than at the directory mode.

    It used to `chmod(0o500)` the store and rely on the kernel to refuse the
    unlink. That is not a refusal every runner gets: a process holding
    CAP_DAC_OVERRIDE - root, which is the default in most CI containers -
    deletes the file anyway, `removed` comes back non-empty, and the assertion
    goes red against correct production code. On Windows the read-only
    attribute on a directory does not stop a delete inside it either. A test
    that fails for the operator's own fix pressures somebody into deleting one
    of the two, and this is the guard over the retired-but-still-on-disk
    orphan.

    Raising from `unlink` reaches the same `except OSError` on every platform
    and every user. The integration form is kept below, where the refusal is
    measured before it is relied on.
    """
    store = tmp_path / "store"
    store.mkdir()
    doomed = store / "doomed.md"
    doomed.write_text("x", encoding="utf-8")

    real_unlink = Path.unlink

    def _refuse(self, *args, **kwargs):
        if self == doomed:
            raise PermissionError(13, "Permission denied")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _refuse)
    removed, failed = memory_stores.retire_memory("doomed.md", stores=[store])

    assert removed == []
    assert [Path(p).name for p, _why in failed] == ["doomed.md"]
    assert doomed.exists(), "the file was reported gone and is here"
    assert "could not retire" in capsys.readouterr().err


def test_a_read_only_store_is_reported_the_same_way(tmp_path, capsys):
    """The same branch through a real permission bit, when the host has one.

    The skip condition is MEASURED, not asserted: the mode is applied and an
    unlink is actually attempted on a throwaway file. Whether this process can
    override the mode is a fact about the runner, and `os.geteuid() == 0` is
    only a proxy for it.
    """
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    probe = probe_dir / "canary.md"
    probe.write_text("x", encoding="utf-8")
    probe_dir.chmod(0o500)
    try:
        probe.unlink()
        enforced = False
    except OSError:
        enforced = True
    finally:
        probe_dir.chmod(0o700)
    if not enforced:
        pytest.skip("this process deletes through mode 0o500; measured, not assumed")

    store = tmp_path / "store"
    store.mkdir()
    (store / "doomed.md").write_text("x", encoding="utf-8")
    store.chmod(0o500)
    try:
        removed, failed = memory_stores.retire_memory("doomed.md", stores=[store])
    finally:
        store.chmod(0o700)

    assert removed == []
    assert [Path(p).name for p, _why in failed] == ["doomed.md"]
    assert (store / "doomed.md").exists()
    assert "could not retire" in capsys.readouterr().err


def test_a_store_that_never_held_the_memory_reports_neither_way(tmp_path):
    """The same lie, pointing the other way, and a surviving mutation until now.

    `retire_memory` is documented idempotent and missing-safe. A version that
    appended to `removed` on the absent branch passed every test in this file:
    the caller would then write "retired" to its audit log and strip the
    MEMORY.md pointer for a store that never carried the file.
    """
    store = tmp_path / "store"
    store.mkdir()
    removed, failed = memory_stores.retire_memory("never-existed.md",
                                                  stores=[store])
    assert removed == []
    assert failed == []


def test_a_successful_retirement_reports_no_failures(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    (store / "gone.md").write_text("x", encoding="utf-8")
    removed, failed = memory_stores.retire_memory("gone.md", stores=[store])
    assert failed == []
    assert removed == [str(store / "gone.md")]


def test_the_index_pointer_is_kept_when_the_file_was_not_removed():
    """The caller must not strip a pointer for a name still on disk."""
    source = (ROOT / "scripts" / "memory-auto-retire.py").read_text(encoding="utf-8")
    assert "NOT retired" in source
    # The message is built across f-string lines; match a contiguous fragment.
    assert "index pointer is left in place" in source
    body = source[source.index("for name, exp in expired:"):]
    assert body.index("if failed:") < body.index("names.append(name)")


# ============================================================
# The orphan count taken against an index nobody read
# ============================================================

def test_an_absent_index_makes_every_fact_file_an_orphan(tmp_path):
    """It reported none - over the state where nothing is referenced at all."""
    for name in ("a.md", "b.md", "c.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")

    result = compute_memory_defects(tmp_path)

    assert sorted(result["orphans"]) == ["a.md", "b.md", "c.md"]
    assert result["index_readable"] is False
    assert "does not exist" in result["index_problem"]


def test_a_present_index_counts_only_what_it_omits(tmp_path):
    for name in ("a.md", "b.md", "c.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("- [a](a.md) · [b](b.md)\n", encoding="utf-8")

    result = compute_memory_defects(tmp_path)

    assert result["orphans"] == ["c.md"]
    assert result["index_readable"] is True
    assert result["index_problem"] == ""


def test_both_consumers_surface_the_unread_index():
    for rel in ("scripts/memory-hygiene.py", "scripts/prime-health-parallel.py"):
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert "index_readable" in source, f"{rel} drops the index state"
        assert "NOT read" in source, f"{rel} does not say the index went unread"


# ============================================================
# The path whose leading dot was eaten
# ============================================================

@pytest.mark.parametrize("rel", [".git/x.html", ".claude/hooks/a.html",
                                 ".venv/lib/a.html", "docs/a.html"])
def test_a_dot_directory_keeps_its_dot(rel):
    """`lstrip` takes a character set; the first component lost its dot."""
    assert ie.relative_path(str(ROOT / rel)) == rel


def test_a_leading_dot_slash_is_still_removed():
    """A relative input on purpose, and it is not a hidden cwd parameter.

    An audit reported this expectation as true only from the repository root.
    MEASURED 2026-08-30 from `/tmp`: it passes. `relative_path` resolves
    nothing - it is `str(...)`, a separator swap, a `removeprefix` of the root
    when the text already starts with it, and `removeprefix("./")`. No
    `Path.resolve`, no `os.getcwd`, so the process directory cannot reach it.
    Recorded so the claim is not chased a third time.
    """
    assert ie.relative_path("./docs/b.html") == "docs/b.html"


def test_the_configured_out_of_scope_fragment_can_match():
    """`/.git/` is listed in config/visual-check-profiles.json and was dead."""
    config = ROOT / "config" / "visual-check-profiles.json"
    if not config.is_file():
        pytest.skip("no visual-check profile config in this clone")
    fragments = json.loads(config.read_text(encoding="utf-8")).get(
        "out_of_scope", {}).get("path_fragments", [])
    if "/.git/" not in fragments:
        pytest.skip("the /.git/ fragment is no longer configured")
    assert ".git/" in ie.relative_path(str(ROOT / ".git" / "x.html"))


# ============================================================
# The findings a config warning threw away
# ============================================================

def test_a_config_warning_no_longer_silences_the_findings(monkeypatch, capsys):
    """The module promises every failure path reports MORE, never less."""
    monkeypatch.setattr(
        ie, "deep_findings",
        lambda path, profile_override=None: (
            [{"type": "impeccable:contrast", "detail": "x"}],
            "profile config unreadable (boom); falling back to screen",
        ))
    count = ie.report_for_artifact(Path("artifact.html"), stream=sys.stdout)
    out = capsys.readouterr().out
    assert count == 1, "a computed finding was discarded over a config warning"
    assert "profile config unreadable" in out, "the warning must still be said"
    assert "1 finding(s)" in out


def test_a_config_warning_with_nothing_found_still_reports_clean(monkeypatch, capsys):
    monkeypatch.setattr(
        ie, "deep_findings",
        lambda path, profile_override=None: ([], "profile config unreadable (boom)"))
    assert ie.report_for_artifact(Path("artifact.html"), stream=sys.stdout) == 0
    out = capsys.readouterr().out
    assert "profile config unreadable" in out
    assert "clean" in out


def test_a_detector_that_raises_is_still_a_zero(monkeypatch, capsys):
    def _boom(path, profile_override=None):
        raise RuntimeError("node missing")

    monkeypatch.setattr(ie, "deep_findings", _boom)
    assert ie.report_for_artifact(Path("artifact.html"), stream=sys.stdout) == 0
    assert "check unavailable" in capsys.readouterr().out


# ============================================================
# The ratchet written without a tempfile
# ============================================================

def test_the_baseline_is_written_atomically():
    source = (ROOT / "scripts" / "utils" / "impeccable_engine.py").read_text(
        encoding="utf-8")
    assert "atomic_write_text(path," in source
    live = [ln for ln in source.splitlines()
            if "path.write_text(" in ln and not ln.lstrip().startswith("#")]
    assert live == [], "the ratchet state is written non-atomically again"


def test_the_baseline_round_trips(tmp_path, monkeypatch):
    target = tmp_path / ".visual-baseline.json"
    findings = [{"file": str(ROOT / "docs" / "a.html"), "type": "impeccable:contrast"}]
    ie.record_baseline(findings, path=target)
    assert target.is_file()
    assert "docs/a.html" in json.loads(target.read_text(encoding="utf-8"))["files"]


# ============================================================
# The block boundaries that fused two words
# ============================================================

@pytest.mark.parametrize("html,expected", [
    ("<div>Hello</div><div>World</div>", "Hello\nWorld"),
    ("<p>First.</p><p>Second.</p>", "First.\nSecond."),
    ("Line one<br>Line two", "Line one\nLine two"),
    ("<li>one</li><li>two</li>", "one\ntwo"),
    ("<tr><td>a</td><td>b</td></tr>", "a\nb"),
])
def test_a_block_boundary_ends_the_line(html, expected):
    assert strip_html(html) == expected


@pytest.mark.parametrize("html,expected", [
    ("<p>A</p>tail", "A\ntail"),
    ("<div>body</div>signature line", "body\nsignature line"),
    ("<li>item</li>after", "item\nafter"),
])
def test_a_block_that_CLOSES_before_loose_text_still_breaks(html, expected):
    """The end tag carries its own break, and only this shape proves it.

    Between two adjacent blocks the NEXT block's start tag already breaks the
    line, so dropping the end-tag break changes nothing there. It shows only
    where a block closes and loose text follows - which is exactly the shape a
    mail signature takes after the last `</div>`.
    """
    assert strip_html(html) == expected


@pytest.mark.parametrize("html,expected", [
    ("Line one<br/>Line two", "Line one\nLine two"),
    ("Line one<br />Line two", "Line one\nLine two"),
    ("<p>A</p><hr/><p>B</p>", "A\nB"),
])
def test_a_self_closing_block_tag_breaks_the_line_too(html, expected):
    """The XHTML spelling reaches a DIFFERENT handler, and nothing measured it.

    `HTMLParser` routes a bare `<br>` to `handle_starttag` and a self-closed
    `<br/>` to `handle_startendtag`. `_HTMLStripper` overrides all three, so the
    self-closed spelling is not covered by the `<br>` case above: measured
    2026-09-01, gutting `handle_startendtag` to `return None` left every test in
    this file green, and left the whole 607-test corpus of every `html_text`
    consumer in `tests/` green too (28 failed / 579 passed, byte-identical to the
    unmutated baseline). Because the override SHADOWS `HTMLParser`'s default -
    which would otherwise have called `handle_starttag` and `handle_endtag` - a
    regression there produces no break at all, and `Line one<br/>Line two` fuses
    back to `Line oneLine two`. XHTML-serialized mail is the common case for the
    senders that emit `<br />`, and this is the plaintext three mail daemons hand
    to a model.
    """
    assert strip_html(html) == expected


@pytest.mark.parametrize("html,expected", [
    ("keep <b>bold</b> inline", "keep bold inline"),
    ('an <a href="#">anchor</a> too', "an anchor too"),
    ("<span>a</span><span>b</span>", "ab"),
])
def test_an_inline_tag_still_runs_on(html, expected):
    assert strip_html(html) == expected


def test_the_newline_collapse_still_applies():
    assert strip_html("<p>A</p>\n\n\n\n<p>B</p>") == "A\n\nB"


def test_style_and_script_are_still_dropped():
    out = strip_html("<style>.x{}</style><p>Body</p><script>evil()</script>")
    assert out == "Body"


def test_empty_input_is_still_empty():
    assert strip_html("") == ""
    assert strip_html(None) == ""
