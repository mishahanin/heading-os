"""The templates -> docs sync must fire on real templates and nothing else.

Found by the 2026-08-23 engine audit. The trigger was one unanchored substring:

    norm_path = str(file_path).replace("\\\\", "/")
    if "/templates/" not in norm_path:
        sys.exit(0)

Wrong in both directions.

FALSE POSITIVE, and this is the dangerous one. A write to
`outputs/scratch/templates/EMERGENCY-PROCEDURES.md` matched. `sync_targets`
then returned the real `<engine>/docs/EMERGENCY-PROCEDURES.md`, because that
name is in ENGINE_PUBLISHED, and `shutil.copy2` overwrote the published
document with scratch content. Nothing shouted: `REQUIRED_ANCHORS` only covers
GETTING-STARTED. It also slipped past `check_protect_docs` in
`.claude/hooks/_dispatch.py`, the wall that exists to stop exactly this file
being clobbered, because the copy happens inside the hook rather than through a
tool call the wall can see.

FALSE NEGATIVE. A relative path, `templates/GETTING-STARTED.md`, carries no
leading slash, so an ordinary edit expressed that way was silently not synced.

The fix is `is_real_template()`, three structural tests. The third is stricter
than the other two on purpose: a name in ENGINE_PUBLISHED reaches the public
docs site, so its template must live in the engine root or the data root by
identity, not merely look like a template. Rules 1 and 2 stay shape-based so a
synthetic root in a test tree still exercises the sync, which is what
`tests/test_sync_docs_anchor_guard.py` and `tests/test_sync_docs_targets.py`
rely on.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "sync-docs.py"

# Carries both entries of `REQUIRED_ANCHORS["GETTING-STARTED.md"]`, so a probe
# that uses this body is stopped by the gate under test and never by the anchor
# guard standing in front of it.
GOOD_TEMPLATE = """# Getting started

## Windows setup
- Install Python dependencies via `uv sync --all-groups`

> Dependencies are managed by uv. See `docs/security/DEPENDENCY-POLICY.md`.
"""


def _hook_module():
    spec = importlib.util.spec_from_file_location("sync_docs_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sync_docs_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def hook():
    return _hook_module()


# --- the false positive the audit found ---------------------------------------

def test_a_templates_dir_nested_in_the_engine_is_not_a_template_source(hook):
    attack = ROOT / "outputs" / "scratch" / "templates" / "EMERGENCY-PROCEDURES.md"
    assert hook.is_real_template(attack) is False, (
        "a scratch templates/ inside the engine still reaches the sync; this "
        "path overwrote docs/EMERGENCY-PROCEDURES.md with arbitrary content"
    )


def test_the_same_nesting_is_refused_for_a_non_published_name_too(hook):
    """The scratch path is not a template whatever the filename. Refusing only
    the published names would leave the shape half-open."""
    attack = ROOT / "outputs" / "scratch" / "templates" / "GETTING-STARTED.md"
    assert hook.is_real_template(attack) is False


def test_a_published_page_outside_any_workspace_root_is_refused(hook, tmp_path):
    """Rule 3. `/tmp/templates/EMERGENCY-PROCEDURES.md` is not nested inside a
    root, so rule 2 lets it through; it must still never reach the public docs
    site."""
    outside = tmp_path / "templates" / "EMERGENCY-PROCEDURES.md"
    assert hook.is_real_template(outside) is False


# --- the false negative -------------------------------------------------------

def test_a_relative_template_path_is_recognised(hook):
    assert hook.is_real_template(Path("templates/GETTING-STARTED.md")) is True, (
        "a relative path lacks the leading slash the old substring needed, so "
        "an ordinary edit was silently not synced"
    )


def test_an_absolute_template_path_is_recognised(hook):
    assert hook.is_real_template(ROOT / "templates" / "GETTING-STARTED.md") is True


def test_a_synthetic_root_still_syncs(hook, tmp_path):
    """Rules 1 and 2 stay shape-based so the existing hook tests, which build a
    templates/ + docs/ pair under tmp_path, keep exercising the real path."""
    assert hook.is_real_template(tmp_path / "templates" / "GETTING-STARTED.md") is True


# --- rule 1, the one nothing measured ------------------------------------------

@pytest.mark.parametrize("holder", ["nottemplates", "templates-backup", "docs",
                                    "my-templates", "templates.old"])
def test_a_directory_that_is_not_named_templates_is_refused(hook, tmp_path, holder):
    """Rule 1 of the three, and until 2026-09-01 no test asked it anything.

    Deleting the `parent.name != "templates"` clause left the whole sync-docs
    corpus green, because every other case in this file puts the probe inside a
    directory literally called `templates`. The spellings here are the realistic
    near-misses a human types (`templates-backup`, `templates.old`), not an
    obviously invalid one: `str(path)` contains "templates" in all of them, so
    each would have satisfied the unanchored substring the module docstring
    above records as the original defect.
    """
    assert hook.is_real_template(tmp_path / holder / "GETTING-STARTED.md") is False


def test_the_hook_asks_the_guard_before_it_copies(tmp_path):
    """`is_real_template` refusing proves nothing until the caller consults it.

    The end-to-end probe below names a file it never creates, so `shutil.copy2`
    fails on a missing SOURCE whatever the guard decides, and the published page
    is left alone either way. MEASURED 2026-09-01 by removing the
    `if not is_real_template(...)` line from `main()`: all three sync-docs test
    files stayed green, and the hook created a `docs/` directory under the
    scratch root it should have refused.

    Here the source file EXISTS, so the only thing standing between it and
    `<root>/docs/GETTING-STARTED.md` is the call. Everything happens under
    `tmp_path`, so a regression writes into the scratch tree rather than into
    the engine's published documentation.
    """
    holder = tmp_path / "nottemplates"
    holder.mkdir()
    (holder / "GETTING-STARTED.md").write_text(GOOD_TEMPLATE, encoding="utf-8")
    (tmp_path / "docs").mkdir()

    payload = {"cwd": str(tmp_path),
               "tool_input": {"file_path": str(holder / "GETTING-STARTED.md")}}
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "docs" / "GETTING-STARTED.md").exists(), (
        "the hook copied from a directory that is not a templates/ source, so "
        "nothing in main() consults is_real_template"
    )


def test_a_name_outside_sync_files_is_never_copied(tmp_path):
    """The other gate in `main()`, and it had no test either.

    Removing `if file_path.name not in SYNC_FILES` left the corpus green. The
    control half matters as much as the probe: the SAME tree is driven with a
    name that IS in SYNC_FILES, so a run that copies nothing because the fixture
    never reached the copy cannot be read as the gate doing its job.
    """
    templates = tmp_path / "templates"
    templates.mkdir()
    (tmp_path / "docs").mkdir()
    (templates / "NOT-SHARED.md").write_text(GOOD_TEMPLATE, encoding="utf-8")
    (templates / "GETTING-STARTED.md").write_text(GOOD_TEMPLATE, encoding="utf-8")

    def drive(name):
        payload = {"cwd": str(tmp_path),
                   "tool_input": {"file_path": str(templates / name)}}
        proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr

    drive("NOT-SHARED.md")
    assert not (tmp_path / "docs" / "NOT-SHARED.md").exists(), (
        "a name the sync does not own was copied into docs/"
    )
    drive("GETTING-STARTED.md")
    assert (tmp_path / "docs" / "GETTING-STARTED.md").exists(), (
        "the control never reached the copy, so the assertion above is empty"
    )


# --- end to end, through the hook itself ---------------------------------------

def test_the_hook_does_not_touch_the_published_doc_from_a_scratch_template(tmp_path):
    """The reproduction from the finding, run for real.

    The probe path is deliberately inside the engine, under `outputs/`, because
    that is where a real scratch draft lives and the old trigger keyed on the
    path SHAPE rather than on the tree. A path in the system temp directory
    would also pass, for the wrong reason: the hook would have refused it as
    outside the workspace before ever reaching the trigger this test measures.

    Nothing is written. The file is never created, only NAMED in the payload,
    and the last assertion is that the hook did not create it either. That
    assertion is the reason this is safe to point at the engine tree; the
    docstring claimed the file was "created inside tmp_path" until 2026-08-27,
    which described neither what the code does nor why it is safe.
    """
    published = ROOT / "docs" / "EMERGENCY-PROCEDURES.md"
    before = published.read_text(encoding="utf-8")

    scratch = ROOT / "outputs" / "scratch-sync-probe" / "templates"
    payload = {
        "cwd": str(ROOT),
        "tool_input": {"file_path": str(scratch / "EMERGENCY-PROCEDURES.md")},
    }
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0
    assert published.read_text(encoding="utf-8") == before, (
        "the hook overwrote the published EMERGENCY-PROCEDURES from a scratch "
        "templates/ path"
    )
    assert not scratch.exists(), "the probe must not create anything"


# --- the regeneration claim must be earned -------------------------------------

def test_a_failing_renderer_is_reported_as_a_failure(tmp_path):
    """The claim, driven rather than grepped.

    The three assertions this replaced read the hook's own SOURCE for
    `"proc.returncode != 0"`, `"HTML regen FAILED"` and `"The HTML is STALE."`.
    MEASURED 2026-09-01: rewriting the live check to `if False and
    proc.returncode != 0:` left every one of those substrings in the file and
    the test green, which is the shape a comment satisfies too.

    So the renderer is made to fail for real. `scripts/regenerate-docs-html.py`
    writes `<name>.html` beside its input through an atomic replace, and a
    DIRECTORY at that path makes the replace raise IsADirectoryError and the
    script exit 1. Nothing is monkeypatched and no path leaves `tmp_path`.
    """
    templates = tmp_path / "templates"
    templates.mkdir()
    (tmp_path / "docs").mkdir()
    (templates / "GETTING-STARTED.md").write_text(GOOD_TEMPLATE, encoding="utf-8")
    # The first render target is the template's own sibling HTML.
    (templates / "GETTING-STARTED.html").mkdir()

    payload = {"cwd": str(tmp_path),
               "tool_input": {"file_path": str(templates / "GETTING-STARTED.md")}}
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=90)

    # Non-blocking stays non-blocking: the docstring promises a warning, not an
    # abort, and the markdown copy still lands.
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "docs" / "GETTING-STARTED.md").exists()

    message = json.loads(proc.stdout)["additionalContext"]
    assert "HTML regen FAILED" in message and "The HTML is STALE." in message, (
        f"a renderer that exited non-zero was not reported: {message!r}"
    )
    assert "+ regenerated HTML" not in message, (
        "the success line was emitted over a render that failed"
    )
    assert "HTML regen FAILED" in proc.stderr


def test_the_success_message_is_not_emitted_unconditionally():
    """Pins the shape, not just the strings: the success line must sit in the
    else branch of the failure check."""
    src = HOOK.read_text(encoding="utf-8")
    success = src.index('regen_msg = f" + regenerated HTML for')
    guard = src.index("if failures:")
    assert guard < success, (
        "the success message is emitted before or without the failure check"
    )
