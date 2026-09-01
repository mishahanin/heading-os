#!/usr/bin/env python3
"""Shard 55: the nine readers that looked for three characters, not a fence line.

Shard 52 fixed this shape in three copies and shard 54 in a fourth. Shard 54's
shape-keyed sweep then found the whole population: 20 call sites in 17 files,
NINE of them real frontmatter readers still testing for the CHARACTERS `---`.
Every one is fixed here, and this file is what stops the tenth.

MEASURED 2026-08-28, each reader against `scripts.utils.markdown.split_frontmatter`
over one table of eight documents. The divergences, before:

    document                  who disagreed, and how
    ------------------------  ------------------------------------------------
    fence with trailing space  threads_lib RAISED; validate-crm-schema,
                               run-skill-eval and quick_validate REFUSED a valid
                               file
    fence with a tab           the same four, plus odin-cadence, which left the
                               malformed opener inside the block and made PyYAML
                               fail on correct YAML
    `---` inside a scalar      chronicle truncated `"alpha --- beta"` to `"alpha`
                               and fed the REST of the frontmatter to the gist;
                               crm_migrate scored 29 against 14 for the same
                               body and so picked the WRONG canonical record
    `---extra` as the opener   odin-cadence errored; viraid_counterpart,
                               router_payload and chronicle ACCEPTED a file the
                               canonical splitter refuses

After the change the same table produces ZERO divergences across all readers.

Example data is invented throughout. No real entity appears in this file.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import read_sources, tracked_python_files  # noqa: E402

from scripts.utils.markdown import FM_OK, split_frontmatter  # noqa: E402
from scripts.utils.threads_lib import parse_thread_file  # noqa: E402
from scripts.utils.viraid_counterpart import _frontmatter as viraid_frontmatter  # noqa: E402
from scripts.utils.router_payload import load_skill_description  # noqa: E402


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


OC = _load("scripts/odin-cadence.py", "odin_cadence_s55")
RSE = _load("scripts/run-skill-eval.py", "run_skill_eval_s55")
VCS = _load("scripts/validate-crm-schema.py", "validate_crm_schema_s55")
CHRON = _load("scripts/chronicle.py", "chronicle_s55")
MIG = _load("scripts/crm_migrate_to_entity_model.py", "crm_migrate_s55")


# ============================================================
# The one table every reader is measured against
# ============================================================

DOCUMENTS = {
    "plain": "---\ntitle: alpha\nid: x\n---\n\n# body\n",
    # A fence is a LINE. Trailing whitespace on it is invisible in an editor and
    # survives a copy-paste, so it is the shape a hand-edited file actually takes.
    "fence with a trailing space": "--- \ntitle: alpha\nid: x\n---\n\n# body\n",
    "fence with a tab": "---\t\ntitle: alpha\nid: x\n---\n\n# body\n",
    "dashes inside a scalar": '---\ntitle: "alpha --- beta"\nid: x\n---\n\n# body\n',
    "a horizontal rule in the body": "---\ntitle: alpha\nid: x\n---\n\n# body\n\n---\n\ntail\n",
    "four dashes in the body": "---\ntitle: alpha\nid: x\n---\n\n----\n\ntail\n",
    "no closing fence": "---\ntitle: alpha\nid: x\n\n# body\n",
    "---extra as the opener": "---extra\ntitle: alpha\nid: x\n---\n\n# body\n",
    "CRLF throughout": "---\r\ntitle: alpha\r\nid: x\r\n---\r\n\r\n# body\r\n",
}

REFUSED = "REFUSED"


def _canonical(text: str) -> str:
    block, _body, kind = split_frontmatter(text)
    if block is None or kind != FM_OK:
        return REFUSED
    return str((yaml.safe_load(block) or {}).get("title"))


def _tmpfile(text: str, name: str = "doc.md") -> Path:
    path = Path(tempfile.mkdtemp()) / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


# --- one adapter per reader, each calling the REAL function ---

def _r_odin_cadence(text: str) -> str:
    block = OC._frontmatter_block(text)
    if not block:
        return REFUSED
    return str((yaml.safe_load(block) or {}).get("title"))


def _r_viraid(text: str) -> str:
    fm = viraid_frontmatter(text)
    return fm.get("title", REFUSED) if fm else REFUSED


def _r_router_payload(text: str) -> str:
    d = Path(tempfile.mkdtemp())
    (d / "SKILL.md").write_text(text, encoding="utf-8", newline="")
    # This reader returns `description`, so the fixture carries the title there
    # too; what is under test is whether it finds the block at all.
    (d / "SKILL.md").write_text(text.replace("title:", "description:"),
                                encoding="utf-8", newline="")
    got = load_skill_description(d)
    return got.strip('"') if got else REFUSED


def _r_run_skill_eval(text: str) -> str:
    d = Path(tempfile.mkdtemp())
    (d / "SKILL.md").write_text(text, encoding="utf-8", newline="")
    _body, fm = RSE.load_skill_system_prompt(d)
    return fm.get("title", REFUSED)


def _r_validate_crm(text: str) -> str:
    fm = VCS.parse_frontmatter(_tmpfile(text))
    return REFUSED if fm is None else str(fm.get("title", "?"))


def _r_quick_validate(text: str) -> str:
    """The regex quick_validate.py uses, read from its source, not retyped."""
    src = (ROOT / ".claude" / "skills" / "skill-creator" / "scripts"
           / "quick_validate.py").read_text(encoding="utf-8")
    pattern = next(
        n.args[0].value for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "match" and n.args
        and isinstance(n.args[0], ast.Constant)
        and isinstance(n.args[0].value, str) and n.args[0].value.startswith("^---")
        and "(.*?)" in n.args[0].value
    )
    m = re.match(pattern, text, re.DOTALL)
    if not m:
        return REFUSED
    data = yaml.safe_load(m.group(1))
    return str(data.get("title")) if isinstance(data, dict) else "NOT-A-MAPPING"


def _r_threads_lib(text: str) -> str:
    thread = (
        text.replace("title: alpha", "title: alpha\n" + _THREAD_TAIL)
            .replace('title: "alpha --- beta"', 'title: "alpha --- beta"\n' + _THREAD_TAIL)
    ).replace("id: x", "id: t-1")
    if "\r\n" in text:
        thread = thread.replace("\n", "\r\n").replace("\r\r\n", "\r\n")
    try:
        return parse_thread_file(_tmpfile(thread, "t-1.md")).title
    except (ValueError, yaml.YAMLError):
        return REFUSED


_THREAD_TAIL = ("status: active\ntype: business\nclassification: private\n"
                "opened: 2026-01-01\nlast_touched: 2026-01-02\nlinks: {}\ntags: []")


def _r_chronicle(text: str) -> str:
    """The chronicle path, exercised through its own splitter usage."""
    block, _body, kind = split_frontmatter(text)
    if block is None or kind != FM_OK:
        return REFUSED
    for line in block.splitlines():
        m = CHRON._FRONT_RE.match(line.strip())
        if m and m.group(1) == "title":
            return m.group(2).strip().strip('"')
    return "?"


READERS = {
    "odin-cadence": _r_odin_cadence,
    "viraid_counterpart": _r_viraid,
    "router_payload": _r_router_payload,
    "run-skill-eval": _r_run_skill_eval,
    "validate-crm-schema": _r_validate_crm,
    "quick_validate": _r_quick_validate,
    "threads_lib": _r_threads_lib,
    "chronicle": _r_chronicle,
}


@pytest.mark.parametrize("doc_label", sorted(DOCUMENTS))
@pytest.mark.parametrize("reader_label", sorted(READERS))
def test_every_reader_agrees_with_the_canonical_splitter(reader_label, doc_label):
    """The test that matters: one table, every reader, one answer required.

    A per-reader test passes while the readers drift apart, which is exactly what
    happened between 2026-08-20 and 2026-08-28 across four separate files.
    """
    text = DOCUMENTS[doc_label]
    want = _canonical(text)
    got = READERS[reader_label](text)
    assert str(got).strip('"') == str(want).strip('"'), (
        f"{reader_label} read {got!r} where the canonical splitter says {want!r} "
        f"for the document {doc_label!r}")


# ============================================================
# The named consequence of each defect, pinned one by one
# ============================================================

def test_a_thread_file_with_a_trailing_space_on_its_fence_still_parses():
    """`parse_thread_file` RAISED on a valid file, and it has 45 callers.

    The one caller that catches ValueError is `scan_for_archive`, which then
    drops the thread from the archive scan without a word. The other 44 get a
    traceback.
    """
    body = ("--- \nid: t-1\ntitle: Alpha\nstatus: active\ntype: business\n"
            "classification: private\nopened: 2026-01-01\nlast_touched: 2026-01-02\n"
            "links: {}\ntags: []\n---\n\n# body\n")
    thread = parse_thread_file(_tmpfile(body, "t-1.md"))
    assert thread.title == "Alpha"
    assert thread.body.strip() == "# body"


def test_a_thread_file_round_trips_through_a_crlf_fence():
    body = ("---\r\nid: t-1\r\ntitle: Alpha\r\nstatus: active\r\ntype: business\r\n"
            "classification: private\r\nopened: 2026-01-01\r\nlast_touched: 2026-01-02\r\n"
            "links: {}\r\ntags: []\r\n---\r\n\r\n# body\r\n")
    thread = parse_thread_file(_tmpfile(body, "t-1.md"))
    assert thread.title == "Alpha"
    assert thread.body.strip() == "# body"


def test_a_thread_with_no_frontmatter_still_raises():
    """The refusal is load-bearing; only the false one was wrong."""
    with pytest.raises(ValueError, match="missing YAML frontmatter"):
        parse_thread_file(_tmpfile("# just a body\n", "t-1.md"))


def test_the_crm_gate_no_longer_fails_a_valid_card():
    """A FAIL here blocks the record from aggregation, on a correct file."""
    card = '--- \nname: Jane Bond\nemail: jane@example.invalid\ntype: partner\n---\n\n# Jane\n'
    fm = VCS.parse_frontmatter(_tmpfile(card, "jane-bond.md"))
    assert fm is not None, "the gate refused a card whose YAML is fine"
    assert fm["name"] == "Jane Bond"


def test_the_crm_gate_still_refuses_a_card_with_no_frontmatter():
    assert VCS.parse_frontmatter(_tmpfile("# no frontmatter\n", "x.md")) is None


def test_an_unreadable_crm_file_is_named_rather_than_swallowed(tmp_path, capsys):
    """`except Exception: return None` reported an unreadable FILE as a malformed
    BLOCK. Two different findings, one message."""
    missing = tmp_path / "gone.md"
    assert VCS.parse_frontmatter(missing) is None
    assert "could not be read" in capsys.readouterr().err


def test_the_eval_prompt_no_longer_carries_the_yaml_block():
    """This one failed OPEN: `body = text` kept the whole file as the SYSTEM
    prompt, and `model:` was lost so the run silently used the default model."""
    d = Path(tempfile.mkdtemp())
    (d / "SKILL.md").write_text("--- \nname: s\nmodel: claude-opus-5\n---\n\n"
                                "# The skill body\n", encoding="utf-8")
    body, fm = RSE.load_skill_system_prompt(d)
    assert fm.get("model") == "claude-opus-5", "the model pin was lost"
    assert body.startswith("# The skill body")
    assert "name: s" not in body, "the YAML block leaked into the system prompt"


def test_the_skill_validator_accepts_a_fence_with_trailing_whitespace():
    QV = _load(".claude/skills/skill-creator/scripts/quick_validate.py", "quick_validate_s55")
    d = Path(tempfile.mkdtemp())
    (d / "SKILL.md").write_text(
        "--- \nname: demo-skill\ndescription: A demo skill for the test suite.\n"
        "metadata:\n  author: Misha Hanin\n  email: misha.hanin@odinix.com\n"
        '  version: "1.0"\n---\n\n# Demo\n', encoding="utf-8")
    ok, message = QV.validate_skill(d)
    assert ok, f"a valid skill was rejected: {message}"


def test_the_skill_validator_still_names_its_two_refusals_apart():
    """"No frontmatter" and "invalid format" are different findings."""
    QV = _load(".claude/skills/skill-creator/scripts/quick_validate.py", "quick_validate_s55b")
    d1 = Path(tempfile.mkdtemp())
    (d1 / "SKILL.md").write_text("# no frontmatter at all\n", encoding="utf-8")
    assert QV.validate_skill(d1) == (False, "No YAML frontmatter found")
    d2 = Path(tempfile.mkdtemp())
    (d2 / "SKILL.md").write_text("---\nname: x\n\n# never closed\n", encoding="utf-8")
    assert QV.validate_skill(d2) == (False, "Invalid frontmatter format")


def test_the_migration_scores_the_body_not_the_leaked_yaml(tmp_path):
    """`text.split("---", 2)[-1]` left the rest of the YAML in the "body".

    MEASURED: 29 against 14 for the same body text, so the heuristic picked the
    WRONG record as canonical -- and that decides which card becomes the
    address-book entity.
    """
    plain = tmp_path / "plain.md"
    plain.write_text("---\nname: A\n---\n\nSHORT BODY\n", encoding="utf-8")
    dashed = tmp_path / "dashed.md"
    dashed.write_text('---\nname: "A --- B"\nsrc: padding padding padding\n---\n\nSHORT BODY\n',
                      encoding="utf-8")
    # The longer body wins. Both have the SAME body, so the record listed first
    # must survive: without the fix the dashed one scored higher on leaked YAML.
    best = MIG.pick_canonical_record([
        {"file_path": str(plain), "name": "plain"},
        {"file_path": str(dashed), "name": "dashed"},
    ])
    assert best["name"] == "plain", "the leaked YAML still inflates the score"


def test_the_chronicle_keeps_a_title_containing_dashes(tmp_path, monkeypatch):
    """`raw.split("---", 2)` cut `"alpha --- beta"` to `"alpha`, and fed the rest
    of the frontmatter to the gist the entry is indexed by."""
    root = tmp_path / "chronicle"
    (root / "personal").mkdir(parents=True)
    (root / "personal" / "session-1.md").write_text(
        '---\ndate: 2020-01-05\ntitle: "alpha --- beta"\ntopics: [gadget]\n---\n\n'
        "Body prose that becomes the gist.\n", encoding="utf-8")
    monkeypatch.setattr(CHRON, "chronicle_root", lambda: root)
    entries = CHRON._load_personal_entries()
    assert len(entries) == 1
    assert entries[0]["date"] == "2020-01-05"
    assert "src:" not in entries[0]["gist"], "frontmatter leaked into the gist"
    assert "topics" not in entries[0]["gist"]


def test_the_router_payload_refuses_what_the_engine_refuses():
    """Fail-open on a malformed opener built the judge payload from a file the
    rest of the engine treats as having no frontmatter."""
    d = Path(tempfile.mkdtemp())
    (d / "SKILL.md").write_text("---extra\nname: s\ndescription: alpha beta\n---\n\n# b\n",
                                encoding="utf-8")
    assert load_skill_description(d) == ""


def test_the_router_payload_still_reads_a_folded_description():
    """The raw line scan stays: the judge sees the author's own wording."""
    d = Path(tempfile.mkdtemp())
    (d / "SKILL.md").write_text(
        "--- \nname: s\ndescription: >\n  alpha beta\n  gamma\nother: x\n---\n\n# b\n",
        encoding="utf-8")
    got = load_skill_description(d)
    assert "alpha beta" in got and "gamma" in got
    assert "other" not in got


def test_the_viraid_resolver_refuses_a_malformed_opener():
    """Fail-open here invents a counterpart record from a file nothing else reads."""
    assert viraid_frontmatter("---extra\nname: Jane Bond\n---\n") == {}
    assert viraid_frontmatter("--- \nname: Jane Bond\n---\n") == {"name": "Jane Bond"}


def test_odin_cadence_reads_a_block_pyyaml_can_parse():
    """A tab on the fence left the opener inside the block and PyYAML failed."""
    block = OC._frontmatter_block("---\t\ntitle: alpha\nid: x\n---\n\n# body\n")
    assert yaml.safe_load(block)["title"] == "alpha"
    assert OC._frontmatter_block("---extra\ntitle: alpha\n---\n") == ""


# ============================================================
# The registry from shard 54 has shrunk, and cannot quietly regrow
# ============================================================

def test_no_open_fence_reader_survives():
    """Every entry shard 54 marked OPEN is migrated.

    The registry lives in the sibling test file; this reads it rather than
    copying it, so the two cannot disagree about what is still open.
    """
    spec = importlib.util.spec_from_file_location(
        "shard54", ROOT / "tests" / "test_a_digest_that_read_a_card_the_schema_had_left.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    still_open = sorted(f for f, why in mod.DECLARED_FENCE_SITES.items()
                        if why.startswith("OPEN"))
    assert still_open == [], f"still marked OPEN: {still_open}"


# ============================================================
# The widened sweep: the REGEX form the shape sweep could not see
# ============================================================
#
# Shard 54's detector matched `<str>.find/startswith/split("---")`. It could not
# see a frontmatter splitter written as a REGEX, and eleven of those turn out to
# exist. That is the third blind spot in a row (name, then shape, now form), so
# this sweep measures BEHAVIOUR instead: it compiles every frontmatter-shaped
# regex literal in the tree and runs it over the same document table.
#
# MEASURED 2026-08-28: 21 such regexes, of which 10 disagree with the canonical
# grammar. Each is recorded below with the documents it mis-reads, so the set can
# only shrink. `\n`-only fences reject a CRLF file and a fence with trailing
# whitespace; the two entries with an empty tuple carry a different capture shape
# than this sweep can read, and are listed so they are not silently uncounted.
# Shard 56 (2026-08-29) emptied eight of the ten entries this registry opened
# with, including both UNJUDGED ones: each of those readers now takes its fences
# from `scripts.utils.markdown`. The consequences they cost, and the measurement
# behind each fix, are in
# `tests/test_ten_regexes_that_spelled_the_fence_themselves.py`.
#
# The two that remain diverge ONLY on a CRLF document, and no reader in the set
# can receive one: both obtain their text through universal-newline decoding, so
# a `\r` never reaches the pattern. `merge-contacts.py` must NOT be widened for
# it -- that reader writes the record back with LF, so accepting CRLF on the
# read would convert a file's line endings on a merge asked to change one field.
# The reachability claim is a test, not a comment:
# `test_no_reader_in_the_set_can_receive_a_cr` goes red if either file starts
# decoding with `newline=""`.
KNOWN_REGEX_DIVERGENCE = {
    "scripts/merge-contacts.py": ("CRLF throughout",),
    "scripts/odin_pagerank.py": ("CRLF throughout",),
}


def _frontmatter_regexes():
    """Every `re.*` call whose pattern is anchored at the start and holds two fences."""
    found = []
    # Read through `read_sources`: the walk lists the modules and this loop
    # parses them, and a scratch `.py` written into `tests/` by a parallel agent
    # can live and die inside that window. A module that is gone spells no
    # frontmatter regex, so it is skipped and named in a warning rather than
    # taking the guard down with a FileNotFoundError.
    for path, source in read_sources(tracked_python_files()):
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and ast.unparse(node.func.value) == "re" and node.args):
                continue
            arg = node.args[0]
            if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                continue
            pattern = arg.value
            anchored = pattern.startswith(("^---", "\\A---", "\\A(---"))
            if anchored and pattern.count("---") >= 2 and "(" in pattern:
                found.append((str(path.relative_to(ROOT)), node.lineno, pattern))
    return found


def _regex_divergence(pattern: str) -> tuple:
    """Which documents this pattern reads differently from the shared splitter."""
    try:
        rx = re.compile(pattern, re.DOTALL)
    except re.error:  # pragma: no cover
        return ()
    off = []
    for label, text in DOCUMENTS.items():
        want = _canonical(text)
        want_block = None if want == REFUSED else want
        m = rx.match(text) or rx.search(text)
        got = None
        if m and m.groups():
            got = next((g.strip() for g in m.groups() if g and "title" in g), None)
            if got is None:
                got = (m.group(1) or "").strip()
        elif m:
            got = ""
        accepted_ok = (got is None) == (want_block is None)
        if not accepted_ok:
            off.append(label)
    return tuple(off)


def test_the_regex_sweep_actually_finds_the_splitters():
    """Every sweep below is green over an empty result.

    The three tests that follow all assert that nothing UNDECLARED diverges,
    which is trivially true of a sweep that found nothing. The detector is a
    heuristic over pattern text - anchored at the start, two fences, one capture
    group - and any of those three could stop matching after an ordinary rewrite
    of a pattern, silently retiring the whole registry. That is the third time
    this defect shape would have hidden here: the first detector keyed on the
    function NAME, the second on the call SHAPE, and a floor is what stops the
    third from keying on nothing at all.

    MEASURED 2026-09-01: 12 call sites in 12 files. The floors sit well under
    both, because a reader migrating to `scripts.utils.markdown` legitimately
    removes its own regex and that must never fail this test.
    """
    found = _frontmatter_regexes()
    files = {rel for rel, _line, _pattern in found}

    assert len(found) >= 8, (
        f"only {len(found)} frontmatter regexes reached the sweep; the detector "
        f"heuristic has stopped matching and the registry below guards nothing")
    assert "scripts/utils/markdown.py" in files, (
        "the canonical splitter's own pattern is no longer being swept, so the "
        "grammar every other reader is measured against is unmeasured itself")


def test_the_declared_divergences_still_name_files_the_sweep_can_see():
    """A declared entry outliving its regex is a hole nobody can see.

    `KNOWN_REGEX_DIVERGENCE` is keyed by PATH, and the shrink test below reads
    `seen.get(rel, set())`, so an entry whose file was renamed, or whose pattern
    stopped matching the detector, passes both tests forever while covering
    whatever is written at that path next. Same failure as a stale exemption
    list anywhere else: it looks like a decision and is really an absence.
    """
    files = {rel for rel, _line, _pattern in _frontmatter_regexes()}
    stale = sorted(set(KNOWN_REGEX_DIVERGENCE) - files)

    assert stale == [], (
        f"declared as divergent but no longer carrying a frontmatter regex the "
        f"sweep can see: {stale}. Either the reader was migrated - drop the "
        f"entry - or the detector stopped seeing its pattern.")


def test_the_regex_frontmatter_splitters_are_all_declared():
    """A new divergent regex must be argued for, not inherited.

    This sweep exists because the previous two detectors each missed the copy
    that carried the defect: one keyed on the function NAME, one on the call
    SHAPE. This one runs the pattern and compares the answer.
    """
    seen = {}
    for rel, _line, pattern in _frontmatter_regexes():
        off = _regex_divergence(pattern)
        if off:
            seen.setdefault(rel, set()).update(off)
    undeclared = sorted(set(seen) - set(KNOWN_REGEX_DIVERGENCE))
    assert undeclared == [], (
        "regex frontmatter splitter(s) disagreeing with "
        "scripts.utils.markdown.split_frontmatter and not declared: "
        + ", ".join(f"{f} on {sorted(seen[f])}" for f in undeclared))


def test_the_declared_regex_divergences_only_shrink():
    """A declared entry may be FIXED; it may not quietly get worse."""
    seen = {}
    for rel, _line, pattern in _frontmatter_regexes():
        off = _regex_divergence(pattern)
        if off:
            seen.setdefault(rel, set()).update(off)
    for rel, declared in KNOWN_REGEX_DIVERGENCE.items():
        if any(d.startswith("UNJUDGED") for d in declared):
            continue
        actual = seen.get(rel, set())
        widened = sorted(actual - set(declared))
        assert widened == [], f"{rel} now also mis-reads {widened}"


def test_the_shared_splitter_is_not_itself_in_the_divergent_set():
    """The canonical grammar must agree with itself, which is not a tautology:
    `parse_frontmatter`'s own regex lives in the same module and is swept too."""
    divergent = {rel for rel, _l, p in _frontmatter_regexes() if _regex_divergence(p)}
    assert "scripts/utils/markdown.py" not in divergent


def test_the_quick_validate_grammar_matches_the_shared_one():
    """`quick_validate.py` keeps its own regex because inside skill-creator the
    module path `scripts.utils` already means that plugin's own `scripts/utils.py`.
    A copy is allowed; a DIVERGENT copy is not, so the grammar is pinned here.
    """
    for label, text in DOCUMENTS.items():
        assert _r_quick_validate(text).strip('"') == _canonical(text).strip('"'), label
