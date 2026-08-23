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


# --- end to end, through the hook itself ---------------------------------------

def test_the_hook_does_not_touch_the_published_doc_from_a_scratch_template(tmp_path):
    """The reproduction from the finding, run for real. Nothing under the engine
    is written: the scratch file is created inside tmp_path, and only the path
    SHAPE is what the old trigger keyed on."""
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

def test_the_regen_result_is_inspected():
    """`subprocess.run(..., check=False)` with the result discarded let a failing
    renderer produce the message '+ regenerated HTML' over stale output. The
    failure stays non-blocking, per the module docstring, but it must be said."""
    src = HOOK.read_text(encoding="utf-8")
    assert "proc.returncode != 0" in src, (
        "sync-docs no longer inspects the renderer's exit code, so a failed "
        "regeneration is reported as a success again"
    )
    assert "HTML regen FAILED" in src
    assert "The HTML is STALE." in src


def test_the_success_message_is_not_emitted_unconditionally():
    """Pins the shape, not just the strings: the success line must sit in the
    else branch of the failure check."""
    src = HOOK.read_text(encoding="utf-8")
    success = src.index('regen_msg = f" + regenerated HTML for')
    guard = src.index("if failures:")
    assert guard < success, (
        "the success message is emitted before or without the failure check"
    )
