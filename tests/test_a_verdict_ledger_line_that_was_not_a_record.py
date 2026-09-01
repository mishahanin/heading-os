"""A `null` in the verdict ledger stopped the aggregate being written, forever.

`load_verdicts` caught `json.JSONDecodeError` and nothing else. `json.loads`
returns any JSON value, so a line of `null`, `[]`, `42` or `"text"` parsed fine
and then raised `AttributeError` on `rec.get("verdict_id")`. That escapes
`main`, so `_aggregate.md` is never written.

The permanence is the damage. `_verdicts.jsonl` is append-only and nothing
removes the bad line, so every later run crashed the same way until someone
hand-edited the file. The read side of the whole verdict workflow, and the
Phase-3b calibration gate that counts its rows, go down with it.

`scripts/council-record-verdict.py` already guarded the identical parse of this
identical file. The guard reached one of the ledger's two readers and not the
other, which is why this asserts the behaviour of the reader rather than the
presence of a line of code.

Measured 2026-08-29 before the fix: `AttributeError: 'NoneType' object has no
attribute 'get'`, from `scripts/council-aggregate.py:202`.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_SRC = ROOT / "scripts" / "council-aggregate.py"
_spec = importlib.util.spec_from_file_location("council_aggregate_ledger", _SRC)
ca = importlib.util.module_from_spec(_spec)
# Registered BEFORE exec: `@dataclass` resolves its annotations through
# `sys.modules[cls.__module__]`.
sys.modules["council_aggregate_ledger"] = ca
_spec.loader.exec_module(ca)

# Every JSON value that is not an object. Each one parses, and each one used to
# reach `.get` on something that has no `.get`.
NOT_A_RECORD = ["null", "[]", "42", '"text"', "true", '["verdict_id"]']

GOOD_BEFORE = '{"verdict_id": "2026-08-29_council_alpha", "choice": "kimi", "notes": "clearest"}'
GOOD_AFTER = '{"verdict_id": "2026-08-29_council_beta", "choice": "claude"}'


def _ledger(tmp_path, monkeypatch, lines):
    path = tmp_path / "_verdicts.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(ca, "verdicts_path", lambda p=path: p)
    return path


@pytest.mark.parametrize("scalar", NOT_A_RECORD)
def test_a_scalar_ledger_line_is_skipped_not_dereferenced(tmp_path, monkeypatch, scalar):
    """One bad line costs that line, and only that line."""
    _ledger(tmp_path, monkeypatch, [GOOD_BEFORE, scalar, GOOD_AFTER])

    verdicts = ca.load_verdicts()

    assert set(verdicts) == {
        "2026-08-29_council_alpha",
        "2026-08-29_council_beta",
    }, f"a ledger line of {scalar} must not cost the records around it"
    assert verdicts["2026-08-29_council_alpha"]["choice"] == "kimi"
    assert verdicts["2026-08-29_council_beta"]["choice"] == "claude"


def test_the_aggregate_is_still_written_after_a_bad_ledger_line(tmp_path, monkeypatch):
    """The end the operator sees: `_aggregate.md` exists and carries the verdict.

    `load_verdicts` returning a dict is not the point on its own. The point is
    that `main` reaches the write. Before the fix this raised out of `main` and
    left no file at all.
    """
    council = tmp_path / "council"
    council.mkdir()
    transcript = council / "2026-08-29_council_alpha.md"
    transcript.write_text(
        "---\ntimestamp: 2026-08-29T09:00:00\nmode: independent\n---\n"
        "# Council Consultation - Acme Telecom renewal\n\n"
        "## Question\nDo we renew Acme Telecom at the same terms?\n\n"
        "## Kimi's full response\nRenew, but shorten the term.\n",
        encoding="utf-8",
    )
    _ledger(
        tmp_path,
        monkeypatch,
        ['{"verdict_id": "2026-08-29_council_alpha", "choice": "kimi"}', "null"],
    )
    monkeypatch.setattr(ca, "council_dir", lambda p=council: p)
    monkeypatch.setattr(ca, "aggregate_path", lambda p=council / "_aggregate.md": p)

    assert ca.collect_transcripts(), "empty corpus: this test would pass proving nothing"

    assert ca.main([]) == 0

    written = (council / "_aggregate.md").read_text(encoding="utf-8")
    assert "Acme Telecom renewal" in written
    assert "**CEO verdict:** KIMI" in written
    assert "Kimi=1" in written


# ---------------------------------------------------------------------------
# The same permanence, reached through the bytes rather than through the JSON.
#
# The docstring above argues that the damage is PERMANENCE: `_verdicts.jsonl` is
# append-only and nothing removes a bad line, so one poisoned record breaks
# every later run. That argument was only ever enforced for lines that DECODED.
# The read itself was `read_text(encoding="utf-8").splitlines()` with no handler
# at all, in BOTH readers of this one file, and it carried two more defects of
# exactly the shape this test file is named for. Fixed and measured 2026-09-01.
# ---------------------------------------------------------------------------

def _record_verdict_module():
    """The OTHER reader of `_verdicts.jsonl`, loaded the same way."""
    src = ROOT / "scripts" / "council-record-verdict.py"
    spec = importlib.util.spec_from_file_location("council_record_verdict_ledger", src)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["council_record_verdict_ledger"] = mod
    spec.loader.exec_module(mod)
    return mod


# A verdict whose `notes` arrived as Latin-1 from a paste. `append` writes with
# `ensure_ascii=False`, so nothing upstream normalises what lands on this line.
LATIN1_LINE = b'{"verdict_id": "bad", "choice": "kimi", "notes": "caf\xe9"}'
GOOD_LINE = b'{"verdict_id": "2026-08-29_council_alpha", "choice": "kimi"}'


def _write_bytes(tmp_path, monkeypatch, payload: bytes):
    path = tmp_path / "_verdicts.jsonl"
    path.write_bytes(payload)
    monkeypatch.setattr(ca, "verdicts_path", lambda p=path: p)
    return path


def test_an_undecodable_ledger_byte_does_not_end_the_run(tmp_path, monkeypatch, capsys):
    """MEASURED before the fix: `UnicodeDecodeError: 'utf-8' codec can't decode
    byte 0xe9`, raised out of `load_verdicts`, out of `main`, on every run
    afterwards. `UnicodeDecodeError` is a `ValueError`, so it matched neither
    `json.JSONDecodeError` nor anything else in this file, while
    `parse_transcript` sixty lines up already caught it for transcripts.
    """
    _write_bytes(tmp_path, monkeypatch, LATIN1_LINE + b"\n" + GOOD_LINE + b"\n")

    verdicts = ca.load_verdicts()

    assert set(verdicts) == {"2026-08-29_council_alpha"}, (
        "one undecodable line must cost that line and no other")
    assert "undecodable" in capsys.readouterr().err, (
        "a dropped verdict changes the count feeding the Phase-3b calibration "
        "gate; dropping it in silence reads as a measurement")


def test_the_other_ledger_reader_survives_the_same_byte(tmp_path, capsys):
    """The fix has to reach BOTH readers of this one file. It did not, twice
    before, and this file's own docstring is about the first of those times."""
    mod = _record_verdict_module()
    path = tmp_path / "_verdicts.jsonl"
    path.write_bytes(LATIN1_LINE + b"\n" + GOOD_LINE + b"\n")

    assert set(mod.latest_verdicts(path)) == {"2026-08-29_council_alpha"}
    assert "undecodable" in capsys.readouterr().err


# The eight characters `str.splitlines()` breaks on and `bytes.splitlines()`
# does not. Written as escapes, never as raw literals: `.claude/rules/
# hidden-chars.md` bans invisible Unicode in any generated file, and the
# PostToolUse sanitiser flags this very file when they appear unescaped. The
# bytes are identical.
EXTRA_STR_BOUNDARIES = ["\u2028", "\u2029", "\x85", "\x0b", "\x0c",
                        "\x1c", "\x1d", "\x1e"]


def _lands_raw(sep: str) -> bool:
    """Does `append`'s encoder put `sep` on the ledger line unescaped?

    Only then can the reader's splitter ever see it. JSON requires the C0
    controls to be escaped, so `json.dumps` emits `\\u000b` for five of the eight
    above whatever `ensure_ascii` says, and the shredding path is unreachable
    for those. Derived, never hand-listed: the split between reachable and
    unreachable is a property of the stdlib encoder and of `append`'s
    `ensure_ascii=False`, and either can change.
    """
    return sep in json.dumps({"n": f"a{sep}b"}, ensure_ascii=False)


REACHABLE = [s for s in EXTRA_STR_BOUNDARIES if _lands_raw(s)]


def test_the_reachable_separator_set_is_exactly_the_three_measured():
    """A derivation guard, so the parametrization below cannot go vacuous.

    Five of the eight were in that list when it was first written and each of
    them passed identically with the fix and without it -- `json.dumps` escapes
    the C0 controls, so the raw character never reaches the ledger and there was
    nothing for a splitter to break on. Five cases that cannot fail read exactly
    like five that pass. MEASURED 2026-09-01: U+2028, U+2029 and U+0085 land raw;
    U+000B, U+000C, U+001C, U+001D and U+001E do not.

    If `append` ever switches to `ensure_ascii=True` this set empties, the
    parametrization below silently stops running, and this assertion is what
    says so instead.
    """
    assert set(REACHABLE) == {"\u2028", "\u2029", "\x85"}, (
        f"the set of separators that reach the ledger raw changed to "
        f"{[hex(ord(c)) for c in REACHABLE]}; the parametrization below is "
        "measuring a different thing than it was written to measure")


@pytest.mark.parametrize("sep", REACHABLE, ids=lambda s: f"U+{ord(s):04X}")
def test_a_line_separator_inside_a_note_does_not_shred_the_record(
        tmp_path, monkeypatch, sep):
    """`str.splitlines()` breaks on characters JSONL does not.

    MEASURED before the fix: a single U+2028 inside `notes` split the record
    into two fragments, neither parsed, and the `JSONDecodeError` clause dropped
    both without a word -- `load_verdicts` answered `{}` over a file holding one
    perfectly valid verdict. Silent, and permanent while the line sits in an
    append-only ledger. `bytes.splitlines()` breaks on `\\n` and `\\r` only.
    """
    payload = json.dumps(
        {"verdict_id": "2026-08-29_council_alpha", "choice": "kimi",
         "notes": f"line one{sep}line two"},
        ensure_ascii=False).encode("utf-8") + b"\n"
    _write_bytes(tmp_path, monkeypatch, payload)

    verdicts = ca.load_verdicts()

    assert set(verdicts) == {"2026-08-29_council_alpha"}, (
        f"a record carrying U+{ord(sep):04X} was shredded and silently dropped")
    assert verdicts["2026-08-29_council_alpha"]["notes"] == f"line one{sep}line two"


def test_the_writer_still_emits_the_raw_character(tmp_path, monkeypatch):
    """The anchor under the test above, taken through `append` itself.

    `_lands_raw` asks `json.dumps` directly; this asks the function that
    actually writes the ledger, so a change to `append`'s call fails here even
    if the encoder is unchanged.
    """
    mod = _record_verdict_module()
    council = tmp_path / "council"
    monkeypatch.setattr(mod, "council_dir", lambda p=council: p)
    monkeypatch.setattr(mod, "verdicts_path",
                        lambda p=council / "_verdicts.jsonl": p)

    mod.append("2026-08-29_council_alpha", "kimi", "line one\u2028line two")

    assert b"\xe2\x80\xa8" in (council / "_verdicts.jsonl").read_bytes(), (
        "the writer escapes non-ASCII now, so the shredding path above is dead "
        "code and this guard is measuring nothing")


def test_a_crlf_ledger_still_reads(tmp_path, monkeypatch):
    """The negative control on moving the read to bytes: a CRLF ledger, which a
    Windows editor can leave behind, must still parse.

    Stated narrowly, because the first draft of this docstring claimed more than
    the test establishes. It does NOT pin `bytes.splitlines()` over
    `raw.split(b"\\n")`: MEASURED 2026-09-01, that substitution left this test
    green, because the `.strip()` on the decoded line already removes the
    trailing `\\r`. The two spellings differ only on a bare `\\r` with no `\\n`,
    which nothing writes to this file. What this test does establish is that the
    byte-level read did not break the ordinary CRLF case.
    """
    _write_bytes(tmp_path, monkeypatch, GOOD_LINE + b"\r\n" + LATIN1_LINE + b"\r\n")

    assert set(ca.load_verdicts()) == {"2026-08-29_council_alpha"}


def test_an_unreadable_ledger_is_announced(tmp_path, monkeypatch, capsys):
    """A directory where the ledger should be: `read_bytes` raises OSError.

    "There are no verdicts" and "I could not read the verdicts" are different
    facts, and only one of them is true here.
    """
    path = tmp_path / "_verdicts.jsonl"
    path.mkdir()
    monkeypatch.setattr(ca, "verdicts_path", lambda p=path: p)

    assert ca.load_verdicts() == {}
    assert "could not be read" in capsys.readouterr().err


def test_a_good_ledger_is_still_silent(tmp_path, monkeypatch, capsys):
    """The anchor: the two new warnings must not fire on an ordinary file."""
    _write_bytes(tmp_path, monkeypatch, GOOD_LINE + b"\n")

    assert set(ca.load_verdicts()) == {"2026-08-29_council_alpha"}
    assert capsys.readouterr().err == ""


LEDGER_READERS = ("council-aggregate.py", "council-record-verdict.py")


def test_neither_ledger_reader_decodes_the_whole_file_at_once():
    """A derivation guard, so a THIRD reader cannot arrive unguarded.

    Both defects above rode on one expression, `read_text(...).splitlines()`,
    and the reason there were two sites to fix is that the first fix reached one
    reader of this file and not the other. Asserting the expression is absent
    from both scripts fails the moment anyone reintroduces it, including in a
    reader that does not exist yet.
    """
    for name in LEDGER_READERS:
        src = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        body = "\n".join(ln for ln in src.splitlines()
                         if not ln.lstrip().startswith("#"))
        assert "read_text(encoding=\"utf-8\").splitlines()" not in body, (
            f"{name} decodes the whole ledger in one call again; one bad byte "
            "is then permanent, on an append-only file")


def test_both_readers_go_through_the_one_shared_reader():
    """The fix is one function, and this is what keeps it one.

    A per-file copy is how this pair got here: the `isinstance(rec, dict)` guard
    the top of this file is named for reached one reader and not the other, and
    so did the decode handling. `scripts/utils/jsonl_lines.py` exists so there is
    no second place to forget.

    Asked of the AST, not of the text, so a mention in a comment or a docstring
    is not evidence -- both files DO discuss the module in prose, which is
    exactly what a substring check would accept.
    """
    import ast

    for name in LEDGER_READERS:
        tree = ast.parse((ROOT / "scripts" / name).read_text(encoding="utf-8"))
        imported = any(
            isinstance(n, ast.ImportFrom) and n.module == "scripts.utils.jsonl_lines"
            for n in ast.walk(tree))
        called = any(
            isinstance(n, ast.Call)
            and (getattr(n.func, "id", None) or getattr(n.func, "attr", None))
            == "jsonl_lines"
            for n in ast.walk(tree))
        assert imported and called, (
            f"{name} no longer reads the ledger through jsonl_lines "
            f"(imported={imported}, called={called}); it has its own copy again, "
            "and a copy is the one that stops being fixed")


def test_the_shared_reader_is_not_in_the_frontmatter_registry():
    """Why the byte-level read moved OUT of `council-aggregate.py`.

    `tests/test_ten_regexes_that_spelled_the_fence_themselves.py` keeps a
    registry of files that parse frontmatter, and refuses ANY byte-level read in
    them: `Path.read_text()` decodes in universal-newline mode, so a lone CR
    becomes a newline and cannot reach a frontmatter pattern, while
    `read_bytes()` preserves it. `council-aggregate.py` is in that registry, and
    it is right to be -- `parse_transcript` there reads transcripts and must keep
    universal newlines.

    MEASURED 2026-09-01: putting `read_bytes()` inline in `load_verdicts` turned
    that guard red, correctly, even though the LEDGER read cannot reach a
    frontmatter pattern. Moving the read into a module outside the registry is
    the resolution that leaves both guards saying something true; spelling it
    `open(path, "rb")` inside the same file would have satisfied the check
    without answering it.
    """
    registry = (ROOT / "tests"
                / "test_ten_regexes_that_spelled_the_fence_themselves.py"
                ).read_text(encoding="utf-8")
    assert '"scripts/council-aggregate.py"' in registry, (
        "council-aggregate.py left the frontmatter registry; this test explains "
        "a constraint that no longer applies and should be re-read, not deleted")
    assert "scripts/utils/jsonl_lines.py" not in registry, (
        "the shared ledger reader was added to the frontmatter registry, which "
        "would refuse the byte-level read that is its entire purpose")
