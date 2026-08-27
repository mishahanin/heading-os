"""One place that builds a wizard test workspace, from the SHIPPED config.

`tests/fixtures/pristine_heading_os/` used to carry its own
`config/wizard-questions.yaml` and its own `config/wizard-templates/*.tmpl`, and
`apply-wizard-answers.py` resolves both against `Path.cwd()`. Both integration
tests run it with `cwd=dest`, so they rendered the FIXTURE's copies and never
read the files a real `/setup-wizard` uses - while the snapshot test's docstring
said it "catches accidental drift in wizard-templates/*.tmpl".

The duplication had already drifted: on 2026-08-27 the shipped
`config/wizard-questions.yaml` and the fixture's copy differed on three
`example:` lines, and nothing in the suite noticed. Breaking the shipped
`personal-info.md.tmpl` - dropping its `## Background` section, or renaming
`{{ personal_info_draft }}` so the body renders empty - left both tests green
while every real wizard run emitted a gutted `context/personal-info.md`.

So the fixture no longer carries wizard config at all, and this helper copies the
shipped files in. One copy in the repository, and the tests render what ships.
"""
from __future__ import annotations

import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
PRISTINE = REPO / "tests" / "fixtures" / "pristine_heading_os"

# What the wizard reads at run time, relative to the workspace root.
SHIPPED_CONFIG = ("config/wizard-questions.yaml", "config/wizard-templates")


def build_workspace(dest: Path, src: Path | None = None) -> Path:
    """Copy the pristine fixture to `dest` and overlay the shipped wizard config."""
    shutil.copytree(src or PRISTINE, dest)
    for rel in SHIPPED_CONFIG:
        source = REPO / rel
        target = dest / rel
        if not source.exists():
            raise AssertionError(
                f"{rel} is missing from the repository, so the wizard tests would "
                f"silently render nothing. Point SHIPPED_CONFIG at its new home."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    return dest
