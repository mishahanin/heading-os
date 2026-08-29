#!/usr/bin/env python3
"""A quarantined state file landed under a name no `.gitignore` rule matched.

`_quarantine_corrupt_queue` in `scripts/bridge_daemon/sources/action_queue.py`
renames an unreadable `queue.json` aside so the next write cannot erase the
pending cards. The rename is right. The destination was not: it was built from
the live file's own name, so the wreck stayed in the live directory as
`queue.json.corrupt-<stamp>`.

MEASURED 2026-08-29 with `git check-ignore` in the data overlay
(`.heading-os-data`):

    outputs/operations/action-queue/queue.json                        IGNORED
    outputs/operations/action-queue/queue.json.lock                   IGNORED
    outputs/operations/action-queue/disposition-log.jsonl             IGNORED
    outputs/operations/action-queue/queue.json.corrupt-20260829T120000Z  NOT-IGNORED

`queue.json` is ignored for a reason written beside the rule: it carries
recipient addresses, subjects, and whole drafted email bodies for cards the CEO
has not approved. A byte-for-byte copy of it under an untracked-and-unignored
name is the same content with the protection removed. `scripts/push-all.py`
commits with `git add -A`, so the first corrupt-queue event would have put
un-sent drafts into the private repo's permanent history. The quarantine fires
from the READ path (`list_action_queue`, one `GET /action-queue`), not only from
a write, so one authenticated read over a torn file was enough.

Three writers had the shape. Measured the same day:

    scripts/bridge_daemon/sources/action_queue.py  queue.json.corrupt-<stamp>      DATA
    scripts/email-intelligence.py                  state.json.corrupt-<stamp>      DATA
    scripts/run-skill-eval.py                      benchmark.json.corrupt          ENGINE

The third sits inside a tracked skill directory of the PUBLIC engine repo, where
nothing was ignored at all. Two other writers already had it right, and they are
the fix: `checkpoint-save.py` puts an unredacted handoff in
`outputs/operations/handoff-archive/.quarantine/`, and `fireside-bot.py` writes
its schedule backup inside a directory the overlay ignores whole. Both are safe
because a DIRECTORY is ignored, not because someone remembered a filename.

So `scripts/utils/quarantine.py` is now the only writer and it always lands in a
`.quarantine/` sibling, and both repositories carry `**/.quarantine/`. That is
the same generalisation `outputs/**/*.lock` needed on 2026-08-27, after the lock
sidecars had been named one path at a time until a third one appeared with no
rule and `git add -A` committed it. Quarantine artifacts got no equivalent then;
this is it.

The second half of this file is the sweep. Every place in `scripts/` and
`.claude/` that builds a persistent filename out of another path's name is
declared with the reason it is safe, so writer number four cannot arrive
silently.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon.sources import action_queue as aq  # noqa: E402
from scripts.utils.quarantine import (  # noqa: E402
    QUARANTINE_DIRNAME,
    quarantine_dir,
    quarantine_file,
    quarantine_ref,
    quarantine_target,
)
from scripts.utils.workspace import get_data_root  # noqa: E402
from tests.repo_files import tracked_paths  # noqa: E402


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ei = _load("email-intelligence", "email_intelligence_wreck")


# ============================================================
# What git actually says
# ============================================================

def _ignored(repo: Path, path) -> bool:
    """True when git would refuse to track `path`.

    `check-ignore` exits 0 on a match and 1 on none; anything above that is a
    real error (not a repository, git missing) and must NOT read as False --
    degrading it into "nothing is ignored" is how a guard passes over a tree it
    never looked at.
    """
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(path)],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode > 1:
        pytest.fail(f"git check-ignore failed in {repo}: {result.stderr.strip()}")
    return result.returncode == 0


def _temp_repo(tmp_path: Path, ignore_text: str) -> Path:
    """A real git repository carrying `ignore_text` and nothing else.

    The rules are measured by asking git, not by reading `.gitignore` with a
    regex. A rule that exists and does not match the path it was written for is
    the exact failure here -- `queue.json` had a rule, `queue.json.corrupt-...`
    was one character-class away from it -- and only git can tell the two apart.
    """
    repo = tmp_path / "probe-repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True,
                   capture_output=True)
    (repo / ".gitignore").write_text(ignore_text, encoding="utf-8")
    return repo


ENGINE_IGNORE = (ROOT / ".gitignore").read_text(encoding="utf-8")

# Paths that exist nowhere on disk, so no real file can make these pass.
QUARANTINED = (
    "outputs/operations/action-queue/.quarantine/queue.json.corrupt-20260829T120000Z",
    "outputs/operations/email-intelligence/.quarantine/state.json.corrupt-20260829T120000Z",
    ".claude/skills/a-skill-not-yet-written/evals/.quarantine/benchmark.json.corrupt-20260829T120000Z",
    ".quarantine/at-the-root.json.corrupt-20260829T120000Z",
    "a/b/c/d/e/f/.quarantine/deep.json.broken-20260829T120000Z",
)


@pytest.mark.parametrize("wreck", QUARANTINED)
def test_the_engine_rules_ignore_a_quarantined_wreck_at_any_depth(tmp_path, wreck):
    repo = _temp_repo(tmp_path, ENGINE_IGNORE)
    assert _ignored(repo, repo / wreck), (
        f"{wreck} would be committed. `**/.quarantine/` is the whole mechanism: "
        f"the writer picks the directory, the rule covers every state file, "
        f"including ones nobody has written yet."
    )


def test_the_probe_repo_can_say_no(tmp_path):
    """The other direction, and it is not decoration.

    A `_temp_repo` that failed to write `.gitignore`, or an `_ignored` that
    returned True on error, would make every case above pass while measuring
    nothing. This asks about the OLD wreck name in the engine's own site -- the
    sibling form that caused the incident -- and about an ordinary source file,
    and requires a No for both.

    The engine's site is the skill-eval baseline rather than the action queue,
    because the engine ignores `/outputs/` whole ("data dirs never belong in the
    engine") and would answer yes for the wrong reason. `.claude/skills/` is
    tracked, which is exactly why `benchmark.json.corrupt` was the worst of the
    three: a wreck in a public repository with no rule anywhere near it.
    """
    repo = _temp_repo(tmp_path, ENGINE_IGNORE)
    assert not _ignored(
        repo,
        repo / ".claude/skills/a-skill/evals/benchmark.json.corrupt-20260829T120000Z",
    ), "the sibling form is the defect; if it reads as ignored the probe is broken"
    assert not _ignored(repo, repo / "scripts/utils/quarantine.py")


def test_an_empty_rule_set_ignores_none_of_them(tmp_path):
    """Anti-vacuity for the parametrized case above: the rule text is what makes
    it pass, not `check-ignore` answering yes to everything."""
    bare = _temp_repo(tmp_path, "# no rules at all\n")
    assert [w for w in QUARANTINED if _ignored(bare, bare / w)] == []


def test_removing_the_one_line_is_what_breaks_it(tmp_path):
    """The rule under test, isolated by deleting it.

    Two of the five paths above sit under `outputs/`, which the engine already
    drops whole ("data dirs never belong in the engine"), so they would pass
    with `**/.quarantine/` absent and say nothing about it. Here the line is
    removed from an otherwise identical rule set and the three paths OUTSIDE
    `outputs/` must go back to being committable -- and be ignored again with
    the line restored.
    """
    without = "\n".join(line for line in ENGINE_IGNORE.splitlines()
                        if line.strip() != "**/.quarantine/") + "\n"
    assert without != ENGINE_IGNORE, "the line was not found to remove"
    stripped = _temp_repo(tmp_path / "a", without)
    restored = _temp_repo(tmp_path / "b", ENGINE_IGNORE)
    outside_outputs = [w for w in QUARANTINED if not w.startswith("outputs/")]
    assert len(outside_outputs) == 3, outside_outputs
    for wreck in outside_outputs:
        assert not _ignored(stripped, stripped / wreck), (
            f"{wreck} is ignored by some OTHER rule, so the case measures "
            f"nothing about `**/.quarantine/`")
        assert _ignored(restored, restored / wreck)


def test_the_engine_repository_itself_ignores_a_quarantined_wreck():
    """The temp repo proves the RULES work. This proves the engine carries them."""
    if not (ROOT / ".git").exists():
        pytest.skip("engine is not a git checkout here")
    for wreck in QUARANTINED:
        assert _ignored(ROOT, ROOT / wreck), wreck


def test_the_data_overlay_ignores_a_quarantined_wreck():
    """The overlay is where the draft bodies live, so it carries the rule too.

    Skipped on a CI runner, which has no overlay -- and the engine test above
    still runs there, so the skip cannot silence the whole file.
    """
    data = Path(get_data_root())
    if not (data / ".git").exists():
        pytest.skip(f"{data} is not a git checkout here")
    for wreck in QUARANTINED[:2] + (
        "outputs/operations/a-tool-not-yet-written/.quarantine/state.json.corrupt-x",
        "datastore/anywhere/.quarantine/whatever.bak",
    ):
        assert _ignored(data, data / wreck), (
            f"{wreck} would be committed to the private repo. The overlay is "
            f"back to naming wreck files one path at a time."
        )


def test_the_two_repositories_agree_on_the_directory_name():
    """One name, spelled in three places. A rename in `quarantine.py` that did
    not reach a `.gitignore` would restore the defect quietly."""
    assert QUARANTINE_DIRNAME == ".quarantine"
    assert "**/.quarantine/" in ENGINE_IGNORE
    data = Path(get_data_root())
    if not (data / ".gitignore").exists():
        pytest.skip(f"{data} has no .gitignore here")
    assert "**/.quarantine/" in (data / ".gitignore").read_text(encoding="utf-8")


# ============================================================
# The real writers, driven
# ============================================================

def test_a_corrupt_queue_lands_in_the_quarantine_directory(tmp_path, caplog):
    """The measured path, end to end: a torn `queue.json`, the real reader."""
    q = tmp_path / aq.QUEUE_FILE
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text('{"actions": [{"id": "card-1", "to": "buyer@example.test"',
                 encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        assert aq._load_queue(tmp_path)["actions"] == []

    assert not q.exists()
    beside = [p.name for p in q.parent.iterdir() if p.name != QUARANTINE_DIRNAME]
    assert beside == [], f"a wreck was left in the live directory: {beside}"
    wrecks = list((q.parent / QUARANTINE_DIRNAME).glob("queue.json.corrupt-*"))
    assert len(wrecks) == 1, list((q.parent / QUARANTINE_DIRNAME).iterdir())
    assert "buyer@example.test" in wrecks[0].read_text(encoding="utf-8"), (
        "the bytes must survive; that is what the quarantine is for")
    assert ".quarantine/queue.json.corrupt-" in caplog.text, (
        "the log must name where the cards went")


def test_a_corrupt_email_intel_state_lands_in_the_quarantine_directory(tmp_path, capsys):
    """The sibling writer, driven the same way."""
    path = tmp_path / "state.json"
    path.write_text('{"processed_message_ids": ["<a@31c.io>"', encoding="utf-8")

    state = ei.StateManager(path=path)

    assert state.data["processed_message_ids"] == []
    assert "unusable" in capsys.readouterr().err
    assert not path.exists()
    beside = [p.name for p in tmp_path.iterdir() if p.name != QUARANTINE_DIRNAME]
    assert beside == [], f"a wreck was left in the live directory: {beside}"
    kept = list((tmp_path / QUARANTINE_DIRNAME).glob("state.json.corrupt-*"))
    assert len(kept) == 1
    assert "<a@31c.io>" in kept[0].read_text(encoding="utf-8")


def test_a_healthy_queue_creates_no_quarantine_directory(tmp_path):
    """The mirror. A reader that quarantined unconditionally would pass every
    test above and move the live store on every boot."""
    q = tmp_path / aq.QUEUE_FILE
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text(json.dumps({"version": 1, "actions": [{"id": "a"}]}),
                 encoding="utf-8")
    assert aq._load_queue(tmp_path)["actions"] == [{"id": "a"}]
    assert q.exists()
    assert not (q.parent / QUARANTINE_DIRNAME).exists()


def test_the_wreck_of_a_private_queue_is_not_world_readable(tmp_path):
    """`queue.json` is written 0600 because of what is in it. `os.replace`
    preserves the file's mode; the directory is narrowed to match, so the wreck
    is no easier to read than the original was."""
    q = tmp_path / aq.QUEUE_FILE
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text("{torn", encoding="utf-8")
    os.chmod(q, 0o600)
    aq._load_queue(tmp_path)
    holder = q.parent / QUARANTINE_DIRNAME
    assert holder.stat().st_mode & 0o077 == 0, oct(holder.stat().st_mode)
    wreck = next(holder.iterdir())
    assert wreck.stat().st_mode & 0o077 == 0, oct(wreck.stat().st_mode)


def test_the_skill_eval_baseline_routes_through_the_helper():
    """`run-skill-eval.py` writes its wreck inside a TRACKED directory of the
    public engine, so it is the site with the least margin. Driving it needs a
    whole graded eval run, so the call is pinned structurally instead: the old
    literal is gone and the helper is called.
    """
    source = (ROOT / "scripts" / "run-skill-eval.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)
             and n.func.id == "quarantine_file"]
    assert len(calls) == 1, "the benchmark wreck no longer goes through the helper"
    # AST, not substring: the comment above the call quotes the broken name on
    # purpose, and a substring check would fire on the explanation of the defect.
    assert [s for s in wreck_name_literals(source) if ".corrupt" in s] == []


# ============================================================
# The helper itself
# ============================================================

def test_the_target_is_inside_a_quarantine_sibling(tmp_path):
    target = quarantine_target(tmp_path / "deep" / "state.json")
    assert target.parent == tmp_path / "deep" / QUARANTINE_DIRNAME
    assert target.name.startswith("state.json.corrupt-")


def test_the_directory_helper_agrees_with_the_target(tmp_path):
    p = tmp_path / "state.json"
    assert quarantine_target(p).parent == quarantine_dir(p)


def test_two_wrecks_in_the_same_second_do_not_clobber_each_other(tmp_path):
    """The quarantine exists to stop one file replacing another. A colliding
    stamp inside it would be the same loss, one directory down."""
    for body in ("{bad one", "{bad two"):
        p = tmp_path / "state.json"
        p.write_text(body, encoding="utf-8")
        quarantine_file(p)
    kept = sorted(x.read_text(encoding="utf-8")
                  for x in (tmp_path / QUARANTINE_DIRNAME).iterdir())
    assert kept == ["{bad one", "{bad two"], kept


def test_a_caller_may_name_the_kind(tmp_path):
    p = tmp_path / "schedule.json"
    p.write_text("{}", encoding="utf-8")
    assert quarantine_file(p, "superseded").name.startswith("schedule.json.superseded-")


def test_a_move_that_cannot_happen_raises_rather_than_reporting_success(tmp_path):
    """Both callers catch OSError and say the bytes were NOT saved. A helper
    that swallowed the failure and returned a path would make them print a
    recovery location for a file that is not there."""
    with pytest.raises(OSError):
        quarantine_file(tmp_path / "never-existed.json")


def test_the_log_reference_names_the_directory_and_not_the_tree(tmp_path):
    """A daemon log line should say where the wreck is without printing the
    operator's absolute data path into it."""
    ref = quarantine_ref(tmp_path / "outputs" / QUARANTINE_DIRNAME / "queue.json.corrupt-x")
    assert ref == ".quarantine/queue.json.corrupt-x"
    assert str(tmp_path) not in ref


# ============================================================
# And every other derived sidecar name is declared
# ============================================================
#
# Two shapes reach the same hazard, so one sweep judges both.
#
#   literal  - a string constant naming a wreck suffix (`.corrupt-`, `.bak`,
#              `.mutbak`). This is how all three defective writers spelled it.
#   derived  - `with_name`/`with_suffix` given an f-string or a concatenation,
#              i.e. a filename built out of another path's name.
#
# `.tmp` and `.lock` are exempt from the derived shape and only there: a tmp
# file is unlinked by the atomic write that made it, and a lock sidecar is
# already governed by `outputs/**/*.lock` plus
# `tests/test_lock_sidecars_are_never_tracked.py`. Everything else persists, and
# a file that persists needs to be somewhere git has an opinion about.
#
# Scope is `scripts/` and `.claude/` -- the production writers. Test fixtures
# spell wreck names on purpose and are judged by the tests they belong to.

WRECK_MARKER = re.compile(
    r"\.(corrupt|bak|backup|broken|damaged|dead|mutbak|old|orig|prev|quarantine"
    r"|rej|salvage|save|wreck)\b"
)
EXEMPT_DERIVED_SUFFIXES = (".tmp", ".lock")

DECLARED_SIDECAR_SITES = {
    (".claude/hooks/checkpoint-save.py", "literal", ".quarantine"):
        "the writer that already had it right, and the precedent for the fix: an "
        "unredacted handoff goes to handoff-archive/.quarantine/, ignored whole",
    ("scripts/utils/quarantine.py", "literal", ".quarantine"):
        "the one place the directory name is spelled for every other writer",
    ("scripts/fireside-bot.py", "literal", ".bak.json"):
        "schedule.pre-<date>.bak.json lands in "
        "datastore/operations/tribe/fireside-state/, which the data overlay "
        "ignores whole and the engine drops with /datastore/ -- measured IGNORED "
        "in both on 2026-08-29, so the directory already does this rule's job",
    ("scripts/updaters/cliproxyapi_update.py", "literal", ".bak"):
        "CPX_DIR is Path.home()/'cliproxyapi'; the rolled-back binary is outside "
        "both repositories and no git tree can see it",
    ("scripts/updaters/cliproxyapi_update.py", "derived", "BIN.name + '.incoming'"):
        "same directory, same reason: outside both repositories",
    ("scripts/utils/mutation_harness.py", "literal", ".mutbak"):
        "restored by shutil.move in a finally, and left VISIBLE on purpose -- a "
        "mutation run that litters must show up in `git status`, which is how the "
        "2026-08 littering incident was caught; hiding it in .quarantine/ would "
        "trade a data-leak fix for a measurement one",
    ("scripts/utils/mutation_harness.py", "derived", "target.suffix + '.mutbak'"):
        "the same site seen through the other shape",
    ("scripts/datastore-extract.py", "derived", "filepath.stem + '-extract.md'"):
        "an extraction OUTPUT, not a wreck: it is the deliverable, it is meant to "
        "be tracked, and the source file is tracked beside it",
    ("scripts/datastore-extract.py", "derived",
     "f'{filepath.stem}-{suffix}-extract.md'"):
        "the multi-part form of the same output",
    ("scripts/utils/crm.py", "derived", "f'{base}-{suffix}'"):
        "stamped_backup_path, for merge-contacts and transfer-contact. The "
        "original crm/contacts/<name>.md is TRACKED, not ignored, and both tools "
        "COMMIT the backup deliberately -- the archival is the feature. Nothing "
        "here is a copy of an ignored file, so the defect shape is absent",
}


def wreck_name_literals(source: str) -> list[str]:
    """Every non-docstring string constant that names a wreck suffix.

    Docstrings are excluded because a file must be able to describe the defect
    it fixes. Long or space-bearing constants are excluded for the same reason:
    they are prose, not filenames.
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                              ast.AsyncFunctionDef))
                and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            docstrings.add(id(body[0].value))
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstrings:
            continue
        value = node.value
        if len(value) > 60 or " " in value or "\n" in value:
            continue
        if WRECK_MARKER.search(value):
            found.append(value)
    return found


def derived_sidecar_expressions(source: str) -> list[str]:
    """Every `with_name`/`with_suffix` whose argument is built from another name.

    A plain literal argument (`path.with_suffix('.pdf')`) renames a file to a
    fixed name and cannot produce a sidecar of the original; an f-string or a
    concatenation is the shape that does.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        func = getattr(node, "func", None)
        if not (isinstance(node, ast.Call) and isinstance(func, ast.Attribute)
                and func.attr in ("with_name", "with_suffix")
                and len(node.args) == 1):
            continue
        arg = node.args[0]
        if not isinstance(arg, (ast.JoinedStr, ast.BinOp)):
            continue
        literals = [n.value for n in ast.walk(arg)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        if any(lit.endswith(EXEMPT_DERIVED_SUFFIXES) for lit in literals):
            continue
        found.append(ast.unparse(arg))
    return found


def sidecar_sites(corpus) -> set[tuple[str, str, str]]:
    """(relative path, shape, token) for every derived-name site in `corpus`.

    `corpus` is a sequence of (relative path, source). A set, not a list: the
    same token on two lines of one file is one site to argue about, and line
    numbers drift on every edit above them.
    """
    sites: set[tuple[str, str, str]] = set()
    for rel, source in corpus:
        try:
            literals = wreck_name_literals(source)
            derived = derived_sidecar_expressions(source)
        except SyntaxError:  # pragma: no cover - another test's job
            continue
        sites.update((rel, "literal", value) for value in literals)
        sites.update((rel, "derived", value) for value in derived)
    return sites


def undeclared_sidecar_sites(corpus, declared) -> list[str]:
    return sorted(f"{rel}  {shape} {token!r}"
                  for rel, shape, token in sidecar_sites(corpus)
                  if (rel, shape, token) not in declared)


def stale_declarations(declared, live) -> list:
    return sorted(key for key in declared if key not in live)


def declarations_without_a_reason(declared) -> list:
    return sorted(key for key, why in declared.items() if not why.strip())


# --- the rules, on synthetic input, in both directions ------------------
#
# Over the live tree all three are green by construction, so deleting the line
# that COLLECTS a violation changes no result there and the mutation survives.
# That has happened twice in this repository already. These measure the rules.

_SYNTHETIC = [
    ("a/declared.py", "dest = p.with_name(f'{p.name}.corrupt-{stamp}')\n"),
    ("b/undeclared.py", "shutil.move(src, str(src) + '.broken')\n"),
    ("c/innocent.py",
     "tmp = p.with_suffix(p.suffix + '.tmp')\n"
     "lock = p.with_name(p.name + '.lock')\n"
     "pdf = p.with_suffix('.pdf')\n"),
]
_SYNTHETIC_REGISTRY = {
    ("a/declared.py", "literal", ".corrupt-"): "declared in the fixture",
    ("a/declared.py", "derived", "f'{p.name}.corrupt-{stamp}'"):
        "the same site through the other shape",
}


def test_the_rule_names_a_wreck_literal_with_no_declaration():
    found = undeclared_sidecar_sites(_SYNTHETIC, _SYNTHETIC_REGISTRY)
    assert found == ["b/undeclared.py  literal '.broken'"], found


def test_the_rule_is_silent_when_everything_is_declared():
    """The other direction. A rule that always fires is as useless as one that
    never does, and only the pair separates them."""
    registry = dict(_SYNTHETIC_REGISTRY)
    registry[("b/undeclared.py", "literal", ".broken")] = "declared too"
    assert undeclared_sidecar_sites(_SYNTHETIC, registry) == []


def test_the_rule_leaves_tmp_and_lock_sidecars_alone():
    """Both are transient or governed elsewhere. A rule that flagged them would
    demand thirty declarations of no consequence and get switched off."""
    assert derived_sidecar_expressions(_SYNTHETIC[2][1]) == []
    assert wreck_name_literals(_SYNTHETIC[2][1]) == []


def test_the_rule_leaves_a_fixed_rename_alone():
    """`with_suffix('.pdf')` renames to a fixed name; it cannot make a sidecar
    of the original, which is the shape being hunted."""
    assert derived_sidecar_expressions("out = src.with_suffix('.pdf')\n") == []


def test_the_rule_sees_both_shapes_of_one_site():
    assert sidecar_sites([_SYNTHETIC[0]]) == {
        ("a/declared.py", "literal", ".corrupt-"),
        ("a/declared.py", "derived", "f'{p.name}.corrupt-{stamp}'"),
    }


def test_a_docstring_describing_the_defect_is_not_a_site():
    """This file, `quarantine.py` and three fixed writers all spell the broken
    name in prose. A rule that could not tell prose from code would either fire
    on every explanation or be silenced with a blanket exemption."""
    source = ('"""queue.json.corrupt-<stamp> was the wreck name."""\n'
              "def f():\n"
              '    """Also .bak, in prose."""\n'
              "    return 1\n")
    assert wreck_name_literals(source) == []


def test_prose_outside_a_docstring_is_not_a_site_either():
    assert wreck_name_literals("msg = 'the .bak file was kept for you'\n") == []


def test_the_staleness_rule_names_a_declaration_with_no_site():
    live = sidecar_sites(_SYNTHETIC)
    registry = dict(_SYNTHETIC_REGISTRY)
    registry[("gone/deleted.py", "literal", ".bak")] = "the file was removed"
    assert stale_declarations(registry, live) == [
        ("gone/deleted.py", "literal", ".bak")]


def test_the_staleness_rule_is_silent_when_every_declaration_is_live():
    assert stale_declarations(_SYNTHETIC_REGISTRY, sidecar_sites(_SYNTHETIC)) == []


def test_the_reason_rule_names_a_blank_reason():
    assert declarations_without_a_reason({("a.py", "literal", ".bak"): " "}) == [
        ("a.py", "literal", ".bak")]
    assert declarations_without_a_reason({("a.py", "literal", ".bak"): "why"}) == []


# --- and now over the real tree -----------------------------------------

def _scanned() -> list[Path]:
    return tracked_paths(("scripts/**/*.py", ".claude/**/*.py"))


def _corpus() -> list[tuple[str, str]]:
    out = []
    for path in _scanned():
        try:
            out.append((path.relative_to(ROOT).as_posix(),
                        path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:  # pragma: no cover - not a python source
            continue
    return out


def test_the_sweep_reaches_a_real_corpus():
    """Green over an empty corpus otherwise. 434 files on 2026-08-29.

    Both the walk and the READ are checked: a corpus builder that returned an
    empty list would make every rule below pass while measuring nothing, and a
    file count alone cannot see that. The three writers this shard fixed are
    named, so a glob that stopped reaching `scripts/` fails here.
    """
    assert len(_scanned()) > 300, f"only {len(_scanned())} files scanned"
    corpus = _corpus()
    assert len(corpus) > 300, f"only {len(corpus)} sources read"
    present = {rel for rel, _ in corpus}
    for writer in ("scripts/bridge_daemon/sources/action_queue.py",
                   "scripts/email-intelligence.py",
                   "scripts/run-skill-eval.py",
                   "scripts/utils/quarantine.py"):
        assert writer in present, f"the sweep no longer reaches {writer}"


def test_the_three_fixed_writers_no_longer_build_a_wreck_name():
    """The narrow half, stated as an absence. Each of these spelled a wreck
    suffix in code on 2026-08-29; none may again."""
    for writer in ("scripts/bridge_daemon/sources/action_queue.py",
                   "scripts/email-intelligence.py",
                   "scripts/run-skill-eval.py"):
        source = (ROOT / writer).read_text(encoding="utf-8")
        assert wreck_name_literals(source) == [], writer
        assert derived_sidecar_expressions(source) == [], writer


def test_every_derived_sidecar_name_is_declared():
    """A new one must be argued for, not inherited.

    Three writers had this shape and the audit found two. Naming them one at a
    time is the arrangement that failed for lock sidecars in August and for
    quarantine wrecks here; a wreck that persists must land where a `.gitignore`
    rule already covers it, or say in this registry why it does not need one.
    """
    undeclared = undeclared_sidecar_sites(_corpus(), DECLARED_SIDECAR_SITES)
    assert not undeclared, (
        "a persistent file built from another path's name. Route it through "
        "`scripts/utils/quarantine.quarantine_file`, or add an entry to "
        "DECLARED_SIDECAR_SITES saying which rule already covers it:\n  "
        + "\n  ".join(undeclared))


def test_the_registry_does_not_outlive_its_sites():
    """A declaration naming a site that no longer carries the shape waves
    through whatever is written at that path next."""
    stale = stale_declarations(DECLARED_SIDECAR_SITES, sidecar_sites(_corpus()))
    assert stale == [], f"declared sidecar sites that no longer exist: {stale}"


def test_every_declaration_carries_a_reason():
    empty = declarations_without_a_reason(DECLARED_SIDECAR_SITES)
    assert empty == [], f"declared with no reason written down: {empty}"
