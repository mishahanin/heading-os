"""The checkpoint hooks must behave when they ship as a plugin bundle.

The same four files run in two layouts: `.claude/hooks/` in this monorepo, and
`hooks/` inside a bundle copied to `~/.claude/plugins/cache/`. The difference is
not cosmetic. A hook that resolves its paths from its OWN location is resolving
them inside the plugin cache, where the consumer's repository is not.

Both halves of that were real. Measured on 2026-08-16 against the first built
bundle:

  - the root walk: `parent.parent.parent` lands above the bundle, so
    `scripts.utils` did not import and the hook died before writing anything;
  - the archive: it resolved through the engine's data seam, so a handoff
    belonging to a scratch repository was written into the OPERATOR's live
    archive and overwrote the shared pointer there.

So the bundle is built and driven for real here rather than reasoned about.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent
SESSION = "dddddddd-1111-2222-3333-444444444444"
SLUG = SESSION[:32]


def _api_key() -> str:
    """Assembled, never written whole: this file is tracked and public."""
    return "sk-ant-" + ("A" * 24)


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    out = tmp_path_factory.mktemp("marketplace")
    result = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / "dev" / "build-plugins.py"),
         "--bundle", "heading-core", "--out", str(out)],
        capture_output=True, text=True, cwd=str(ENGINE),
    )
    assert result.returncode == 0, f"bundle build failed:\n{result.stdout}\n{result.stderr}"
    return out / "plugins" / "heading-core"


@pytest.fixture()
def consumer(tmp_path):
    """A stranger's repository, plus a decoy data root beside it.

    The decoy is the assertion that matters: if the bundle ever resolves the
    archive through the engine's data seam again, it lands there instead of in
    the repository, and the test says so.
    """
    project = tmp_path / "someones-repo"
    project.mkdir()
    decoy = tmp_path / "decoy-data-root"
    decoy.mkdir()
    env = dict(os.environ)
    env["HEADING_OS_DATA"] = str(decoy)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("CLAUDE_HANDOFF_AUTO", None)
    return {"project": project, "decoy": decoy, "env": env}


def _run(bundle: Path, name: str, consumer: dict, payload: dict):
    payload = {
        "cwd": str(consumer["project"]),
        "workspace": {"project_dir": str(consumer["project"])},
        **payload,
    }
    return subprocess.run(
        [sys.executable, str(bundle / "hooks" / name)],
        input=json.dumps(payload), capture_output=True, text=True, env=consumer["env"],
    )


def _save(bundle, consumer, summary, session=SESSION):
    return _run(bundle, "checkpoint-save.py", consumer, {
        "session_id": session, "trigger": "manual",
        "compact_summary": summary, "transcript_path": "",
    })


def test_every_bundled_hook_starts_at_all(bundle, consumer):
    """The root walk. Before it, three of the four died on an ImportError."""
    for name, payload in (
        ("checkpoint-statusline.py", {"session_id": SESSION,
                                      "context_window": {"used_percentage": 46}}),
        ("checkpoint-offer.py", {"session_id": SESSION, "stop_hook_active": False}),
        ("checkpoint-inject.py", {"session_id": SESSION, "source": "resume"}),
        ("checkpoint-save.py", {"session_id": SESSION, "trigger": "manual",
                                "compact_summary": "s", "transcript_path": ""}),
    ):
        proc = _run(bundle, name, consumer, payload)
        assert proc.returncode == 0, f"{name} exited {proc.returncode}:\n{proc.stderr}"
        assert "Traceback" not in proc.stderr, f"{name} crashed:\n{proc.stderr}"


def test_the_handoff_lands_in_the_consumers_repository(bundle, consumer):
    proc = _save(bundle, consumer, "CONSUMER-WORK")
    assert proc.returncode == 0, proc.stderr

    archive = consumer["project"] / ".claude" / "handoff"
    written = list(archive.glob("*_handoff_compact-manual_*.md"))
    assert written, f"no handoff under {archive}"
    assert "CONSUMER-WORK" in written[0].read_text(encoding="utf-8")


def test_nothing_reaches_the_operators_data_root(bundle, consumer):
    """The measured incident: a scratch session's handoff written into the
    operator's live archive, overwriting the shared pointer that /next reads."""
    _save(bundle, consumer, "CONSUMER-WORK")
    strays = [p for p in consumer["decoy"].rglob("*") if p.is_file()]
    assert not strays, f"the bundle wrote into the data root: {strays}"


def test_state_lands_in_the_consumers_repository(bundle, consumer):
    _run(bundle, "checkpoint-statusline.py", consumer, {
        "session_id": SESSION, "context_window": {"used_percentage": 46},
    })
    state = consumer["project"] / ".claude" / "state" / f"checkpoint-{SLUG}.json"
    assert state.is_file(), "state did not land in the consumer's repo"


def test_the_bundle_injects_only_its_own_session(bundle, consumer):
    _save(bundle, consumer, "SESSION-D-WORK")
    _save(bundle, consumer, "SESSION-E-WORK", session="eeeeeeee-0000-0000-0000-000000000000")

    out = _run(bundle, "checkpoint-inject.py", consumer,
               {"session_id": SESSION, "source": "resume"}).stdout
    assert "SESSION-D-WORK" in out
    assert "SESSION-E-WORK" not in out


def test_the_bundle_still_redacts(bundle, consumer):
    """The capability the nexi plugin does not have: a compact summary reaches
    disk through the redactor, in the bundle as much as in the monorepo."""
    _save(bundle, consumer, "the key " + _api_key() + " was rotated")

    bodies = [
        p.read_text(encoding="utf-8")
        for p in (consumer["project"] / ".claude" / "handoff").rglob("*.md")
    ]
    assert bodies, "nothing was written"
    for body in bodies:
        assert _api_key() not in body, "a credential-shaped span reached disk"
    assert any("[REDACTED: Anthropic API key]" in b for b in bodies), (
        "the redactor did not run in bundle mode"
    )
