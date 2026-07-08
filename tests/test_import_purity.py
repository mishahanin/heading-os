"""Import purity regression (F-2.1).

No script may exit or crash *at import time* when a heavy, capability-scoped
optional dependency is absent. This is the fresh-clone contract: `pytest` must
be able to collect the whole suite (and CI must be able to run it) after only
`uv sync --dev` + core deps, without the `email`/`browser`/`media`/... extras.

Mechanism: each candidate script is exec-imported in a subprocess whose import
system raises `ModuleNotFoundError` for every optional-integration package (the
future F-7.1 extras set). A module-level `import exchangelib` (unguarded) or an
import-time `sys.exit(1)` therefore fails the subprocess; a lazily-guarded
import does not run at import time and passes.

When this test fails on a new script, the fix is to make its heavy import lazy
(see scripts/utils/optdeps.py and scripts/send-email.py for the pattern), not to
widen the allowlist. The allowlist is only for scripts that legitimately cannot
import without a blocked package for a reason recorded inline.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Optional-integration packages that live in pyproject extras (F-7.1). A fresh
# core clone does not have these; importing this workspace's code must not need
# them. Kept as top-level package names; the blocker also covers submodules.
#
# F-7.1 drop: weasyprint and replicate were removed as declared deps (zero direct
# imports) and are fully gone from the lock, so they are no longer optional-only
# and leave this set. xlsxwriter stays - it survives as a python-pptx transitive
# (the `documents` extra), so it is absent in a core clone and core code must
# still not import it.
BLOCKED = {
    "exchangelib", "telethon", "playwright", "yt_dlp",
    "apify_client", "langfuse", "fastapi", "uvicorn", "starlette",
    "openai", "google", "firecrawl", "youtube_transcript_api", "pptx", "docx",
    "openpyxl", "xlsxwriter", "markitdown", "mammoth", "markdownify",
    "onnxruntime", "magika",
}

# F-2.1 debt is fully paid down: BASELINE is empty. Every script under scan
# imports pure, so any regression fails this test hard (no grandfathering left).
BASELINE: frozenset[str] = frozenset()

# Not import-testable in the workspace context - excluded with reason (NOT an
# F-2.1 heavy-dep issue). The skill-creator helper scripts import from a
# self-referential top-level `scripts` package (their own
# .claude/skills/skill-creator/scripts/ with an __init__.py), which collides
# with the workspace `scripts/` package when loaded outside the plugin's own
# sys.path. Their only heavy dependency is `anthropic`, a core dep. When run by
# the skill-creator plugin the path is set up correctly; this harness cannot
# reproduce that context, so testing them here would be a false negative.
SKIP = {
    ".claude/skills/skill-creator/scripts/improve_description.py":
        "self-referential `scripts` package collides with workspace scripts/ outside the plugin",
    ".claude/skills/skill-creator/scripts/package_skill.py":
        "self-referential `scripts` package collides with workspace scripts/ outside the plugin",
    ".claude/skills/skill-creator/scripts/run_eval.py":
        "self-referential `scripts` package collides with workspace scripts/ outside the plugin",
    ".claude/skills/skill-creator/scripts/run_loop.py":
        "self-referential `scripts` package collides with workspace scripts/ outside the plugin",
}


def _params():
    scripts = sorted(
        p
        for p in list(ROOT.glob("scripts/*.py")) + list(ROOT.glob(".claude/skills/*/scripts/*.py"))
        if p.name != "__init__.py"
    )
    out = []
    for p in scripts:
        rel = str(p.relative_to(ROOT))
        marks = ()
        if rel in SKIP:
            marks = (pytest.mark.skip(reason=SKIP[rel]),)
        elif rel in BASELINE:
            marks = (pytest.mark.xfail(reason="pre-existing F-2.1 debt (BASELINE)", strict=False),)
        out.append(pytest.param(p, id=rel, marks=marks))
    return out

# Subprocess preamble: install a meta-path finder that denies the blocked
# packages, then exec-import the target script by path.
_HARNESS = """
import sys, importlib.util, importlib.abc, importlib.machinery

BLOCKED = {blocked!r}

class _Denier(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        top = name.split('.')[0]
        if top in BLOCKED:
            raise ModuleNotFoundError(f"blocked optional dep: {{name}}", name=name)
        return None

sys.meta_path.insert(0, _Denier())
sys.path.insert(0, {root!r})
# Mirror `python <script>`: the script's own directory is on sys.path so that
# sibling-module imports (e.g. a skill's scripts/ helpers) resolve.
import os as _os
sys.path.insert(0, _os.path.dirname({script!r}))

spec = importlib.util.spec_from_file_location("_under_test", {script!r})
mod = importlib.util.module_from_spec(spec)
# Register before exec, exactly as real import machinery does, so that
# @dataclass (which looks up sys.modules[cls.__module__].__dict__) works.
sys.modules["_under_test"] = mod
spec.loader.exec_module(mod)
"""


@pytest.mark.parametrize("script", _params())
def test_import_is_pure(script: Path):
    rel = str(script.relative_to(ROOT))
    code = _HARNESS.format(blocked=BLOCKED, root=str(ROOT), script=str(script))
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=60,
    )
    assert r.returncode == 0, (
        f"{rel} is not import-pure (a blocked optional dep is imported or "
        f"sys.exit() runs at import time). Make the heavy import lazy.\n"
        f"--- stderr tail ---\n{r.stderr[-1200:]}"
    )
