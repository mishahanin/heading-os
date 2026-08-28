"""Six detectors that stand on a gate, and what each of them could not see.

Every file audited here prints a verdict another tool or a human reads as
coverage. `.claude/rules/scope-claims.md` governs exactly that sentence: a tool
says only what its method established. Each section below is one place where the
method established less than the sentence claimed.

  - `content_denylist.scan_text` matched a two-word real name only within one
    line, so an ordinary hard wrap hid it from BOTH the pre-commit hook and the
    unbypassable push wall. The engine repo is public.
  - `sanitize_text.INVISIBLE_CHARS` carried the four bidi isolates and labelled
    them "(Trojan Source)", and none of the five bidi overrides - including
    U+202E, the character CVE-2021-42574 is named for. `scripts/marp_render.py`
    already strips all five.
  - `content-guard.py` set `data_root = None` on one branch and dereferenced it
    on the next, so the graceful-degradation path it wrote for itself could
    never be taken.
  - `humanization-check.SENTENCE_BOUNDARY` split only before a capital, so the
    same prose measured as three sentences or one depending on letter case.
  - `sanitize-text.py` counted deleted characters only, so a file whose
    non-breaking spaces it had just replaced was reported "already clean".
  - `ste-check.py` read every page unguarded, so an unreadable one exited 1
    ("findings present") instead of 2 ("script error"), and aborted an --all run
    without naming the pages it never reached.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import sanitize_text as ST  # noqa: E402
from scripts.utils.content_denylist import build_denylist  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# The denylist sees a name the way prose actually writes it
# ============================================================

@pytest.fixture
def overlay(tmp_path):
    """A synthetic DATA overlay. Every name here is invented."""
    contacts = tmp_path / "crm" / "contacts"
    contacts.mkdir(parents=True)
    (contacts / "ivor-brandvik.md").write_text(
        "---\nname: Ivor Brandvik\ncompany: Krellide Technologies\n---\n",
        encoding="utf-8",
    )
    return tmp_path


def test_the_two_word_name_is_found_on_one_line(overlay):
    """Anchor: every case below is vacuous if the flat form stopped matching.

    Exactly once. The loose pass matches a single space too, so without the
    deduplication against the line-scoped pass every flat name is reported twice
    and the operator reads one leak as two.
    """
    dl = build_denylist(str(overlay))
    hits = dl.scan_text("We met Ivor Brandvik today.")
    assert len(hits) == 1, hits


@pytest.mark.parametrize("gap, label", [
    ("\n", "a hard wrap"),
    ("  ", "a double space"),
    ("\u00a0", "a non-breaking space"),
    ("\n    ", "a wrap into an indented continuation"),
])
def test_the_two_word_name_is_found_across_whitespace(overlay, gap, label):
    """Prose in this tree is hard-wrapped. A name split by the wrap is the
    ordinary case, not the exotic one, and it was the invisible one.
    """
    dl = build_denylist(str(overlay))
    hits = dl.scan_text(f"We met Ivor{gap}Brandvik today.")
    assert hits, f"invisible across {label}"


def test_a_wrapped_hit_reports_the_line_the_name_starts_on(overlay):
    """A hit with the wrong line number sends the operator to the wrong place."""
    dl = build_denylist(str(overlay))
    text = "first line\nsecond line\nWe met Ivor\nBrandvik today.\n"
    hits = dl.scan_text(text)
    assert hits, "not found at all"
    assert hits[0][0] == 3, f"reported line {hits[0][0]}, the name starts on 3"


def test_a_name_split_across_a_paragraph_break_is_not_a_name(overlay):
    """The join must stop at a blank line. Two paragraphs whose last and first
    words happen to be a first and last name are not an occurrence, and a guard
    that says they are gets suppressed everywhere.
    """
    dl = build_denylist(str(overlay))
    assert not dl.scan_text("a line ending in Ivor\n\nBrandvik opens the next.")


def test_the_suppression_comment_still_covers_a_wrapped_hit(overlay):
    """`content-guard: ok` is a per-line marker. A hit that spans two lines is
    suppressed when either line carries it, or the marker becomes unusable for
    exactly the hits this change made visible.
    """
    dl = build_denylist(str(overlay))
    text = "We met Ivor\nBrandvik today.  # content-guard: ok invented"
    assert not dl.scan_text(text), "the marker on the second line did not cover it"
    text = "We met Ivor  # content-guard: ok invented\nBrandvik today."
    assert not dl.scan_text(text), "the marker on the first line did not cover it"


def test_the_loose_pass_carries_only_multi_word_tokens(overlay):
    """A single-word token widened the same way is the same expression, so this
    reads the compiled pattern rather than a behaviour that cannot differ.

    It still matters: the alternation is rebuilt on every harvest, and a future
    edit that feeds it every token turns one pattern into two copies of itself,
    doubling the scan for no gain.
    """
    dl = build_denylist(str(overlay))
    assert [t for t in dl.tokens if " " not in t], (
        "no single-word token in the fixture; the check is vacuous"
    )
    assert dl._loose_pattern is not None
    body = dl._loose_pattern.pattern.split("(?:", 1)[1].rsplit(")", 2)[0]
    branches = body.split("|")
    assert branches, body
    # Every branch, not "is the token absent": a single-word token is a
    # SUBSTRING of the multi-word branch that starts with it, so a containment
    # check passes over the defect either way.
    assert all(r"\s+" in b for b in branches), branches


# ============================================================
# The sanitizer knows the whole Trojan Source family
# ============================================================

BIDI_OVERRIDES = {
    0x202A: "left-to-right embedding",
    0x202B: "right-to-left embedding",
    0x202C: "pop directional formatting",
    0x202D: "left-to-right override",
    0x202E: "right-to-left override",
}


@pytest.mark.parametrize("cp", sorted(BIDI_OVERRIDES))
def test_every_bidi_override_is_stripped(cp):
    """U+202E is the character CVE-2021-42574 is named for. The table carried
    the four isolates, labelled them "(Trojan Source)", and stopped there.
    """
    assert chr(cp) in ST.INVISIBLE_CHARS, f"U+{cp:04X} not stripped"
    assert ST.sanitize(f"a{chr(cp)}b") == "ab"


@pytest.mark.parametrize("cp", sorted(BIDI_OVERRIDES))
def test_every_bidi_override_is_scanned_and_named(cp):
    """A character `sanitize` removes but `scan` cannot see makes the two halves
    of one tool disagree, and the scan is the half the rule quotes.
    """
    assert chr(cp) in ST.SCANNED_CHARS, f"U+{cp:04X} not scanned"
    name = ST.CHAR_NAMES.get(chr(cp), "")
    assert "Trojan Source" in name, (
        f"U+{cp:04X} is named {name!r}; the label is what tells the reader why "
        f"this finding matters, and the isolates already carry it"
    )


def test_the_sanitizer_covers_every_character_its_deck_sibling_strips():
    """The detector, not the five cases above. `scripts/marp_render.py` carries
    its own table for the same purpose; whichever grows first, the canonical
    sanitizer must not be the shorter one again.
    """
    marp = _load("_d43_marp", "scripts/marp_render.py")
    table = getattr(marp, "INVISIBLE", None)
    if table is None:
        src = (ROOT / "scripts/marp_render.py").read_text(encoding="utf-8")
        found = {chr(int(m, 16)) for m in
                 __import__("re").findall(r'"\\u([0-9a-fA-F]{4})"\s*:', src)}
    else:
        found = set(table)
    assert found, "the sibling table was not located; this detector is vacuous"
    missing = sorted(f"U+{ord(c):04X}" for c in found
                     if c not in ST.SCANNED_CHARS and c != "\u00a0")
    assert not missing, f"the deck renderer strips what the sanitizer misses: {missing}"


# ============================================================
# content-guard survives the None it makes for itself
# ============================================================

def _guard_with_unresolvable_root(tmp_path):
    """`HEADING_OS_DATA` naming a path that does not exist makes
    `get_data_root()` raise `DataRootError`, which is the ONLY way to reach the
    `data_root = None` branch. It is also the ordinary operator accident: a
    renamed overlay, an unmounted drive, the wrong shell.

    `--data-root` does NOT reach it: that argument builds a Path whatever the
    string says, so `.is_dir()` answers False instead of raising. The first
    version of this test used it and passed over the defect.
    """
    page = tmp_path / "a.md"
    page.write_text("nothing to see\n", encoding="utf-8")
    import os
    env = dict(os.environ, HEADING_OS_DATA=str(tmp_path / "no-such-overlay"))
    return subprocess.run(
        [PY, str(ROOT / "scripts/content-guard.py"), "--files", str(page)],
        capture_output=True, text=True, cwd=ROOT, timeout=120, env=env,
    )


def test_the_unresolvable_overlay_is_a_quiet_no_op(tmp_path):
    """The `except` clause sets data_root = None so the gate can degrade. The
    degraded branch then dereferenced that None, so the path written to make it
    graceful was the one that crashed.
    """
    proc = _guard_with_unresolvable_root(tmp_path)
    assert "Traceback" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, proc.stderr


def test_the_unresolvable_overlay_names_the_state_it_found(tmp_path):
    """The message exists to name the state rather than guess a cause, and an
    unresolvable root is a different state from an overlay that is simply not
    there. Reporting the second for the first is the guess the comment forbids.
    """
    proc = _guard_with_unresolvable_root(tmp_path)
    out = proc.stdout + proc.stderr
    assert "skipped" in out.lower(), out
    assert "could not be resolved" in out, out


# ============================================================
# The sentence splitter does not decide on letter case
# ============================================================

def test_a_lowercase_opener_still_starts_a_sentence():
    """A sentence opening with a command name, a path or a slash command was
    merged into its predecessor, so measured sentences grew and the length and
    burstiness checks stopped firing.
    """
    hc = _load("_d43_hc", "scripts/humanization-check.py")
    caps = "One two three. Four five six. Seven eight nine."
    lower = "One two three. four five six. seven eight nine."
    assert len(hc.SENTENCE_BOUNDARY.split(caps)) == 3
    assert len(hc.SENTENCE_BOUNDARY.split(lower)) == 3, (
        "the same prose measured differently on letter case alone"
    )


def test_a_decimal_number_is_not_a_sentence_boundary():
    """The split must not fire inside `3.5` or a version string, or every
    numeric paragraph becomes a pile of two-word sentences.
    """
    hc = _load("_d43_hc2", "scripts/humanization-check.py")
    assert len(hc.SENTENCE_BOUNDARY.split("It grew 3.5 times in one quarter.")) == 1


@pytest.mark.parametrize("text", [
    "Send it to e.g. the London office.",
    "Take the logs, the notes, etc. then close the thread.",
    "Compare it vs. the previous quarter.",
])
def test_an_abbreviation_does_not_end_a_sentence(text):
    """Two separate rules: a single letter between dots covers `e.g.`, and the
    word list covers `etc.` and `vs.`. Dropping either one splits mid-sentence.
    """
    hc = _load("_d43_hc3", "scripts/humanization-check.py")
    assert len(hc.SENTENCE_BOUNDARY.split(text)) == 1, text


# ============================================================
# sanitize-text reports the file it changed
# ============================================================

def test_a_replaced_character_is_reported_not_called_clean(tmp_path):
    """`removed = len(text) - len(clean)` counts deletions only, and a
    non-breaking space is REPLACED by a space, which preserves length. The file
    was rewritten and the report said "already clean".
    """
    page = tmp_path / "a.md"
    page.write_text("one\u00a0two\n", encoding="utf-8")
    proc = subprocess.run(
        [PY, str(ROOT / "scripts/sanitize-text.py"), str(page)],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    out = proc.stdout + proc.stderr
    assert page.read_text(encoding="utf-8") == "one two\n", "the file was not fixed"
    assert "already clean" not in out, f"reported clean over a rewrite: {out!r}"
    assert "replaced 1" in out, (
        f"the count of what changed is not reported: {out!r}"
    )


def test_a_genuinely_clean_file_is_still_called_clean(tmp_path):
    """Anchor: the test above passes trivially if nothing is ever clean."""
    page = tmp_path / "a.md"
    page.write_text("one two\n", encoding="utf-8")
    proc = subprocess.run(
        [PY, str(ROOT / "scripts/sanitize-text.py"), str(page)],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    assert "already clean" in proc.stdout + proc.stderr


def test_a_removed_character_is_still_reported(tmp_path):
    page = tmp_path / "a.md"
    page.write_text("one\u200btwo\n", encoding="utf-8")
    proc = subprocess.run(
        [PY, str(ROOT / "scripts/sanitize-text.py"), str(page)],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    assert page.read_text(encoding="utf-8") == "onetwo\n"
    assert "already clean" not in proc.stdout + proc.stderr


# ============================================================
# ste-check tells a read failure from a style finding
# ============================================================

def _ste(*argv, cwd=None):
    return subprocess.run(
        [PY, str(ROOT / "scripts/ste-check.py"), *argv],
        capture_output=True, text=True, cwd=cwd or ROOT, timeout=180,
    )


def test_an_undecodable_page_is_a_script_error_not_a_finding(tmp_path):
    """The docstring defines 1 as "findings present" and 2 as "script error".
    An unreadable page produced a traceback and exit 1, so a gate that tells the
    two apart attributed a crash to writing style.
    """
    page = tmp_path / "bad.md"
    page.write_bytes(b"\xff\xfe not utf 8\n")
    proc = _ste(str(page))
    assert proc.returncode == 2, f"exit {proc.returncode}: {proc.stderr[-400:]}"
    assert "Traceback" not in proc.stderr, proc.stderr


def test_the_read_failure_names_the_page(tmp_path):
    page = tmp_path / "bad.md"
    page.write_bytes(b"\xff\xfe not utf 8\n")
    proc = _ste(str(page))
    assert "bad.md" in proc.stdout + proc.stderr


def test_a_clean_page_exits_zero(tmp_path):
    """Anchor for the gate: exit 0 has to be reachable."""
    page = tmp_path / "ok.md"
    page.write_text("# Title\n\nOpen the file. Read the value.\n", encoding="utf-8")
    assert _ste(str(page)).returncode == 0


def test_a_sweep_names_the_pages_it_could_not_read(tmp_path, monkeypatch, capsys):
    """`--all` used to die on the first unreadable page, so every page after it
    went unread with nothing saying so. Reading fewer pages than asked and
    reporting like a full sweep is the silent truncation the scope-claims rule
    forbids, so the run names them and exits 2 rather than 0 or 1.
    """
    ste = _load("_d43_ste", "scripts/ste-check.py")
    good = tmp_path / "good.md"
    good.write_text("# Title\n\nOpen the file. Read the value.\n", encoding="utf-8")
    bad = tmp_path / "unreadable.md"
    bad.write_bytes(b"\xff\xfe not utf 8\n")

    monkeypatch.setattr(ste, "resolve_scope", lambda: [bad, good])
    monkeypatch.setattr(sys, "argv", ["ste-check.py", "--all", "--quiet"])
    with pytest.raises(SystemExit) as excinfo:
        ste.main()

    out = capsys.readouterr()
    combined = out.out + out.err
    assert excinfo.value.code == 2, f"exit {excinfo.value.code}"
    assert "unreadable.md" in combined, combined
    assert "NOT" in combined, "the sweep does not say the page went unchecked"


def test_a_sweep_still_checks_the_pages_it_could_read(tmp_path, monkeypatch, capsys):
    """Anchor: naming the failure must not become skipping the work."""
    ste = _load("_d43_ste2", "scripts/ste-check.py")
    good = tmp_path / "good.md"
    good.write_text("# Title\n\nOpen the file. Read the value.\n", encoding="utf-8")

    monkeypatch.setattr(ste, "resolve_scope", lambda: [good])
    monkeypatch.setattr(sys, "argv", ["ste-check.py", "--all", "--quiet"])
    with pytest.raises(SystemExit) as excinfo:
        ste.main()
    assert excinfo.value.code == 0


def test_a_page_with_an_error_fails_the_gate(tmp_path):
    """The property that makes this a gate, and nothing executed it: the tests
    asserted the pre-commit `entry:` string and the CI YAML, never the exit code.
    """
    page = tmp_path / "bad-style.md"
    page.write_text(
        "# Title\n\n"
        "The configuration of the system was subsequently utilised by the "
        "operator in order to facilitate the comprehensive initialisation of "
        "the aforementioned subsystem and its numerous dependent components.\n",
        encoding="utf-8",
    )
    proc = _ste(str(page))
    assert proc.returncode == 1, f"exit {proc.returncode}: {proc.stdout[-500:]}"
