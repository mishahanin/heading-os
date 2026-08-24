"""Shard 07-p2: the skill-router generator, and the Gmail draft builder.

Five findings, all confirmed, two of them against a gate that runs in CI and in
pre-commit.

1. THE ORPHAN DEADLOCK. `cmd_split_check` counts any `*.md` in
   `reference/skill-router/` that no current category backs as drift, exits 1,
   and prints "Run python scripts/generate-skill-router.py and commit". That
   command wrote the seven expected files and deleted nothing, so the drift it
   named could not be cleared by the command it named. One stray file -- a
   renamed category committed earlier, a leftover from a reverted branch, a
   hand-written note -- and CI and pre-commit were red forever, with an
   undocumented `rm` as the only way out.

   Reproduced end to end on 2026-08-25: `touch
   reference/skill-router/zz-audit-probe.md`, run the generator (reports "OK:
   already current", leaves the file), run `--check` (reports ORPHAN drift).

2. YAML 1.1 BOOLEANS REACHED THE ALWAYS-ON ROUTER TABLE. `compound` was the one
   `x-heading-routing` field passed through `str()` with no type check. PyYAML
   is YAML 1.1, so an unquoted `compound: No` parses to the boolean False and
   `str(False)` is "False" -- the Compound column of an always-on rule then read
   `| False |`. Worse than wrong: DETERMINISTICALLY wrong, so `--check`
   regenerated the same corrupt cell and passed. Every neighbouring field is
   type-checked precisely because this file has been burned this way before.

3. `name` WAS THE ONE FIELD TAKEN RAW. `name: 7` produced an int that reached
   `sorted(key=lambda r: r["name"])` and raised `TypeError: '<' not supported
   between 'str' and 'int'` -- an uncaught traceback rather than the curated
   `{rel}: {err}` line this gate exists to print. `name: 0` and `name: false`
   were quieter: both falsy, so `or child.name` swallowed a value the author had
   set deliberately.

4. `splice_region` COULD NOT BOOTSTRAP AN EMPTY REGION, AND MISREPORTED WHY. The
   pattern required a line between the markers, so markers on adjacent lines --
   which is exactly what the "add the markers" error message tells you to
   create -- matched zero times and raised "expected exactly one marker region,
   found 0". There was exactly one pair; the message sent the reader looking for
   duplicates that did not exist, and both --write and --check exited 2.

5. A REPLY TO A SUBJECT-LESS MESSAGE DEFEATED THE SUBJECT GUARD.
   `reply_subject("")` returned `"Re: "` -- truthy, trailing space -- which
   sailed past the `if not subject` check in `main()` that exists to refuse a
   subject-less draft. A Gmail message with no Subject header is ordinary, so a
   real draft to a real recipient could carry the subject line "Re: " alone.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ROUTER_SRC = ROOT / "scripts" / "generate-skill-router.py"
GMAIL_SRC = ROOT / "scripts" / "gmail-draft.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gr():
    return _load("skill_router_gen_k3", ROUTER_SRC)


@pytest.fixture(scope="module")
def gd():
    return _load("gmail_draft_k3", GMAIL_SRC)


def _skill(root: Path, name: str, *, category="Operations", compound='"No"',
           triggers='["a trigger"]', extra="", skill_name=None):
    d = root / name
    d.mkdir(parents=True)
    named = f"name: {skill_name}\n" if skill_name is not None else f"name: {name}\n"
    (d / "SKILL.md").write_text(
        "---\n"
        + named
        + "description: x\n"
        + extra
        + "x-heading-routing:\n"
        f"  category: {category}\n"
        f"  triggers: {triggers}\n"
        '  exclusions: ["N/A"]\n'
        f"  compound: {compound}\n"
        "  router: auto\n"
        "---\n\nbody\n",
        encoding="utf-8")
    return d


# ============================================================
# 1. The gate that could not be cleared by the command it named
# ============================================================

def test_the_writer_removes_an_orphan_the_checker_would_flag(gr, tmp_path,
                                                              monkeypatch, capsys):
    """The finding. Before this, `--check` stayed red forever."""
    monkeypatch.setattr(gr, "CATEGORY_FILE_DIR", tmp_path / "skill-router")
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    (tmp_path / "skill-router").mkdir()
    orphan = tmp_path / "skill-router" / "legacy.md"
    orphan.write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(gr, "ROUTER_FILE", _router_file(gr, tmp_path))
    gr.cmd_split_write([])
    assert not orphan.exists()


def test_the_removal_is_named_not_silent(gr, tmp_path, monkeypatch, capsys):
    """A tool that deletes a file the operator wrote must say which one."""
    monkeypatch.setattr(gr, "CATEGORY_FILE_DIR", tmp_path / "skill-router")
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    (tmp_path / "skill-router").mkdir()
    (tmp_path / "skill-router" / "legacy.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(gr, "ROUTER_FILE", _router_file(gr, tmp_path))
    gr.cmd_split_write([])
    out = capsys.readouterr().out
    assert "legacy.md" in out
    assert "orphan" in out.lower()


def test_a_real_category_file_is_never_removed(gr, tmp_path, monkeypatch):
    """UNLINK is what is watched, not the file's existence afterwards.

    Asserting the seven files exist when `cmd_split_write` returns proves
    nothing about the sweep: the writer recreates them two lines later, so a
    sweep that deleted all seven first looked identical from outside. The
    mutation that removed the "is this a real category?" guard survived
    exactly that test.
    """
    monkeypatch.setattr(gr, "CATEGORY_FILE_DIR", tmp_path / "skill-router")
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    (tmp_path / "skill-router").mkdir()
    for category in gr.CATEGORY_ORDER:
        (tmp_path / "skill-router"
         / f"{gr.category_slug(category)}.md").write_text("x", encoding="utf-8")
    orphan = tmp_path / "skill-router" / "legacy.md"
    orphan.write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(gr, "ROUTER_FILE", _router_file(gr, tmp_path))

    unlinked = []
    real_unlink = Path.unlink

    def _watched(self, *a, **k):
        unlinked.append(self.name)
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", _watched)
    gr.cmd_split_write([])
    assert unlinked == ["legacy.md"], unlinked


def test_the_sweep_removes_nothing_when_there_is_nothing_to_remove(gr, tmp_path,
                                                                    monkeypatch):
    monkeypatch.setattr(gr, "CATEGORY_FILE_DIR", tmp_path / "skill-router")
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    (tmp_path / "skill-router").mkdir()
    for category in gr.CATEGORY_ORDER:
        (tmp_path / "skill-router"
         / f"{gr.category_slug(category)}.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(gr, "ROUTER_FILE", _router_file(gr, tmp_path))

    unlinked = []
    real_unlink = Path.unlink
    monkeypatch.setattr(Path, "unlink",
                        lambda self, *a, **k: (unlinked.append(self.name),
                                               real_unlink(self, *a, **k))[1])
    gr.cmd_split_write([])
    assert unlinked == []


def test_a_non_markdown_file_is_left_alone(gr, tmp_path, monkeypatch):
    """The check only ever called `*.md` an orphan; nothing else is ours."""
    monkeypatch.setattr(gr, "CATEGORY_FILE_DIR", tmp_path / "skill-router")
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    (tmp_path / "skill-router").mkdir()
    keep = tmp_path / "skill-router" / "notes.txt"
    keep.write_text("mine", encoding="utf-8")
    monkeypatch.setattr(gr, "ROUTER_FILE", _router_file(gr, tmp_path))
    gr.cmd_split_write([])
    assert keep.is_file()


def _router_file(gr, tmp_path):
    """A minimal router file carrying exactly one marker region."""
    f = tmp_path / "skill-router.md"
    f.write_text(f"# Router\n\n{gr.MARKER_BEGIN}\nold\n{gr.MARKER_END}\n",
                 encoding="utf-8")
    return f


def test_the_writer_deletes_before_it_writes():
    """Order matters: a check run between the two must never see both."""
    tree = ast.parse(ROUTER_SRC.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "cmd_split_write")
    src = ast.unparse(fn)
    assert src.index("unlink") < src.index("render_category_file")


# ============================================================
# 2. The YAML boolean that reached an always-on rule
# ============================================================

@pytest.mark.parametrize("literal", ["No", "Yes", "on", "off", "TRUE", "false"])
def test_an_unquoted_yaml_boolean_is_refused(gr, tmp_path, monkeypatch, literal):
    """`compound: No` is the boolean False in YAML 1.1, and `str(False)` is
    "False" -- which used to render into the always-on router table."""
    import yaml
    assert isinstance(yaml.safe_load(f"c: {literal}")["c"], bool), literal
    monkeypatch.setattr(gr, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    _skill(tmp_path, "alpha", compound=literal)
    rows, errors = gr.load_routing_rows()
    assert rows == []
    assert any("compound" in e for e in errors)


def test_the_refusal_explains_the_yaml_trap(gr, tmp_path, monkeypatch):
    """An author who typed `No` needs to know why it is not the word No."""
    monkeypatch.setattr(gr, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    _skill(tmp_path, "alpha", compound="No")
    _rows, errors = gr.load_routing_rows()
    joined = " ".join(errors)
    # Asserting "YAML" and "quoted" was too loose: the surviving half of the
    # message still says "Unquoted No/Yes/On/Off are booleans in", so gutting
    # the REMEDY left both words in place. What has to survive is the thing an
    # author can act on -- the literal they should have written.
    assert 'compound: "No"' in joined
    assert "YAML 1.1" in joined


def test_a_quoted_no_is_accepted(gr, tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    _skill(tmp_path, "alpha", compound='"No"')
    rows, errors = gr.load_routing_rows()
    assert errors == []
    assert rows[0]["compound"] == "No"


def test_a_real_compound_description_is_accepted(gr, tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    _skill(tmp_path, "alpha", compound='"Yes: Meeting Prep, Deal Intel"')
    rows, errors = gr.load_routing_rows()
    assert errors == []
    assert rows[0]["compound"] == "Yes: Meeting Prep, Deal Intel"


def test_a_numeric_compound_is_refused(gr, tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    _skill(tmp_path, "alpha", compound="7")
    rows, errors = gr.load_routing_rows()
    assert rows == []
    assert any("compound" in e for e in errors)


def test_no_row_ever_carries_the_word_False(gr, tmp_path, monkeypatch):
    """The rendered symptom, pinned directly."""
    monkeypatch.setattr(gr, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    _skill(tmp_path, "alpha", compound="No")
    rows, _errors = gr.load_routing_rows()
    assert not any(r["compound"] in ("False", "True") for r in rows)


# ============================================================
# 3. The name that was never type-checked
# ============================================================

def test_a_numeric_name_is_a_curated_error_not_a_traceback(gr, tmp_path,
                                                            monkeypatch):
    """It reached `sorted` and raised TypeError with no per-file diagnostic."""
    monkeypatch.setattr(gr, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    _skill(tmp_path, "alpha", skill_name="7")
    rows, errors = gr.load_routing_rows()
    assert rows == []
    assert any("name" in e for e in errors)


def test_the_name_error_names_the_file(gr, tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    _skill(tmp_path, "alpha", skill_name="7")
    _rows, errors = gr.load_routing_rows()
    assert any("alpha" in e for e in errors)


@pytest.mark.parametrize("literal", ["7", "false", "3.5", "[a, b]"])
def test_no_non_string_name_reaches_a_row(gr, tmp_path, monkeypatch, literal):
    monkeypatch.setattr(gr, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    _skill(tmp_path, "alpha", skill_name=literal)
    rows, errors = gr.load_routing_rows()
    assert rows == []
    assert errors


def test_two_skills_in_one_category_still_sort(gr, tmp_path, monkeypatch):
    """The crash needed two rows to compare; one alone never sorted."""
    monkeypatch.setattr(gr, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    _skill(tmp_path, "alpha")
    _skill(tmp_path, "beta")
    rows, errors = gr.load_routing_rows()
    assert errors == []
    assert gr.render_core_index(rows)          # must not raise


def test_a_missing_name_still_falls_back_to_the_directory(gr, tmp_path,
                                                           monkeypatch):
    """The `or child.name` fallback is kept; only non-strings are refused."""
    d = tmp_path / "gamma"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\ndescription: x\nx-heading-routing:\n"
        "  category: Operations\n  triggers: [\"t\"]\n"
        '  exclusions: ["N/A"]\n  compound: "No"\n  router: auto\n---\n\nbody\n',
        encoding="utf-8")
    monkeypatch.setattr(gr, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(gr, "ROOT", tmp_path)
    rows, errors = gr.load_routing_rows()
    assert errors == []
    assert rows[0]["name"] == "gamma"


# ============================================================
# 4. The empty region, and the message that misdiagnosed it
# ============================================================

def test_an_empty_region_can_be_filled(gr):
    """Markers on adjacent lines is what "add the markers" produces."""
    text = f"# Router\n\n{gr.MARKER_BEGIN}\n{gr.MARKER_END}\n"
    out = gr.splice_region(text, "NEW")
    assert "NEW" in out


def test_a_populated_region_is_still_replaced(gr):
    text = f"# Router\n\n{gr.MARKER_BEGIN}\nold\n{gr.MARKER_END}\n"
    out = gr.splice_region(text, "NEW")
    assert "NEW" in out
    assert "old" not in out


def test_everything_outside_the_markers_survives(gr):
    text = f"HEAD\n{gr.MARKER_BEGIN}\nold\n{gr.MARKER_END}\nTAIL\n"
    out = gr.splice_region(text, "NEW")
    assert out.startswith("HEAD\n")
    assert out.endswith("TAIL\n")


def test_a_missing_marker_still_raises(gr):
    with pytest.raises(ValueError, match="sentinel markers"):
        gr.splice_region("# Router\nno markers here\n", "NEW")


def test_two_regions_say_two_not_zero(gr):
    """The old message said "found 0" for the opposite problem."""
    text = (f"{gr.MARKER_BEGIN}\na\n{gr.MARKER_END}\n"
            f"{gr.MARKER_BEGIN}\nb\n{gr.MARKER_END}\n")
    with pytest.raises(ValueError) as exc:
        gr.splice_region(text, "NEW")
    assert "2 marker regions" in str(exc.value)
    assert "found 0" not in str(exc.value)


def test_the_duplicate_message_says_what_to_do(gr):
    text = (f"{gr.MARKER_BEGIN}\na\n{gr.MARKER_END}\n"
            f"{gr.MARKER_BEGIN}\nb\n{gr.MARKER_END}\n")
    with pytest.raises(ValueError, match="Remove the extra"):
        gr.splice_region(text, "NEW")


# ============================================================
# 5. The reply subject that defeated its own guard
# ============================================================

@pytest.mark.parametrize("parent", ["", "   ", "\t\n"])
def test_a_subjectless_parent_yields_no_subject(gd, parent):
    """`"Re: "` is truthy, so the guard in main() never fired."""
    assert gd.reply_subject(parent) == ""


def test_the_bare_re_prefix_is_never_produced(gd):
    assert gd.reply_subject("").strip() != "Re:"
    assert gd.reply_subject("") != "Re: "


def test_an_ordinary_subject_is_prefixed(gd):
    assert gd.reply_subject("Quarterly numbers") == "Re: Quarterly numbers"


def test_an_existing_prefix_is_not_doubled(gd):
    assert gd.reply_subject("Re: Quarterly numbers") == "Re: Quarterly numbers"


def test_the_prefix_check_ignores_case(gd):
    assert gd.reply_subject("RE: Numbers") == "RE: Numbers"


def test_surrounding_whitespace_is_trimmed(gd):
    assert gd.reply_subject("  Numbers  ") == "Re: Numbers"


class _FakeGmail:
    """The three call chains `main` makes before it decides on a subject."""

    def __init__(self, parent_subject):
        self._subject = parent_subject
        self.drafts_created = []

    def users(self):
        return self

    def getProfile(self, userId=None):                    # noqa: N803 - Gmail API
        return self

    def messages(self):
        return self

    def get(self, userId=None, id=None, format=None):     # noqa: A002, N803
        headers = [{"name": "Message-ID", "value": "<p@x>"}]
        if self._subject is not None:
            headers.append({"name": "Subject", "value": self._subject})
        return _Executes({"payload": {"headers": headers}, "threadId": "t1"})

    def drafts(self):
        return self

    def create(self, userId=None, body=None):             # noqa: N803
        self.drafts_created.append(body)
        return _Executes({"id": "d1"})

    def execute(self):
        return {"emailAddress": "me@example.com"}


class _Executes:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


def _run_main(gd, monkeypatch, tmp_path, parent_subject, argv_extra=()):
    body = tmp_path / "letter.md"
    body.write_text("hello\n", encoding="utf-8")
    fake = _FakeGmail(parent_subject)
    monkeypatch.setattr("scripts.utils.gmail_auth.get_service", lambda: fake)
    monkeypatch.setattr(gd.sys, "argv", [
        "gmail-draft.py", "--to", "a@b.c", "--body-file", str(body),
        "--reply-to-message", "m1", *argv_extra])
    return gd.main(), fake


def test_main_refuses_rather_than_drafting_a_bare_re(gd, monkeypatch, tmp_path):
    """BEHAVIOURAL. The first version of this test counted `if not subject`
    occurrences in `main` and required at least two -- but `main` already had
    three, so disabling the reply-path guard left two and the test passed. A
    count is a proxy for the behaviour, and this proxy could not fail.
    """
    code, fake = _run_main(gd, monkeypatch, tmp_path, parent_subject="")
    assert code == 2
    assert fake.drafts_created == [], "no draft may be created with no subject"


def test_a_parent_with_no_subject_header_at_all_is_refused(gd, monkeypatch,
                                                            tmp_path):
    code, fake = _run_main(gd, monkeypatch, tmp_path, parent_subject=None)
    assert code == 2
    assert fake.drafts_created == []


def test_the_refusal_tells_the_operator_what_to_pass(gd, monkeypatch, tmp_path,
                                                      capsys):
    _run_main(gd, monkeypatch, tmp_path, parent_subject="")
    assert "--subject" in capsys.readouterr().err


def test_the_refusal_does_not_contradict_the_flag_that_was_given(gd, monkeypatch,
                                                                  tmp_path, capsys):
    """WHY the inner guard exists at all, now that the outer one exists too.

    Deleting the reply-path re-check changes neither the exit code nor the
    draft count: `subject` stays "" and the OUTER `if not subject` catches it.
    So both of the obvious behavioural assertions pass without it, and the
    mutation survived them. What changes is the sentence the operator reads.

    The outer message is "--subject is required unless --reply-to-message is
    given" -- printed to someone who DID give --reply-to-message. An error
    that contradicts the command just typed sends the reader to check their
    own invocation instead of the parent message, which is where the problem
    is. The inner guard's whole value is that sentence, so that sentence is
    what gets pinned.
    """
    _run_main(gd, monkeypatch, tmp_path, parent_subject="")
    err = capsys.readouterr().err
    assert "no Subject" in err, "must name the parent as the cause"
    assert "unless --reply-to-message is given" not in err, (
        "must not tell the operator to pass a flag they already passed")


def test_an_explicit_subject_still_drafts_the_reply(gd, monkeypatch, tmp_path):
    """The guard must refuse the empty case only."""
    code, fake = _run_main(gd, monkeypatch, tmp_path, parent_subject="",
                           argv_extra=("--subject", "Picked up offline"))
    assert code == 0
    assert len(fake.drafts_created) == 1


def test_a_parent_with_a_subject_still_drafts_the_reply(gd, monkeypatch,
                                                         tmp_path):
    code, fake = _run_main(gd, monkeypatch, tmp_path,
                           parent_subject="Quarterly numbers")
    assert code == 0
    assert len(fake.drafts_created) == 1
