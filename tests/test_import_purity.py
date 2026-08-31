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

import re
import subprocess
import sys
from pathlib import Path

import pytest
from tests.repo_files import tracked_paths

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
    # Local speech-to-text (the `media` extra). ctranslate2 and av are
    # faster-whisper transitives, absent in a core clone just like xlsxwriter,
    # so core code must not import them either.
    "faster_whisper", "ctranslate2", "av",
}

# F-2.1 debt is fully paid down: BASELINE is empty. Every script under scan
# imports pure, so any regression fails this test hard (no grandfathering left).
BASELINE: frozenset[str] = frozenset()

# One class is excluded, mechanically and for a reason that cannot go stale:
# a PEP-723 standalone (see `_is_pep723_standalone`). Everything else is scanned.
#
# The scan glob was `.claude/skills/*/scripts/*.py` until 2026-08-31, which
# stopped one directory short and reached 18 of the 41 tracked skill scripts.
# The 23 outside it were invisible, and 22 of them import `pptx` - a member of
# BLOCKED - at module level, proven by AST. `pptx-generator/scripts/combine_decks.py`
# defers its own `pptx` import with a comment naming THIS test as the reason,
# while 22 siblings in the same skill did as they liked. The header used to say
# "Nothing is excluded" beside an empty BASELINE; both sentences were true only
# of the files the glob happened to name.
#
# Baselining those 22 was the wrong fix: a baseline that swallows a real finding
# hides it behind a gate that now reports clean. The right answer is that they
# are not findings. All 22 carry `#!/usr/bin/env -S uv run` and a PEP-723
# `# /// script` block declaring `python-pptx==1.0.2`, so uv resolves that
# dependency into an ephemeral environment at run time. They never rely on the
# workspace environment, and every one is kebab-case, so `import` cannot even
# name them. The fresh-clone contract this file enforces - that `uv sync --dev`
# is enough to import the tree - is not a contract they can break.
#
# The exemption is a predicate, not a name list, so it repairs itself: strip the
# PEP-723 block from one of them and it is back under scan the same second.
# Measured 2026-08-31: it frees 22 files, all outside the old glob, and ZERO
# inside it - so it cannot weaken coverage that already existed, and the
# constraint on combine_decks.py still binds.
#
# The four skill-creator helper scripts used to be excluded, because
# they import a self-referential top-level `scripts` package (their own
# .claude/skills/skill-creator/scripts/, which has an __init__.py) that collided
# with the workspace `scripts/` package pinned by the editable install. That was
# true when the exclusion was written and stopped being true on 2026-08-23, when
# each of the four gained `sys.path.insert(0, <skill root>)` above its import for
# exactly this reason. The exclusion outlived its cause by three days and hid
# four scripts from the gate; re-measured 2026-08-26, all four import clean under
# this harness. An exclusion that is never re-measured is coverage deleted in
# advance, so if one is ever needed again, it needs a re-check with it.


_PEP723_BLOCK = re.compile(r"^# /// script\s*$.*?^# ///\s*$", re.M | re.S)


def _is_pep723_standalone(path: Path) -> bool:
    """A script uv runs in its own resolved environment, not the workspace's.

    Two conditions, both required: a `uv run` shebang, and a PEP-723
    `# /// script` block that declares at least one dependency. Together they
    mean the file states its own requirements and uv installs them on the spot,
    so nothing about it depends on what `uv sync --dev` put in `.venv`. That is
    exactly the assumption this whole module exists to defend, which is why such
    a file is out of scope rather than grandfathered.

    Both conditions matter. The shebang alone would free any `uv run` script
    that silently leans on the workspace env; the block alone would free a file
    that is imported rather than executed.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not text.startswith("#!") or "uv run" not in text.splitlines()[0]:
        return False
    block = _PEP723_BLOCK.search(text)
    if block is None:
        return False
    deps = re.search(r"^# dependencies = \[(.*?)^# \]", block.group(0), re.M | re.S)
    return bool(deps and deps.group(1).strip(" \n#"))


def _scanned_paths() -> list[Path]:
    """Every tracked Python file under `scripts/` and `.claude/skills/`, minus
    package markers and PEP-723 standalones."""
    return [
        p for p in tracked_paths(("scripts/*.py", ".claude/skills/**/*.py"))
        if p.name != "__init__.py" and not _is_pep723_standalone(p)
    ]


def _params():
    scripts = _scanned_paths()
    out = []
    for p in scripts:
        rel = str(p.relative_to(ROOT))
        marks = ()
        if rel in BASELINE:
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


def test_the_scan_still_finds_the_scripts():
    """A parametrize over an EMPTY list is not a failure to pytest: with the
    default `empty_parameter_set_mark` it becomes one silent skip, and this
    whole gate reports green over zero files. Both globs read engine-only trees
    (`scripts/` and `.claude/skills/*/scripts/`), so an empty result always
    means the glob or the layout moved, never a thin clone. 217 on 2026-08-26.
    """
    found = _params()

    assert len(found) >= 150, f"only {len(found)} scripts reached the purity gate"


def _tracked_skill_py() -> list[Path]:
    return [p for p in tracked_paths((".claude/skills/**/*.py",))
            if p.name != "__init__.py"]


def test_the_scan_reaches_skill_python_outside_a_scripts_subdirectory():
    """The 2026-08-31 widening, asserted as reach rather than as a count.

    The old glob was `.claude/skills/*/scripts/*.py`. Restoring it would leave
    every file below true again except this one, because a `cookbook/` or
    `eval-viewer/` file has no `scripts/` component in its path.
    """
    scanned = set(_scanned_paths())
    outside = [p for p in _tracked_skill_py()
               if not p.match(".claude/skills/*/scripts/*.py")
               and not _is_pep723_standalone(p)]
    assert outside, (
        "no tracked skill Python exists outside a scripts/ subdirectory, so "
        "this test can no longer detect the glob narrowing it guards"
    )
    missed = sorted(str(p.relative_to(ROOT)) for p in outside if p not in scanned)
    assert not missed, f"skill Python outside scripts/ that the gate never opens: {missed}"


def test_the_exemption_covers_only_pep723_standalones_and_nothing_inside_the_old_glob():
    """An exemption is coverage deleted in advance, so both edges are pinned.

    Upper edge: everything it frees really is a uv-run standalone with declared
    dependencies. Lower edge: it frees nothing that the pre-widening glob was
    already scanning, so it cannot be a baseline in disguise.
    """
    exempt = [p for p in _tracked_skill_py() if _is_pep723_standalone(p)]
    assert exempt, (
        "the PEP-723 exemption now matches nothing; either the cookbook was "
        "removed or the predicate broke, and a predicate that matches nothing "
        "silently stops being the reason this file gives"
    )
    for p in exempt:
        head = p.read_text(encoding="utf-8").splitlines()[0]
        assert "uv run" in head, f"{p.relative_to(ROOT)} exempted without a uv run shebang"

    inside_old_glob = sorted(
        str(p.relative_to(ROOT)) for p in exempt
        if p.match(".claude/skills/*/scripts/*.py"))
    assert not inside_old_glob, (
        "the exemption removed files the gate already covered before the glob "
        f"widened, which is a coverage regression, not an exemption: {inside_old_glob}"
    )


def test_the_deferring_sibling_is_still_held_to_the_contract():
    """combine_decks.py defers `pptx` with a comment naming this test.

    It sits in the same skill as the 22 exempt cookbook templates and imports
    the same package. If the exemption ever widened to reach it, the comment in
    that file would become a lie and nothing else would notice.
    """
    target = ROOT / ".claude/skills/pptx-generator/scripts/combine_decks.py"
    assert target.is_file(), f"{target} moved; re-point this guard"
    assert not _is_pep723_standalone(target)
    assert target in set(_scanned_paths()), (
        "combine_decks.py defers its pptx import specifically because this "
        "gate would fail it, and the gate no longer scans it"
    )


_UV_SHEBANG = "#!/usr/bin/env -S uv run\n"
_BLOCK_WITH_DEPS = (
    "# /// script\n"
    '# requires-python = ">=3.11"\n'
    "# dependencies = [\n"
    '#     "python-pptx==1.0.2",\n'
    "# ]\n"
    "# ///\n"
)
_BLOCK_NO_DEPS = "# /// script\n# requires-python = \">=3.11\"\n# ///\n"
_BLOCK_EMPTY_DEPS = (
    "# /// script\n"
    '# requires-python = ">=3.11"\n'
    "# dependencies = [\n"
    "# ]\n"
    "# ///\n"
)


@pytest.mark.parametrize(
    "shebang,block,exempt,why",
    [
        (_UV_SHEBANG, _BLOCK_WITH_DEPS, True,
         "uv run plus declared dependencies is the whole exemption"),
        ("", _BLOCK_WITH_DEPS, False,
         "a PEP-723 block on a file nobody executes says nothing about its env"),
        ("#!/usr/bin/env python3\n", _BLOCK_WITH_DEPS, False,
         "a plain interpreter shebang runs in the workspace env, block or not"),
        (_UV_SHEBANG, "", False,
         "uv run with no block resolves nothing and leans on the workspace env"),
        (_UV_SHEBANG, _BLOCK_NO_DEPS, False,
         "a block that declares no dependency does not install python-pptx"),
        (_UV_SHEBANG, _BLOCK_EMPTY_DEPS, False,
         "an empty dependencies list declares nothing either"),
    ],
)
def test_the_pep723_predicate_requires_both_halves(tmp_path, shebang, block, exempt, why):
    """Each clause of `_is_pep723_standalone`, bound one at a time.

    Every PEP-723 block in the live corpus declares `python-pptx`, so dropping
    the dependency requirement altogether changed no corpus verdict and the
    mutation survived. A predicate whose clauses are only ever exercised in one
    combination is a predicate with untested halves.
    """
    f = tmp_path / "candidate.py"
    f.write_text(shebang + block + "\nfrom pptx import Presentation\n", encoding="utf-8")
    assert _is_pep723_standalone(f) is exempt, why
