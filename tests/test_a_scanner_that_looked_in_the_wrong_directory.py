"""Three defects in the PostToolUse hooks, all of them silent by construction.

1. THE HIDDEN-CHARACTER SCAN DID NOT RUN FROM ANY ENGINE SUBDIRECTORY.
   `post-write-sanitize.py` resolved a RELATIVE `file_path` against the hook
   PROCESS cwd while the scanner import one step below it was anchored to
   `__file__` and read the payload's own `cwd`. The two halves of one hook
   disagreed about one field. MEASURED 2026-08-31, driving the real hook with
   `file_path` relative and the payload cwd at the engine root:

       process cwd = <root>              CONTAMINATION reported
       process cwd = <root>/scripts      nothing, on either stream, exit 0
       process cwd = <root>/.claude/hooks  nothing, on either stream, exit 0

   Two things establish that the non-root cwd is real rather than hypothetical.
   Every hook command in every settings file walks `[Path.cwd(), *parents]` to
   find its own script, which is pointless if cwd is always the root. And this
   hook's own comment records the 2026-08-25 measurement that a session started
   in an engine subdirectory made the scanner IMPORT fail; that fix anchored the
   import and left the file-existence gate one check above it untouched. Its
   sibling on the identical PostToolUse matcher, `prompt-guard.py`, already
   resolved against the payload cwd.

   Why it was invisible: the miss produced no stdout, no stderr and exit 0,
   which is byte-identical to a clean file. The nearest existing test,
   `tests/test_two_guards_that_scanned_the_wrong_tree.py`, drives this hook from
   all three cwds and passes an ABSOLUTE path, so it could not see it.

2. FOUR HOOKS GUARDED THE TYPE OF `tool_input` AND NONE THE TYPE OF THE PATH
   INSIDE IT. MEASURED 2026-08-31 driving each real hook with a non-string
   `file_path` / `notebook_path`:

       data-path-redirect.py   AttributeError: 'int' object has no attribute 'replace'
       prompt-guard.py         AttributeError: 'int' object has no attribute 'replace'
       sync-docs.py            TypeError: expected str, bytes or os.PathLike ...
       post-write-sanitize.py  TypeError: stat: path should be string ... not list

   `data-path-redirect.py` is the one that matters: it is PreToolUse, so a
   traceback exit means the redirect does not happen and the tool proceeds
   against the ENGINE path, which is a write landing in the wrong repository.

   Why it was invisible: `tests/test_every_hook_survives_a_malformed_payload.py`
   feeds only top-level non-object payloads (`[]`, `"x"`, `3`, `null`). A wrong
   FIELD type inside a well-formed payload was covered nowhere.

   So the sweep below is DERIVED, not four hand-written cases. The rule is
   "every hook that names a path field survives a non-string in it", the hooks
   are enumerated from the syntax tree, and a hook added tomorrow is picked up
   and fails until it is guarded.

3. A TOP-LEVEL `additionalContext` ON PostToolUse IS SILENTLY DROPPED.
   Established 2026-08-31 by the coordinator through the real CLI, not from
   docs: a file carrying U+200B was touched with the real Edit tool, the
   registered matcher `Write|Edit|MultiEdit|NotebookEdit` invoked
   `post-write-sanitize.py`, and nothing reached the session. Control, ruling
   out both "the hook never fired" and "the hook found nothing": the same
   payload run by hand prints the contamination notice and exits 0. Every
   advisory these two hooks have ever produced was discarded while they exited 0
   reporting success, so the mechanical half of the always-on
   `.claude/rules/hidden-chars.md` policy has been silent, and sync-docs' "The
   HTML is STALE" and "Failed to sync" never arrived either.

   The hooks reference does not settle the shape: it says a top-level
   `additionalContext` is ignored on UserPromptSubmit, shows the
   `hookSpecificOutput` wrapper for PreToolUse, and says nothing about
   PostToolUse. It does state plainly that a PostToolUse hook's stderr is shown
   to Claude on exit 0. Hence three channels, wrapper plus top-level key plus
   stderr.

   WHAT THE TESTS BELOW ESTABLISH, stated narrowly. They pin the SHAPE of what
   the hook emits and the stderr copy beside it. DELIVERY was established by the
   harness probe above and cannot be re-measured from pytest, which has no CLI
   to drive. A green run here does not prove the message arrives; it proves the
   hook stopped betting the whole advisory on one unproven channel.

Run: .venv/bin/python -m pytest tests/test_a_scanner_that_looked_in_the_wrong_directory.py
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".claude" / "hooks"

sys.path.insert(0, str(ROOT))
from tests.repo_files import read_sources  # noqa: E402

# The interpreter the settings files actually launch hooks with, NOT
# `sys.executable`. The project venv carries an editable install of this repo, so
# under `.venv/bin/python` every `import scripts.utils.*` inside a hook succeeds
# from any directory whatever the hook does to `sys.path`. The sibling
# `tests/test_two_guards_that_scanned_the_wrong_tree.py` records a mutation that
# SURVIVED a full suite for exactly that reason. Nothing here depends on
# sys.path, but running the hooks the way the harness runs them keeps that trap
# shut for whoever edits this file next.
PY = shutil.which("python3") or sys.executable

_ZWSP = "hello" + chr(0x200B) + "world\n"

SANITIZE = HOOKS / "post-write-sanitize.py"
SYNC_DOCS = HOOKS / "sync-docs.py"
REDIRECT = HOOKS / "data-path-redirect.py"

# A template that satisfies sync-docs' REQUIRED_ANCHORS for GETTING-STARTED, and
# one that has lost them. Same fixture shape as
# `tests/test_sync_docs_anchor_guard.py`, which owns the anchor rule itself.
GOOD_TEMPLATE = """# Getting started

- Install dependencies with `uv sync --all-groups`

> Dependencies are managed by uv. See `docs/security/DEPENDENCY-POLICY.md`.
"""
BAD_TEMPLATE = """# Getting started

- self-contained, nothing to install
"""


def _run(hook: Path, payload: dict, cwd: Path | None = None,
         env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Drive a hook in its own process, the way the harness does.

    `cwd` is the PROCESS cwd, which is the whole point of the first defect: it
    is deliberately different from `payload["cwd"]` in most tests here.
    """
    env = dict(os.environ, **(env_extra or {}))
    return subprocess.run(
        [PY, str(hook)], input=json.dumps(payload), capture_output=True,
        text=True, timeout=120, check=False, cwd=str(cwd) if cwd else None,
        env=env)


def _context(proc: subprocess.CompletedProcess) -> str:
    """The advisory the hook emitted, read from the wrapper.

    Empty string when the hook said nothing, so a caller can assert silence
    without distinguishing "no JSON" from "no message".
    """
    if not proc.stdout.strip():
        return ""
    return (json.loads(proc.stdout)
            .get("hookSpecificOutput", {})
            .get("additionalContext", ""))


# ============================================================
# Defect 1: which directory a relative path is resolved from
# ============================================================

@pytest.fixture
def contaminated(tmp_path):
    """A U+200B file in a scratch tree, plus a sibling directory that lacks it.

    `tmp_path` on purpose. The operator's data overlay is never written by this
    suite, and a scratch tree outside the engine clone also proves the
    resolution is driven by the payload rather than by anything about the
    workspace layout.
    """
    (tmp_path / "sub").mkdir()
    target = tmp_path / "sub" / "contaminated.md"
    target.write_text(_ZWSP, encoding="utf-8")
    (tmp_path / "elsewhere").mkdir()
    return tmp_path, "sub/contaminated.md"


@pytest.mark.parametrize("process_cwd", ["", "scripts", ".claude/hooks"])
def test_a_relative_path_is_resolved_against_the_payload_cwd(
        contaminated, process_cwd):
    """The defect itself. From a subdirectory the hook said nothing at all."""
    scratch, rel = contaminated
    proc = _run(SANITIZE,
                {"tool_name": "Write", "cwd": str(scratch),
                 "tool_input": {"file_path": rel}},
                cwd=ROOT / process_cwd if process_cwd else ROOT)

    assert proc.returncode == 0, proc.stderr
    assert "HIDDEN CHARACTER CONTAMINATION" in _context(proc), (
        f"nothing was reported with the process parked in "
        f"{process_cwd or '<root>'} and the payload cwd at {scratch}. Silence "
        f"here is indistinguishable from a clean file.\nstderr: {proc.stderr}")
    assert "U+200B" in _context(proc)


def test_the_process_cwd_no_longer_decides_where_to_look(contaminated):
    """The direction, not just the outcome.

    The file resolves against the PROCESS cwd and not against the payload cwd.
    A hook that simply tried both would pass every test above and still be
    scanning a file the session never named, so this one requires the payload to
    win and the miss to be REPORTED rather than swallowed.
    """
    scratch, rel = contaminated
    proc = _run(SANITIZE,
                {"tool_name": "Write", "cwd": str(scratch / "elsewhere"),
                 "tool_input": {"file_path": rel}},
                cwd=scratch)

    assert proc.returncode == 0, proc.stderr
    assert "SCAN DID NOT RUN" in _context(proc)
    assert "UNVERIFIED, not clean" in _context(proc)
    assert "CONTAMINATION" not in _context(proc)


def test_an_absolute_path_still_reports_from_any_directory(contaminated):
    """The passing side. This is what the 2026-08-25 fix bought and it must
    survive: an absolute path is used as given, whatever the payload cwd says."""
    scratch, rel = contaminated
    proc = _run(SANITIZE,
                {"tool_name": "Write", "cwd": str(scratch / "elsewhere"),
                 "tool_input": {"file_path": str(scratch / rel)}},
                cwd=ROOT / "scripts")
    assert "HIDDEN CHARACTER CONTAMINATION" in _context(proc)


def test_a_clean_file_from_a_subdirectory_stays_quiet(contaminated):
    """The mirror. A hook that shouted unconditionally would satisfy every
    assertion above, and would also teach the operator to ignore it."""
    scratch, _rel = contaminated
    (scratch / "sub" / "clean.md").write_text("nothing hidden here\n",
                                              encoding="utf-8")
    proc = _run(SANITIZE,
                {"tool_name": "Write", "cwd": str(scratch),
                 "tool_input": {"file_path": "sub/clean.md"}},
                cwd=ROOT / "scripts")
    assert proc.stdout.strip() == "", proc.stdout


def test_a_path_that_resolves_nowhere_says_the_scan_did_not_run(tmp_path):
    """Obligation 3 of `.claude/rules/scope-claims.md`, at the gate where the
    silent non-scan lived. A control that cannot run must say it did not run."""
    proc = _run(SANITIZE,
                {"tool_name": "Write", "cwd": str(tmp_path),
                 "tool_input": {"file_path": "nowhere/missing.md"}},
                cwd=ROOT)
    assert "HIDDEN CHARACTER SCAN DID NOT RUN" in _context(proc)
    assert "UNVERIFIED, not clean" in _context(proc)
    assert "missing.md" in _context(proc)


@pytest.mark.parametrize("tool_input", [
    {},
    {"file_path": ""},
    {"command": "ls -la"},
])
def test_a_payload_naming_no_file_stays_silent(tool_input, tmp_path):
    """The mirror for the branch above. This hook is wired to a matcher that can
    deliver a payload with no path in it, and a scan notice on every one of
    those would bury the notices that mean something."""
    proc = _run(SANITIZE,
                {"tool_name": "Bash", "cwd": str(tmp_path),
                 "tool_input": tool_input},
                cwd=ROOT)
    assert proc.stdout.strip() == "", proc.stdout


def test_a_binary_extension_is_skipped_without_a_scan_notice(tmp_path):
    """The other mirror. A `.png` is skipped by design, so an unresolvable one
    must stay silent rather than claim an unverified text file."""
    proc = _run(SANITIZE,
                {"tool_name": "Write", "cwd": str(tmp_path),
                 "tool_input": {"file_path": "assets/logo.png"}},
                cwd=ROOT)
    assert proc.stdout.strip() == "", proc.stdout


# ============================================================
# Defect 3: the channel the advisory goes out on
# ============================================================

def _both_channels(proc: subprocess.CompletedProcess, fragment: str) -> None:
    """Assert one advisory reached every channel, carrying the same words.

    Three channels, and the reasoning is in each hook's `advise` docstring:
    stderr is the one the reference documents for PostToolUse, the wrapper is
    the shape the reference shows for the events it does describe, and the
    top-level key is the form these hooks have always emitted and other tooling
    in this tree still reads. Asserting all three together is what stops a later
    edit from quietly dropping back to one.
    """
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    specific = payload.get("hookSpecificOutput", {})
    assert specific.get("hookEventName") == "PostToolUse", payload
    assert fragment in specific.get("additionalContext", ""), payload
    assert payload.get("additionalContext") == specific["additionalContext"], (
        "the two keys carry different text, so a reader gets a different "
        "advisory depending on which one the harness honours")
    assert fragment in proc.stderr, (
        f"stderr is the only channel the reference documents for PostToolUse "
        f"and it carries no copy of the advisory: {proc.stderr!r}")


def test_the_contamination_advisory_goes_out_on_every_channel(contaminated):
    scratch, rel = contaminated
    proc = _run(SANITIZE,
                {"tool_name": "Write", "cwd": str(scratch),
                 "tool_input": {"file_path": rel}},
                cwd=ROOT)
    _both_channels(proc, "HIDDEN CHARACTER CONTAMINATION")


def test_the_did_not_run_advisory_goes_out_on_every_channel(tmp_path):
    """The branch that matters most: a guard that could not run has to be heard,
    or its silence is read as a clean file."""
    proc = _run(SANITIZE,
                {"tool_name": "Write", "cwd": str(tmp_path),
                 "tool_input": {"file_path": "nowhere/missing.md"}},
                cwd=ROOT)
    _both_channels(proc, "HIDDEN CHARACTER SCAN DID NOT RUN")


def test_a_quiet_hook_writes_to_neither_channel(contaminated):
    """The mirror for both tests above. A stderr copy that is emitted
    unconditionally would be shown to Claude on every single clean write."""
    scratch, _rel = contaminated
    (scratch / "sub" / "ok.md").write_text("plain text\n", encoding="utf-8")
    proc = _run(SANITIZE,
                {"tool_name": "Write", "cwd": str(scratch),
                 "tool_input": {"file_path": "sub/ok.md"}},
                cwd=ROOT)
    assert proc.stdout.strip() == ""
    assert "CONTAMINATION" not in proc.stderr
    assert "SCAN DID NOT RUN" not in proc.stderr


def _template_tree(tmp_path: Path, body: str) -> Path:
    (tmp_path / "templates").mkdir()
    (tmp_path / "docs").mkdir()
    template = tmp_path / "templates" / "GETTING-STARTED.md"
    template.write_text(body, encoding="utf-8")
    (tmp_path / "docs" / "GETTING-STARTED.md").write_text(GOOD_TEMPLATE,
                                                          encoding="utf-8")
    return template


def test_the_sync_advisory_goes_out_on_every_channel(tmp_path):
    template = _template_tree(tmp_path, GOOD_TEMPLATE)
    proc = _run(SYNC_DOCS, {"tool_name": "Edit", "cwd": str(tmp_path),
                            "tool_input": {"file_path": str(template)}})
    _both_channels(proc, "Auto-synced")


def test_the_blocked_sync_advisory_goes_out_on_every_channel(tmp_path):
    """The anchor guard's refusal. `tests/test_sync_docs_anchor_guard.py` owns
    whether it fires; this owns whether anyone hears it."""
    template = _template_tree(tmp_path, BAD_TEMPLATE)
    proc = _run(SYNC_DOCS, {"tool_name": "Edit", "cwd": str(tmp_path),
                            "tool_input": {"file_path": str(template)}})
    _both_channels(proc, "BLOCKED sync")
    assert "uv sync" in _context(proc)


def test_a_file_sync_docs_does_not_own_writes_to_neither_channel(tmp_path):
    """The mirror. Most writes are not templates and must produce nothing."""
    other = tmp_path / "notes.md"
    other.write_text("hello\n", encoding="utf-8")
    proc = _run(SYNC_DOCS, {"tool_name": "Edit", "cwd": str(tmp_path),
                            "tool_input": {"file_path": str(other)}})
    assert proc.stdout.strip() == ""
    assert "Auto-synced" not in proc.stderr


# ============================================================
# Defect 2: a derived sweep over every hook that names a path field
# ============================================================

PATH_FIELDS = ("file_path", "notebook_path")

# The tool each field arrives under. `data-path-redirect.py` routes on
# `tool_name` before it reads anything, so a field driven under the wrong tool
# would exit early and the sweep would measure nothing.
TOOL_FOR_FIELD = {"file_path": "Write", "notebook_path": "NotebookEdit"}

# Every non-string a JSON payload can put in a path field. All five are needed:
# an int is accepted by `os.stat` as a FILE DESCRIPTOR rather than rejected, so
# in `post-write-sanitize.py` the int case was only fatal when fd 3 happened to
# be open on a regular file, while the list, dict and float cases were fatal
# unconditionally. A rule tested on the int alone would have looked satisfied.
WRONG_TYPES = [3, True, [1], {"a": 1}, 3.5]


def _docstring_constants(tree: ast.AST) -> set[int]:
    """The Constant nodes that are docstrings, by identity.

    A field named in a docstring is being EXPLAINED, not read. Every hook in
    this tree documents its payload fields in prose, so counting those would
    enumerate files that touch no path at all.
    """
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


def path_fields_named(source: str) -> set[str]:
    """Which path fields a source names in CODE, asked of the syntax tree.

    Deliberately wide: any string literal outside a docstring counts, not only
    a literal `.get("file_path")`. `data-path-redirect.py` reads its fields
    through a variable bound from the `_PATH_FIELDS` table, where the names are
    tuple entries and never appear at a `.get` call at all, so the narrow shape
    would have missed the one hook whose crash puts a write in the wrong
    repository. A false positive costs one extra subprocess that must not
    crash, which is a cheap thing to be wrong about; a false negative is a hook
    nobody swept.

    Comments are invisible to `ast`, which is the other half of why this asks
    the tree rather than grepping.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - another test's job
        return set()
    skip = _docstring_constants(tree)
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and id(node) not in skip
            and node.value in PATH_FIELDS}


_READS_FIXTURE = 'p = payload["tool_input"].get("file_path", "")\n'
_TABLE_FIXTURE = 'FIELDS = {"NotebookEdit": ("notebook_path",)}\n'
_DOCSTRING_ONLY_FIXTURE = '"""Reads file_path and notebook_path."""\nx = 1\n'
_INNOCENT_FIXTURE = 'p = payload.get("command", "")\n'


def test_the_enumerator_sees_a_direct_read():
    assert path_fields_named(_READS_FIXTURE) == {"file_path"}


def test_the_enumerator_sees_a_field_named_only_in_a_table():
    """The shape `data-path-redirect.py` uses, and the one a `.get`-only
    detector reported as clean."""
    assert path_fields_named(_TABLE_FIXTURE) == {"notebook_path"}


def test_the_enumerator_ignores_a_docstring_mention():
    assert path_fields_named(_DOCSTRING_ONLY_FIXTURE) == set()


def test_the_enumerator_leaves_an_unrelated_hook_alone():
    """Both directions, or the enumerator is not tested at all. A detector that
    returned every field for every source would satisfy the three above."""
    assert path_fields_named(_INNOCENT_FIXTURE) == set()


def path_reading_hooks() -> list[tuple[Path, str]]:
    """(hook, field) for every hook under `.claude/hooks/` that names a path."""
    # The glob lists the hooks and the loop reads them; a file can be created and
    # removed inside that window in a checkout several agents share, and the
    # FileNotFoundError would come out of this guard as though it had caught
    # something. A hook that is gone names no path field, so `read_sources`
    # skips it and warns - the floor below then measures what was really read.
    pairs = []
    vanished: list[Path] = []
    for hook, text in read_sources(sorted(HOOKS.glob("*.py")), vanished):
        for field in sorted(path_fields_named(text)):
            pairs.append((hook, field))
    return pairs


_PAIRS = path_reading_hooks()


def test_the_sweep_reaches_a_real_set_of_hooks():
    """A floor, because an enumerator that found nothing turns the sweep below
    green over zero work, which is the failure mode of every derived rule."""
    names = {hook.name for hook, _ in _PAIRS}
    assert len(names) >= 4, f"only found {sorted(names)}"
    for expected in ("data-path-redirect.py", "post-write-sanitize.py",
                     "sync-docs.py", "prompt-guard.py", "_dispatch.py"):
        assert expected in names, (
            f"{expected} names a path field and the enumerator missed it")


@pytest.mark.parametrize("value", WRONG_TYPES, ids=lambda v: type(v).__name__)
@pytest.mark.parametrize("hook, field", _PAIRS,
                         ids=[f"{h.name}-{f}" for h, f in _PAIRS])
def test_a_non_string_path_field_does_not_crash_the_hook(
        hook, field, value, tmp_path):
    """One rule for every hook that reads a path, present and future.

    Four hooks guarded the TYPE of `tool_input` and none the type of the field
    inside it, and four hand-written cases would not have caught the fifth hook.
    `HEADING_OS_DATA` and the rate-limit counter are pointed at scratch because
    several of these hooks write where `get_data_root()` says, and this suite
    never touches the operator's overlay.
    """
    overlay = tmp_path / "data-root"
    overlay.mkdir()
    env = {"HEADING_OS_DATA": str(overlay),
           "WS_RATE_LIMIT_STATE": str(tmp_path / "rate.json")}
    proc = _run(hook,
                {"tool_name": TOOL_FOR_FIELD[field], "cwd": str(tmp_path),
                 "tool_input": {field: value}},
                env_extra=env)

    tail = proc.stderr[-1500:]
    assert proc.returncode >= 0, (
        f"{hook.name} was killed by signal {-proc.returncode} on "
        f"{field}={value!r}: {tail}")
    assert "can't open file" not in proc.stderr, (
        f"{hook.name} never started, so this case measured nothing: {tail}")
    assert "Traceback" not in proc.stderr, (
        f"{hook.name} crashed on {field}={value!r}. On a PreToolUse hook that "
        f"means the guard did not run and the tool proceeded:\n{tail}")
    assert proc.returncode == 0, (
        f"{hook.name} exited {proc.returncode} on {field}={value!r}; a field "
        f"that names no path is not a reason to refuse the call:\n{tail}")


@pytest.mark.parametrize("hook, field", _PAIRS,
                         ids=[f"{h.name}-{f}" for h, f in _PAIRS])
def test_an_ordinary_string_path_is_not_treated_as_the_wrong_type(
        hook, field, tmp_path):
    """The passing side of the same guard.

    A hook that announced "not a string" for every payload would satisfy the
    sweep above while refusing to read any path at all.
    """
    overlay = tmp_path / "data-root"
    overlay.mkdir()
    env = {"HEADING_OS_DATA": str(overlay),
           "WS_RATE_LIMIT_STATE": str(tmp_path / "rate.json")}
    proc = _run(hook,
                {"tool_name": TOOL_FOR_FIELD[field], "cwd": str(tmp_path),
                 "tool_input": {field: "scripts/thread.py"}},
                env_extra=env)
    assert "Traceback" not in proc.stderr, proc.stderr[-1500:]
    assert "not a string" not in proc.stderr, (
        f"{hook.name} called an ordinary path the wrong type: "
        f"{proc.stderr[-500:]}")


@pytest.mark.parametrize("hook", [SANITIZE, SYNC_DOCS, REDIRECT],
                         ids=lambda p: p.name)
def test_the_wrong_type_is_announced_and_not_swallowed(hook, tmp_path):
    """A coercion nobody is told about is a scan, or a redirect, silently
    skipped. Scoped to the three hooks fixed in this lane: `prompt-guard.py` and
    `_dispatch.py` are covered by the crash sweep above, and pinning their
    wording here would put one file's phrasing in another file's test.
    """
    overlay = tmp_path / "data-root"
    overlay.mkdir()
    proc = _run(hook,
                {"tool_name": "Write", "cwd": str(tmp_path),
                 "tool_input": {"file_path": [1]}},
                env_extra={"HEADING_OS_DATA": str(overlay)})
    assert "not a string" in proc.stderr, (
        f"{hook.name} swallowed a non-string path field: {proc.stderr!r}")
