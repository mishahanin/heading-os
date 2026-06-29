"""End-to-end test: pristine HEADING OS clone -> full wizard run -> every file personalized."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent.parent


def test_pristine_heading_os_full_run(tmp_path):
    src = REPO / "tests" / "fixtures" / "pristine_heading_os"
    dest = tmp_path / "workspace"
    shutil.copytree(src, dest)

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
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


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
