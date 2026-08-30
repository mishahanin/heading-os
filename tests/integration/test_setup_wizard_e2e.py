"""End-to-end test: pristine HEADING OS clone -> full wizard run -> every file personalized."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.wizard_fixture import build_workspace

REPO = Path(__file__).parent.parent.parent


def test_pristine_heading_os_full_run(tmp_path):
    # Built from the SHIPPED wizard config, not the fixture's own copy: the
    # wizard resolves config against cwd, so a duplicate inside the fixture
    # shadows the files a real /setup-wizard reads. See
    # tests/integration/wizard_fixture.py.
    dest = build_workspace(tmp_path / "workspace")

    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "dev" / "wizard-simulate.py"),
         "--answers", str(REPO / "tests" / "fixtures" / "canned_public.yaml"),
         "--workspace", str(dest)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    content = (dest / "context" / "about.md").read_text()
    assert "{COMPANY" not in content
    assert "{CEO_" not in content
    assert "{TIMEZONE" not in content
    assert "Acme" in content
    assert "Jane Doe" in content

    voice_path = dest / "reference" / "ceo-voice.md"
    assert voice_path.exists()
    assert "direct" in voice_path.read_text().lower()

    env = (dest / ".env").read_text()
    assert "ANTHROPIC_API_KEY=TEST-FIXTURE-PUBLIC" in env  # pragma: allowlist secret

    state = json.loads((dest / ".setup" / "answers.json").read_text())
    assert state["answers"]["company_full_name"]["value"] == "Acme Corporation"
    assert state["answers"]["company_hq_cities"]["status"] == "skipped"

    status = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"), "--status",
         "--force-ceo-master"],
        cwd=dest, capture_output=True, text=True,
    )
    payload = json.loads(status.stdout)
    assert payload["completion_pct"] >= 80


import hashlib


def _hash_tree(root: Path) -> str:
    """SHA-256 over every file in `root`, path and bytes.

    REFUSES AN ABSENT OR EMPTY TREE since 2026-08-30. `rglob` on a directory
    that does not exist yields nothing, so both sides of the comparison below
    became the SHA-256 of empty input and the assertion passed. Deleting or
    renaming `tests/fixtures/exec_workspace/corporate` therefore turned the
    central guarantee -- the wizard must not touch `corporate/` -- into a
    comparison of two empty trees, with the rest of the test still green.
    Verified 2026-08-30: `rm -rf` that fixture directory and the old helper
    kept the test passing.
    """
    if not root.is_dir():
        raise AssertionError(
            f"{root} does not exist, so hashing it proves nothing about "
            f"whether the wizard modified it")
    files = [p for p in sorted(root.rglob("*")) if p.is_file()]
    if not files:
        raise AssertionError(
            f"{root} holds no files, so this hash is the hash of nothing and "
            f"cannot detect a modification")
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(root).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def test_the_tree_hash_refuses_an_absent_or_empty_directory(tmp_path):
    """The guard above, in both directions. NEW 2026-08-30.

    A helper that returned a constant for "nothing here" made the wizard test
    green over a deleted fixture. A helper that refused everything would be no
    better, so the populated case is pinned too.
    """
    with pytest.raises(AssertionError, match="does not exist"):
        _hash_tree(tmp_path / "never-created")

    empty = tmp_path / "empty"
    (empty / "sub").mkdir(parents=True)
    with pytest.raises(AssertionError, match="holds no files"):
        _hash_tree(empty)

    populated = tmp_path / "populated"
    populated.mkdir()
    (populated / "a.md").write_text("one\n", encoding="utf-8")
    first = _hash_tree(populated)
    assert first == _hash_tree(populated), "the hash is not stable"
    (populated / "a.md").write_text("two\n", encoding="utf-8")
    assert _hash_tree(populated) != first, "an edit did not change the hash"


def test_exec_workspace_only_touches_personal(tmp_path):
    src = REPO / "tests" / "fixtures" / "exec_workspace"
    dest = tmp_path / "workspace"
    shutil.copytree(src, dest)

    corporate_hash_before = _hash_tree(dest / "corporate")

    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "dev" / "wizard-simulate.py"),
         "--answers", str(REPO / "tests" / "fixtures" / "canned_exec.yaml"),
         "--workspace", str(dest)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    corporate_hash_after = _hash_tree(dest / "corporate")
    assert corporate_hash_before == corporate_hash_after, \
        "corporate/ must not be modified by the wizard on exec workspaces"

    assert (dest / "personal" / "reference" / "voice.md").exists()
    assert (dest / "personal" / "context" / "personal-info.md").exists()
    assert "ANTHROPIC_API_KEY=TEST-FIXTURE-EXEC" in (dest / ".env").read_text()  # pragma: allowlist secret
