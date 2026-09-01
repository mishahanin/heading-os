#!/usr/bin/env python3
"""Shard 56: ten frontmatter readers that spelled the fence grammar themselves.

Shard 55 fixed nine readers that tested for the CHARACTERS `---`, and its own
behaviour-keyed sweep then found ten MORE -- written as regex literals, which
the two earlier name-keyed and shape-keyed detectors could not see. Those ten
were recorded in `KNOWN_REGEX_DIVERGENCE` rather than waved through. This file
is the work that registry named.

Eight are fixed here. Two are not, and the reason is measured rather than
asserted: their only divergence is a CRLF document, and no reader in the set can
receive one. Every one of the ten obtains its text through `Path.read_text()` or
`open(path, encoding=...)`, both of which decode in universal-newline mode, so a
CRLF file arrives with `\\n` and no `\\r` reaches the pattern. Changing
`merge-contacts.py` for it would be actively wrong: that reader round-trips a
record it then WRITES BACK, so teaching it to accept `\\r\\n` without also
emitting `\\r\\n` would convert a file's line endings on a merge that was asked
to change one field. `test_no_reader_in_the_set_can_receive_a_cr` pins the
reachability claim, so the blind spot cannot become reachable in silence.

THE TEST THAT MATTERS is `test_every_reader_agrees_on_the_same_document_table`:
one table, all readers, forced to agree. A per-reader test passes while they
drift apart, which is exactly what happened across seventeen files between
2026-08-20 and 2026-08-29.

Every example value in this file is invented. No real entity appears in it.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import read_sources, tracked_paths  # noqa: E402

from scripts.utils.markdown import (  # noqa: E402
    FM_OK,
    split_frontmatter,
    split_frontmatter_raw,
)


# ============================================================
# The document table
# ============================================================
#
# Four shapes. The first is the control; the next two are what eight of the ten
# readers got wrong; the fourth is the one no reader can receive.

def _doc(fm_lines, body, *, open_suffix="", close_suffix="", eol="\n"):
    parts = ["---" + open_suffix, *fm_lines, "---" + close_suffix, body]
    return eol.join(parts) + eol


VARIANTS = {
    "canonical": {},
    "fence with a trailing space": {"open_suffix": " ", "close_suffix": " "},
    "fence with a tab": {"open_suffix": "\t", "close_suffix": "\t"},
}

# Kept out of VARIANTS on purpose: a byte-preserving reader legitimately returns
# CRLF for a CRLF input, so it is not a divergence, and universal-newline
# decoding means no file reader receives one anyway.
CRLF = {"eol": "\r\n"}


def _load(rel: str, name: str):
    """Import a hyphenated CLI script by path. `scripts/x-y.py` is not a module."""
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _in_tree(rel_under_root, runner):
    """Write one document into a temp tree and hand the runner (root, path)."""
    def call(text):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / rel_under_root
            path.parent.mkdir(parents=True, exist_ok=True)
            # newline="" so a CRLF variant reaches disk as CRLF and the reader's
            # own decoding decides whether it ever sees a "\r".
            path.write_text(text, encoding="utf-8", newline="")
            return runner(Path(td), path)
    return call


# ============================================================
# One adapter per reader: PUBLIC entry point, real file where the reader
# opens one. Driving the module-private regex would test the pattern and
# not the consequence, which is the mistake shard 55's sweep already made.
# ============================================================

_inflight = _load("scripts/bridge_daemon/refreshers/inflight.py", "s56_inflight")
_cfa = _load("scripts/context-floor-audit.py", "s56_cfa")
_council = _load("scripts/council-aggregate.py", "s56_council")
_ipr = _load("scripts/inbox-pulse-report.py", "s56_ipr")
_merge = _load("scripts/merge-contacts.py", "s56_merge")
_pagerank = _load("scripts/odin_pagerank.py", "s56_pagerank")
_buildplugins = _load("scripts/dev/build-plugins.py", "s56_buildplugins")

from scripts.utils import canopus_note as _canopus  # noqa: E402
from scripts.utils.content_denylist import build_denylist  # noqa: E402

_humanization = _load("scripts/humanization-check.py", "s56_humanization")


def _r_inflight(text):
    return _inflight._extract_session_id(text)


def _r_context_floor_skills(text):
    return _in_tree(
        ".claude/skills/widget-forge/SKILL.md",
        lambda root, _p: _cfa.measure_skills(root)["skills"],
    )(text)


def _r_context_floor_always_on(text):
    return _cfa._is_always_on(text)


def _r_council(text):
    return _council._parse_frontmatter(text)


def _pinned_data_root(root: Path):
    """Point the data-root SEAM at `root`, and put it back afterwards.

    Both call sites here used to do `_ipr.get_crm_contacts_dir = lambda: ...`,
    a raw module-attribute assignment. Two defects in one line.

    It never restored, so the replacement leaked into every later test in the
    session. And it patched a name on the REPORT module, which stopped being the
    name the code reads on 2026-08-29: `load_known_crm_domains` now goes through
    `scripts.utils.crm.contact_index_by_email` so it can see both CRM card
    schemas. The patch bound a stranger, the function resolved the OPERATOR'S
    LIVE CRM instead of the fixture, and the assertion came back holding real
    company domains. It read, it did not write, so nothing was damaged; the
    point is that a moved seam turned a hermetic test into one that reaches the
    operator's data with nothing saying so.

    Pinning `HEADING_OS_DATA` binds the seam itself rather than one symbol on
    one module, so the next consumer added to this battery is covered without
    anybody remembering to patch it.
    """
    return patch.dict(os.environ, {"HEADING_OS_DATA": str(root)})


def _r_inbox_pulse(text):
    def run(root, _p):
        with _pinned_data_root(root):
            return sorted(_ipr.load_known_crm_domains(root))
    return _in_tree("crm/contacts/dana-okonkwo.md", run)(text)


def _r_denylist(text):
    return _in_tree(
        "crm/contacts/dana-okonkwo.md",
        lambda root, _p: sorted(t for t, cat in build_denylist(root).tokens.items()
                                if cat.startswith("crm-")),
    )(text)


def _r_canopus(text):
    return _in_tree(
        "records/slices/slice-07.md",
        lambda root, _p: _canopus.read_note(root, "slice-07"),
    )(text)


def _r_build_plugins(text):
    """Does the REWRITTEN bundle still carry parseable YAML frontmatter?

    Not byte equality: this reader is byte-preserving by contract, so a `--- `
    file legitimately comes back with `--- `. The defect is whether the
    frontmatter took the YAML-ESCAPED substitution or the plain one, and the
    plain one closes the double-quoted `allowed-tools` scalar early.
    """
    out = _buildplugins.rewrite_script_paths(text)[0]
    fm, _body, kind = split_frontmatter(out)
    if fm is None or kind != FM_OK:
        return "no frontmatter in the output"
    try:
        loaded = yaml.safe_load(fm)
    except yaml.YAMLError as exc:
        return f"bundle frontmatter is not YAML: {type(exc).__name__}"
    return loaded.get("allowed-tools")


def _r_humanization(text):
    return _humanization.strip_markdown_noise(text).strip()


def _r_canonical(text):
    fm, _body, kind = split_frontmatter(text)
    return None if kind != FM_OK else fm


# (label, adapter, document builder). The document is chosen per reader so the
# adapter's answer is one the reader's own caller cares about.
READERS = [
    ("inflight._extract_session_id", _r_inflight,
     lambda **kw: _doc(["session_id: sess-4417", "title: Draft"], "Body.", **kw)),
    ("context-floor-audit.measure_skills", _r_context_floor_skills,
     lambda **kw: _doc(["name: widget-forge", "description: Build widgets."],
                       "# Widget forge", **kw)),
    ("context-floor-audit._is_always_on", _r_context_floor_always_on,
     lambda **kw: _doc(["paths:", "  - scripts/**"], "# Scoped rule", **kw)),
    ("council-aggregate._parse_frontmatter", _r_council,
     lambda **kw: _doc(["mode: full", "timestamp: 2026-08-29T10:00"],
                       "## Question\nWhat now?", **kw)),
    ("inbox-pulse-report.load_known_crm_domains", _r_inbox_pulse,
     lambda **kw: _doc(["name: Dana Okonkwo",
                        "email: dana@nimbus-freight.example"], "Notes.", **kw)),
    ("content_denylist.build_denylist", _r_denylist,
     lambda **kw: _doc(["name: Dana Okonkwo",
                        "email: dana@nimbus-freight.example",
                        "company: Nimbus Freight Holdings"], "Notes.", **kw)),
    ("canopus_note.read_note", _r_canopus,
     lambda **kw: _doc(["slug: slice-07", "value: the clause"], "Prose.", **kw)),
    ("build-plugins.rewrite_script_paths", _r_build_plugins,
     lambda **kw: _doc(['allowed-tools: "Bash(python scripts/x.py:*)"'],
                       "Run python scripts/x.py here.", **kw)),
    ("humanization-check.strip_markdown_noise", _r_humanization,
     lambda **kw: _doc(["title: leveraging robust systems"], "Real prose here.",
                       **kw)),
]


# ============================================================
# The test that matters
# ============================================================


@pytest.mark.parametrize("label,adapter,build", READERS, ids=[r[0] for r in READERS])
def test_every_reader_agrees_on_the_same_document_table(label, adapter, build):
    """One table, every reader, forced to agree with its own canonical answer.

    Nine per-reader tests all passed on 2026-08-28 while these readers disagreed
    with each other, because each one was measured against the fence IT
    accepted. The agreement is the property; the individual answers are not.
    """
    canonical = adapter(build())
    for variant, kwargs in VARIANTS.items():
        if variant == "canonical":
            continue
        got = adapter(build(**kwargs))
        assert got == canonical, (
            f"{label} answers differently for a {variant}: "
            f"{got!r} instead of {canonical!r}"
        )


# ============================================================
# Each named consequence, pinned on its own
# ============================================================


def test_an_inflight_artifact_keeps_its_session_id_through_a_spaced_fence():
    """None here is indistinguishable from an artifact that genuinely has no
    session_id, which is the one thing this function exists to tell apart."""
    text = _doc(["session_id: sess-4417"], "Body.", open_suffix=" ",
                close_suffix=" ")
    assert _inflight._extract_session_id(text) == "sess-4417"


def test_a_skill_with_a_spaced_fence_still_counts_toward_the_context_floor():
    """A dropped skill makes the measured floor SMALLER, so the 5% growth gate
    passes on a floor that grew. That is the one direction a gate must not
    fail in."""
    text = _doc(["name: widget-forge", "description: Build widgets."],
                "# Widget forge", open_suffix=" ", close_suffix=" ")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        skill = root / ".claude" / "skills" / "widget-forge" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(text, encoding="utf-8")
        result = _cfa.measure_skills(root)
    assert result["skills"] == 1
    assert result["frontmatter_bytes"] > 0
    assert result["unreadable_skills"] == []


def test_a_skill_with_no_frontmatter_is_named_not_silently_dropped(capsys):
    """`.claude/rules/scope-claims.md` obligation 2: name what you left out.

    Both surfaces are asserted. The returned field serves a machine reader of
    `--json`; the stderr line serves the operator running the gate by hand, and
    a mutation that silenced only the second one survived a version of this test
    that checked only the first.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        skill = root / ".claude" / "skills" / "orphan" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("# No frontmatter at all\n", encoding="utf-8")
        result = _cfa.measure_skills(root)
    assert result["skills"] == 0
    assert any(entry.startswith("orphan") for entry in result["unreadable_skills"])
    err = capsys.readouterr().err
    assert "orphan" in err, "the operator running the gate by hand saw nothing"
    assert "NOT in the figures" in err


def test_the_skipped_skill_warning_stays_quiet_when_every_skill_parses(capsys):
    """A warning that fires on a clean catalogue is a warning nobody reads."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        skill = root / ".claude" / "skills" / "widget-forge" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(_doc(["name: widget-forge"], "# Forge"),
                         encoding="utf-8")
        _cfa.measure_skills(root)
    assert capsys.readouterr().err == ""


def test_the_context_floor_byte_count_did_not_move_on_the_live_catalogue():
    """The shared block KEEPS the newline before the closing fence; the regex
    that used to sit in `measure_skills` put it outside its group. The migration
    drops exactly one terminator to preserve the recorded number, so
    `config/context-floor-baseline.json` is unaffected by the grammar change.

    MEASURED 2026-08-29: 94 skills, +94 bytes without that line.
    """
    old = re.compile(r"\A---\n(.*?)\n---\n", re.S)
    total_old = 0
    for _path, text in read_sources(
            tracked_paths((".claude/skills/*/SKILL.md",)), errors="replace"):
        match = old.match(text)
        if match:
            total_old += len(match.group(1).encode("utf-8"))
    assert total_old > 0, "no SKILL.md matched the old grammar; test is vacuous"
    assert _cfa.measure_skills(ROOT)["frontmatter_bytes"] == total_old


def test_a_path_scoped_rule_with_a_spaced_fence_is_not_called_always_on():
    """No frontmatter means always-on by convention. A rule that HAS `paths:`
    but whose fence carries a space was read as having none, so a path-scoped
    rule was reported to the operator as loading in every session."""
    text = _doc(["paths:", "  - scripts/**"], "# Scoped", open_suffix=" ",
                close_suffix=" ")
    assert _cfa._is_always_on(text) is False


def test_a_council_transcript_with_a_tabbed_fence_keeps_its_mode():
    """`parse_transcript` reads `mode` out of this dict to decide whether the
    file IS a transcript. {} drops it from the aggregate entirely."""
    text = _doc(["mode: full", "timestamp: 2026-08-29T10:00"], "## Question\nX",
                open_suffix="\t", close_suffix="\t")
    assert _council._parse_frontmatter(text).get("mode") == "full"


def test_a_crm_contact_with_a_spaced_fence_still_yields_its_email_domain():
    """Every message from that employer counted as an unknown sender, in a
    report whose whole subject is known-versus-unknown."""
    text = _doc(["name: Dana Okonkwo", "email: dana@nimbus-freight.example"],
                "Notes.", open_suffix=" ", close_suffix=" ")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        contacts = root / "crm" / "contacts"
        contacts.mkdir(parents=True)
        (contacts / "dana-okonkwo.md").write_text(text, encoding="utf-8")
        with _pinned_data_root(root):
            domains = _ipr.load_known_crm_domains(root)
        assert domains == {"nimbus-freight.example"}, (
            "a fixture of one contact must yield exactly one domain; anything "
            "wider means the seam resolved somewhere other than the fixture")


# ------------------------------------------------------------------
# The leak wall. This is the sharpest one in the shard.
# ------------------------------------------------------------------


@pytest.mark.parametrize("suffix,label", [(" ", "trailing space"), ("\t", "tab")])
def test_the_leak_wall_sees_a_contact_whose_fence_carries_whitespace(suffix, label):
    """A contact record read as having no frontmatter contributed neither its
    e-mail nor its employer to the denylist, so the content-leak wall would scan
    public engine prose naming them and print clean.

    MEASURED 2026-08-29 before the fix: 5 crm- tokens for a canonical fence, 2
    for the same record with `--- `. The two survivors come from the FILENAME,
    in a different harvester; the person's own address and their employer were
    both absent.
    """
    text = _doc(["name: Dana Okonkwo",
                 "email: dana@nimbus-freight.example",
                 "company: Nimbus Freight Holdings"], "Notes.",
                open_suffix=suffix, close_suffix=suffix)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        contacts = root / "crm" / "contacts"
        contacts.mkdir(parents=True)
        (contacts / "dana-okonkwo.md").write_text(text, encoding="utf-8")
        tokens = build_denylist(root).tokens
    assert "dana@nimbus-freight.example" in tokens, label
    assert "nimbus freight holdings" in tokens, label


def test_the_leak_wall_names_a_contact_it_could_not_read(capsys):
    """A record with an opening fence and no close has fields this harvester
    cannot reach. Silence about it reads as coverage."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        contacts = root / "crm" / "contacts"
        contacts.mkdir(parents=True)
        (contacts / "broken.md").write_text("---\nemail: x@y.example\nno close\n",
                                            encoding="utf-8")
        build_denylist(root)
    err = capsys.readouterr().err
    assert "broken.md" in err
    # `"not" in err.lower()` stood here until 2026-09-01 and is satisfied by
    # "Notes", "nothing", "another" and every other word that contains the
    # three letters, so a line saying only "content-denylist: read 1 record"
    # would have passed it. The claim this test exists to hold is the SECOND
    # sentence of the warning: the record's fields are absent from the tokens.
    assert "no readable frontmatter" in err, err
    assert "NOT" in err and "denylist tokens" in err, err


def test_an_unreadable_contact_does_not_switch_the_whole_wall_off():
    """Raising here was tried and rejected. `build_denylist` catches a harvester
    exception, sets `degraded`, and stops -- and `scripts/content-guard.py`
    reads `degraded` as "no overlay" and SKIPS the scan with exit 0. One bad
    record must not disarm the layer."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        contacts = root / "crm" / "contacts"
        contacts.mkdir(parents=True)
        (contacts / "broken.md").write_text("---\nno close\n", encoding="utf-8")
        (contacts / "dana-okonkwo.md").write_text(
            _doc(["email: dana@nimbus-freight.example"], "Notes."),
            encoding="utf-8")
        denylist = build_denylist(root)
    assert denylist.degraded is False
    assert "dana@nimbus-freight.example" in denylist.tokens


# ------------------------------------------------------------------


def test_a_canopus_note_with_a_spaced_opening_fence_is_readable():
    """`_FENCE` accepted trailing whitespace on the CLOSING fence and not on the
    OPENING one. `canopus_check.py` takes `note_paths()` as its ENTIRE
    population, so one refused note aborts the check for every clause."""
    text = _doc(["slug: slice-07", "value: the clause"], "Prose.",
                open_suffix=" ", close_suffix=" ")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        note = root / "records" / "slices" / "slice-07.md"
        note.parent.mkdir(parents=True)
        note.write_text(text, encoding="utf-8")
        fields = _canopus.read_note(root, "slice-07")
    assert fields["slug"] == "slice-07"
    assert fields["body"] == "Prose."


@pytest.mark.parametrize("suffix,label", [(" ", "trailing space"), ("\t", "tab")])
def test_a_bundle_built_from_a_spaced_fence_still_parses_as_yaml(suffix, label):
    """With the fence unrecognised the WHOLE file took the body substitution,
    and the plain form closes the double-quoted `allowed-tools` scalar early.
    That is the defect `_REWRITE_SUB_YAML` exists to prevent, reintroduced
    through the splitter instead of the substitution.

    MEASURED 2026-08-29 on the pre-fix code: `yaml.safe_load` raised
    ParserError for both suffixes.
    """
    text = _doc(['allowed-tools: "Bash(python scripts/x.py:*)"'],
                "Run python scripts/x.py here.",
                open_suffix=suffix, close_suffix=suffix)
    out = _buildplugins.rewrite_script_paths(text)[0]
    fm, _body, kind = split_frontmatter(out)
    assert kind == FM_OK, label
    loaded = yaml.safe_load(fm)
    assert loaded["allowed-tools"] == 'Bash(python "${CLAUDE_PLUGIN_ROOT}"/scripts/x.py:*)'


def test_the_rewriter_preserves_the_input_bytes_it_does_not_rewrite():
    """The byte-preserving split is the point: a build generator that
    normalises a fence rewrites a file it was asked to copy."""
    text = _doc(["name: widget"], "No script references here.",
                open_suffix=" ", close_suffix="\t")
    out, count = _buildplugins.rewrite_script_paths(text)
    assert count == 0
    assert out == text


def test_frontmatter_is_not_audited_as_prose_through_a_spaced_fence():
    """`title: leveraging robust systems` produced two banned-vocab errors out
    of metadata. A false finding is worse than a missed one: the operator edits
    real prose to satisfy it."""
    text = _doc(["title: leveraging robust systems"], "Real prose here.",
                open_suffix=" ", close_suffix=" ")
    cleaned = _humanization.strip_markdown_noise(text)
    assert "leveraging" not in cleaned
    assert "Real prose here." in cleaned


# ============================================================
# The two that are NOT fixed, and why that is not a gap
# ============================================================

# Path-relative, so the assertion names the file rather than an import alias.
CRLF_ONLY_READERS = (
    "scripts/merge-contacts.py",
    "scripts/odin_pagerank.py",
)

# Every module in the shard-55 registry plus the two above, with the source they
# read from. A CR can only reach a pattern through `newline=""`.
READERS_THAT_OPEN_FILES = (
    "scripts/bridge_daemon/refreshers/inflight.py",
    "scripts/context-floor-audit.py",
    "scripts/council-aggregate.py",
    "scripts/inbox-pulse-report.py",
    "scripts/utils/content_denylist.py",
    "scripts/merge-contacts.py",
    "scripts/odin_pagerank.py",
    "scripts/utils/canopus_note.py",
    "scripts/dev/build-plugins.py",
    "scripts/humanization-check.py",
)


def test_no_reader_in_the_set_can_receive_a_cr():
    """The reachability claim behind leaving two readers alone, as a test.

    `Path.read_text()` and `open(path, encoding=...)` both decode in
    universal-newline mode: `\\r\\n` and a lone `\\r` both become `\\n`. Only
    `newline=""` (or reading bytes) preserves a CR. If someone adds one of those
    to a file in this list, the CRLF blind spot becomes reachable and this test
    goes red instead of the defect going unnoticed.

    Three ways to read bytes, not one. `Path.read_bytes()` is the obvious one
    and was the only one asked about until 2026-09-01; a BINARY MODE reaches the
    same place and is the spelling a reader is far likelier to acquire, because
    it arrives as an ordinary-looking `open(path, "rb")`. The mode argument sits
    in a different POSITION for the two callables this walks - second for the
    builtin `open`, FIRST for `Path.open` - so a check that read one index would
    report nonsense on the other, and both are read here by position and by
    keyword.
    """
    offenders = []

    def _mode_arg(node, name):
        """The mode string this call passes, or None. Position depends on the
        callable: `open(path, mode)` vs `Path(p).open(mode)`."""
        for kw in node.keywords:
            if kw.arg == "mode":
                return kw.value
        index = 0 if isinstance(node.func, ast.Attribute) else 1
        return node.args[index] if len(node.args) > index else None

    for rel in READERS_THAT_OPEN_FILES:
        source = (ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name not in ("read_text", "open", "read_bytes"):
                continue
            if name == "read_bytes":
                offenders.append(f"{rel}:{node.lineno} read_bytes()")
                continue
            if name == "open":
                mode = _mode_arg(node, name)
                if isinstance(mode, ast.Constant) and "b" in str(mode.value):
                    offenders.append(f"{rel}:{node.lineno} open(mode={mode.value!r})")
                elif mode is not None and not isinstance(mode, ast.Constant):
                    # A mode this cannot read is not a mode this may vouch for.
                    offenders.append(
                        f"{rel}:{node.lineno} open() with a computed mode "
                        f"({ast.unparse(mode)}); read it by hand")
            for kw in node.keywords:
                if kw.arg == "newline" and not (
                    isinstance(kw.value, ast.Constant) and kw.value.value is None
                ):
                    offenders.append(f"{rel}:{node.lineno} {name}(newline=...)")
    assert offenders == [], (
        "a reader in the frontmatter set now decodes without universal "
        "newlines, so a CR can reach its pattern: " + ", ".join(offenders)
    )


def test_the_universal_newline_claim_is_true_of_python_itself():
    """The test above is only worth anything if the premise holds. Assert it
    against the interpreter rather than against a comment."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "crlf.md"
        path.write_bytes(b"---\r\na: 1\r\n---\r\nBody.\r\n")
        assert "\r" not in path.read_text(encoding="utf-8")
        with open(path, encoding="utf-8") as handle:
            assert "\r" not in handle.read()
        with open(path, encoding="utf-8", newline="") as handle:
            assert "\r" in handle.read()


@pytest.mark.parametrize("rel", CRLF_ONLY_READERS)
def test_the_two_unfixed_readers_are_correct_on_every_reachable_document(rel):
    """Their regex diverges only on a CRLF document, which they cannot receive.
    On the three shapes that DO reach them, they agree with the shared grammar.
    """
    module = {"scripts/merge-contacts.py": _merge,
              "scripts/odin_pagerank.py": _pagerank}[rel]
    for variant, kwargs in VARIANTS.items():
        text = _doc(["name: Dana Okonkwo", "stage: warm"], "Notes.", **kwargs)
        match = module.FRONTMATTER_RE.match(text)
        expected, _body, kind = split_frontmatter(text)
        assert kind == FM_OK
        assert match is not None, f"{rel} refuses a {variant}"
        # The shared block keeps the newline before the closing fence; these two
        # put it outside their group. Compare the YAML, not the byte count.
        assert yaml.safe_load(match.group(1)) == yaml.safe_load(expected), variant


def test_teaching_merge_contacts_crlf_would_rewrite_the_file_it_merges():
    """The positive reason the CRLF case is left alone in ONE of the two.

    `merge-contacts.py` parses a record and writes it back. Its serializer emits
    `\\n`, so accepting `\\r\\n` on the read without emitting it on the write
    would convert a record's line endings on a merge asked to change one field.
    That is the same class of silent rewrite the module's own `[ \\t]*\\n`
    comment records as a past defect.
    """
    fields, _body = _merge.parse_frontmatter(
        _doc(["name: Dana Okonkwo", "stage: warm"], "Notes."))
    written = _merge.serialize_frontmatter(fields)
    assert "\r" not in written, (
        "the writer emits LF only, so a CRLF-accepting reader would convert a "
        "record's line endings on a merge asked to change one field")
    assert "name: Dana Okonkwo" in written


# ============================================================
# The byte-preserving splitter added for build-plugins
# ============================================================


@pytest.mark.parametrize("text", [
    "---\na: 1\n---\nBody\n",
    "--- \na: 1\n--- \nBody\n",
    "---\t\na: 1\n---\t\nBody\n",
    "---\r\na: 1\r\n---\r\nBody\r\n",
    "---\na: 1\n---\n\n\nBody\n",
    "---\na: 1\n---",
    "Just a body\n",
    "---\na: 1\nno closing fence\n",
    "",
])
def test_split_frontmatter_raw_is_byte_exact(text):
    """`front + rest == text`, always. A generator that rewrites one half and
    concatenates depends on this and on nothing else."""
    front, rest, _kind = split_frontmatter_raw(text)
    assert (front or "") + rest == text


def test_split_frontmatter_raw_agrees_with_split_frontmatter_on_what_is_yaml():
    """Two splitters in one module is exactly the duplication this shard exists
    to remove, so they must not be able to disagree about where the block is."""
    for variant, kwargs in VARIANTS.items():
        text = _doc(["a: 1", "b: two"], "Body.", **kwargs)
        block, _body, kind = split_frontmatter(text)
        front, _rest, raw_kind = split_frontmatter_raw(text)
        assert kind == raw_kind == FM_OK, variant
        assert yaml.safe_load(block) == yaml.safe_load(
            front.split("\n", 1)[1].rsplit("\n", 2)[0]), variant


def test_split_frontmatter_raw_gives_the_whole_text_as_body_when_there_is_none():
    """A caller must be able to treat the result as body without a second read."""
    front, rest, kind = split_frontmatter_raw("No fences here.\n")
    assert front is None
    assert rest == "No fences here.\n"
    assert kind != FM_OK


# ============================================================
# The registry from shard 55 only shrinks
# ============================================================


def test_the_shard_55_registry_lost_the_eight_readers_fixed_here(monkeypatch):
    """A site that needs fixing gets FIXED, not relabelled. The eight are gone
    from `KNOWN_REGEX_DIVERGENCE`; the two that remain are the CRLF-only pair
    whose reachability is pinned above."""
    # `monkeypatch.syspath_prepend`, never a bare insert: a bare one leaves
    # `<repo>/tests` on the path for the rest of the xdist worker, where every
    # test module becomes a top-level importable name for whatever runs later.
    monkeypatch.syspath_prepend(str(ROOT / "tests"))
    module = importlib.import_module(
        "test_nine_readers_that_looked_for_three_characters")
    assert set(module.KNOWN_REGEX_DIVERGENCE) == set(CRLF_ONLY_READERS)
    for rel, documents in module.KNOWN_REGEX_DIVERGENCE.items():
        assert set(documents) == {"CRLF throughout"}, rel


def test_no_new_regex_frontmatter_splitter_appeared(monkeypatch):
    """The behaviour-keyed sweep, run from here too. Shard 52's detector keyed
    on function NAMES and missed a copy spelled differently; shard 54's keyed on
    the call SHAPE and missed one written as a REGEX. This one compiles every
    frontmatter-shaped pattern in the tree and runs it."""
    monkeypatch.syspath_prepend(str(ROOT / "tests"))
    module = importlib.import_module(
        "test_nine_readers_that_looked_for_three_characters")
    seen = {}
    for rel, _line, pattern in module._frontmatter_regexes():
        divergence = module._regex_divergence(pattern)
        if divergence:
            seen.setdefault(rel, set()).update(divergence)
    undeclared = sorted(set(seen) - set(module.KNOWN_REGEX_DIVERGENCE))
    assert undeclared == [], (
        "new frontmatter regex disagreeing with the shared grammar: "
        + ", ".join(f"{rel} on {sorted(seen[rel])}" for rel in undeclared))
