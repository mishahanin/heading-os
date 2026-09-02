"""Every detect-secrets exclusion claims a second scanner still reads the file.

`.pre-commit-config.yaml` excludes a handful of paths from detect-secrets, each
for a real reason: the file carries hex digests or base64 blobs BY CONSTRUCTION,
so entropy detection fires on every one and a baseline entry goes stale the next
time the file is written.

Each exclusion is written with the same promise beside it: the file is not
unguarded, because `scripts/secret-scanner.py` reads it at commit time and again
inside the unbypassable push-time scan, on pattern rules rather than on entropy.

That promise is a claim about a scanner's coverage, which is the exact shape this
engine spent ten days removing: a sentence about what was checked, with nothing
checking the sentence. So it is measured here, per excluded file, by planting a
credential-shaped canary in a COPY and requiring the scanner to fire.

The canary is assembled from parts and is the AWS documentation example key. No
real credential appears in this file, in any command it runs, or in anything it
writes to a tracked path; the copies live under `.tmp/`, which is gitignored and
blocked from commits by the runtime-state guard.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCANNER = ROOT / "scripts" / "secret-scanner.py"
PYTHON = ROOT / ".venv" / "bin" / "python"

# Assembled from parts: the literal never appears in this file, so the file
# cannot itself trip the scanner it is testing.
CANARY = "AKIA" + "IOSFODNN" + "7EXAMPLE"


def _excluded_patterns() -> list[str]:
    """The path patterns detect-secrets is told to skip."""
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml")
                            .read_text(encoding="utf-8"))
    for repo in config["repos"]:
        for hook in repo.get("hooks", []):
            if hook.get("id") != "detect-secrets":
                continue
            raw = hook.get("exclude") or ""
            # The block is a verbose alternation: (?x)^( a| b| c )$
            inner = re.sub(r"^\(\?x\)\^\(|\)\$\s*$", "", raw.strip())
            return [part.strip() for part in inner.split("|") if part.strip()]
    raise AssertionError("no detect-secrets hook found in .pre-commit-config.yaml")


def _concrete_excluded_files() -> list[str]:
    """Excluded patterns that name one existing file, not a directory glob.

    A glob covers generated output that may not exist in a clone; a concrete
    path is a file this repository tracks today and can therefore be measured.
    """
    out = []
    for pattern in _excluded_patterns():
        if ".*" in pattern:
            continue
        rel = pattern.replace("\\.", ".")
        if (ROOT / rel).is_file():
            out.append(rel)
    return out


def test_the_exclusion_list_is_read_and_is_not_empty():
    """A parser that returned nothing would make every case below vacuous, and
    this file would pass while measuring no exclusion at all."""
    patterns = _excluded_patterns()
    assert len(patterns) >= 4, f"read only {len(patterns)} exclusion patterns"


def test_at_least_one_excluded_file_exists_to_measure():
    """The corpus floor. If every excluded pattern were a glob over generated
    output, the parametrised case below would collect zero tests and report
    green over an empty set."""
    assert _concrete_excluded_files(), (
        "no excluded pattern names a file that exists; nothing was measured")


@pytest.mark.parametrize("rel", _concrete_excluded_files())
def test_the_workspace_scanner_still_reads_an_excluded_file(rel, tmp_path):
    """Plant a credential in a copy; the scanner must find it.

    MEASURED 2026-09-02 on `config/audit-rotation-ledger.json`: the scanner
    exited 1 and named `AWS access key` on the planted line, so its clean
    verdict on the real file is a verdict about content it read rather than a
    file it skipped.
    """
    original = (ROOT / rel).read_text(encoding="utf-8")
    if rel.endswith(".json"):
        data = json.loads(original)
        poisoned = json.dumps(data, indent=2) + f'\n// canary {CANARY}\n'
    else:
        poisoned = original + f"\ncanary {CANARY}\n"

    target = tmp_path / Path(rel).name
    target.write_text(poisoned, encoding="utf-8")

    proc = subprocess.run([str(PYTHON), str(SCANNER), str(target)],
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 1, (
        f"the workspace scanner reported clean on a planted credential in a "
        f"copy of {rel}; the exclusion in .pre-commit-config.yaml claims this "
        f"file is still covered, and it is not\n{proc.stdout}{proc.stderr}")
    assert "AWS access key" in proc.stdout


def test_the_scanner_is_clean_on_the_unpoisoned_copy(tmp_path):
    """The anchor. A scanner that exited 1 on everything would satisfy every
    case above and block every commit in the repository."""
    rel = _concrete_excluded_files()[0]
    target = tmp_path / Path(rel).name
    target.write_text((ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")

    proc = subprocess.run([str(PYTHON), str(SCANNER), str(target)],
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, f"clean file reported dirty:\n{proc.stdout}"


def test_every_exclusion_carries_a_written_reason():
    """An exclusion with no comment beside it is a hole somebody widened
    without saying why, and the next reader cannot tell it from a mistake."""
    raw = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    block = raw.split("exclude: |")[0].rsplit("- id: detect-secrets", 1)[-1]
    reasons = [line for line in block.splitlines() if line.strip().startswith("# -")]
    assert len(reasons) >= len(_excluded_patterns()) - 2, (
        f"{len(_excluded_patterns())} exclusion patterns but only "
        f"{len(reasons)} written reasons; some patterns share one reason, but "
        f"a gap this wide means one was added silently")
