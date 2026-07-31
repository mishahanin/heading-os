"""Frozen contract: a handoff the system generates can never block its own backup.

Frozen at the pre-impl gate for the handoff-redaction slice, before any
implementation exists. Every import of the code under test therefore sits inside
a test body; a module-scope import would stop this file collecting, and a file
that collects nothing cannot be frozen.

This is NOT a copy of the implementation suite in the plan. Four properties are
held here that the plan does not hold, and they are the reason this contract
earns its place rather than being a ceremony:

  1. THE REAL GATE, NOT ITS SCANNER. The plan says so itself, in a section
     titled "Traceability, stated as a limit": nothing in it proves push-all
     accepts a generated handoff, because content_scan() also SELECTS the files
     it scans and reproducing that needs a repository harness. The plan runs the
     scanner CLI on a file instead, and its SC-2 was restated during scrutiny to
     stop claiming more than that.

     It turns out the harness is cheap. _push_delta_files() falls back to
     `git ls-files` when origin/main is absent, so a throwaway repo with one
     commit exercises the real selection, the real scanner and the real refusal.
     test_the_real_push_gate_accepts_a_generated_handoff is that measurement, and
     test_the_real_push_gate_still_refuses_an_unredacted_handoff is the control
     without which it would pass for the wrong reason.

  2. DISCOVERY, NOT A NAMED LIST. The hook writes three files today and two of
     them carry the summary. A future author who adds a fourth breaks the
     property silently if the test names the files it checks. This contract
     globs whatever the hook actually wrote.

  3. NON-ASCII PROSE. The handoffs this workspace generates are largely in
     Russian. A redactor that mangles them is useless here and no test in the
     plan would notice, because every sample in it is ASCII.

  4. THE DIRECTION OF THE RECONCILIATION. Entry sixteen is being unified toward
     the scanner's tuned form, which LOOSENS the PreToolUse gate. The opposite
     reconciliation is the plausible future mistake: it would tighten the wall
     against every tracked file in both repositories. Pinned here so it cannot
     be made quietly.

Every credential-shaped sample is assembled at runtime by concatenation. None is
written whole into this file: it is tracked, the engine repository is public, and
the prevent-secrets hook refuses the write. That refusal is correct, and the
assembly is the workspace convention rather than a way around it.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[3]

SENTINEL = "not-a-real-token-userinfo"


def _poison() -> str:
    """A URL carrying a recognisable non-credential userinfo component.

    Written out in one piece this is refused by the prevent-secrets hook, which
    refused a design spec, two plan drafts and a probe of the previous slice for
    exactly this. No allow-list entry exists and none will be added.
    """
    return "https://" + "x-access-token" + ":" + SENTINEL + "@" + "github.com/owner/repo.git"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _repo_with_one_commit(base: Path) -> Path:
    """A git repo with no origin, so _push_delta_files falls back to ls-files."""
    repo = base / "overlay"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "builder@example.invalid")
    _git(repo, "config", "user.name", "Builder")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def _load_hook(tmp_path: Path, monkeypatch):
    """Load checkpoint-save.py with BOTH of its write targets redirected.

    HANDOFF_DIR resolves through get_outputs_dir() -> get_data_root(), which
    reads HEADING_OS_DATA at call time and is computed at module exec, so the
    env var must be set BEFORE exec_module.

    STATE_PATH does NOT go through the data root. It is an ENGINE path and
    main() writes it unconditionally, so a test that forgets it overwrites the
    live session's checkpoint state. The assertion below is what stops a silent
    redirect failure from writing into the operator's real archive.
    """
    import importlib.util

    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "checkpoint_save_contract", ENGINE / ".claude" / "hooks" / "checkpoint-save.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STATE_PATH", tmp_path / "checkpoint-state.json")
    assert tmp_path in module.HANDOFF_DIR.parents or tmp_path == module.HANDOFF_DIR, (
        f"sandbox escaped: HANDOFF_DIR is {module.HANDOFF_DIR}, not under {tmp_path}")
    return module


def _feed(module, monkeypatch, summary: str):
    import io
    import json

    payload = {"session_id": "s", "trigger": "manual",
               "compact_summary": summary, "transcript_path": ""}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    module.main()
    return module


def _load_push_all():
    """push-all.py as a module. Its name is not importable, hence the loader.

    tests/conftest.py sets the guard that makes its module-scope ensure_venv()
    a no-op, so importing it here does not re-exec the pytest process.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "push_all_contract", ENGINE / "scripts" / "push-all.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ============================================================
# The real gate, not its scanner
# ============================================================

def test_the_real_push_gate_accepts_a_generated_handoff(tmp_path, monkeypatch):
    """The span the plan admits it does not hold.

    content_scan() is the authoritative wall: it picks the files about to be
    pushed AND scans them AND refuses. Running the scanner CLI on a path proves
    the scanning half only. This drives the whole function over a repository
    carrying a handoff the hook actually generated.
    """
    push_all = _load_push_all()
    repo = _repo_with_one_commit(tmp_path / "r")

    module = _load_hook(repo, monkeypatch)
    _feed(module, monkeypatch, "the remote was " + _poison() + " at the time")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "handoff")

    push_all.content_scan(repo)  # must not raise SystemExit


def test_the_real_push_gate_still_refuses_an_unredacted_handoff(tmp_path):
    """The control, without which the test above passes for the wrong reason.

    A content_scan that accepted everything would make the flagship green while
    proving nothing. Here the poisoned text is written directly, with no hook and
    no redaction, and the gate must refuse it with exit 2.
    """
    push_all = _load_push_all()
    repo = _repo_with_one_commit(tmp_path / "r")
    (repo / "handoff.md").write_text("the remote was " + _poison() + " at the time\n",
                                     encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "poison")

    with pytest.raises(SystemExit) as caught:
        push_all.content_scan(repo)
    assert caught.value.code == 2


# ============================================================
# Discovery, not a named list
# ============================================================

def test_every_file_the_hook_writes_survives_the_scanner(tmp_path, monkeypatch):
    """Globbed rather than named, so a future fourth output file is covered on
    the day it lands instead of the day it leaks."""
    module = _load_hook(tmp_path, monkeypatch)
    _feed(module, monkeypatch, "remote " + _poison() + " here")

    written = sorted(module.HANDOFF_DIR.rglob("*"))
    files = [p for p in written if p.is_file()]
    assert len(files) >= 2, f"the hook wrote {len(files)} file(s), expected at least 2"

    result = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / "secret-scanner.py"),
         *[str(p) for p in files]],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout


# ============================================================
# The prose this workspace actually writes
# ============================================================

def test_non_ascii_prose_around_a_secret_survives_intact(tmp_path, monkeypatch):
    """Handoffs here are largely in Russian. Every sample in the plan is ASCII,
    so nothing in it would notice a redactor that mangled Cyrillic."""
    from scripts.utils.secret_patterns import redact

    before = "Задача 3 закрыта. Ремоут был "
    after = " на тот момент. Дальше задача 4."
    out = redact(before + _poison() + after)

    assert out.startswith(before)
    assert out.endswith(after)
    assert SENTINEL not in out


def test_redaction_is_idempotent():
    """Redacting a redacted summary must be a no-op.

    Not hypothetical plumbing: .latest/summary.md is rewritten on every compact,
    and any future path that re-reads an archived handoff and passes it through
    again must not nest markers inside markers.
    """
    from scripts.utils.secret_patterns import redact

    once = redact("remote " + _poison() + " end")

    # Asserted by CONTENT before idempotence, because equality alone is vacuous:
    # the probe caught exactly that. Against a null implementation both sides are
    # None and `redact(once) == once` is trivially true, so the test passed while
    # proving nothing. These two lines are what a mock cannot satisfy.
    assert SENTINEL not in once
    assert "[REDACTED:" in once

    assert redact(once) == once
    assert once.count("[REDACTED:") == 1, "a second pass nested a marker in a marker"


# ============================================================
# The direction of the reconciliation
# ============================================================

def test_the_wall_still_passes_a_placeholder_env_password(tmp_path):
    """Entry sixteen is being unified toward the scanner's tuned form, which
    loosens the PreToolUse gate. Reconciling the other way is the plausible
    future mistake, and it would tighten the WALL against every tracked file in
    both repositories. Asserted through the scanner CLI, which is the wall.
    """
    key = "EXCHANGE_" + "PASSWORD"
    target = tmp_path / "doc.md"
    target.write_text(key + "=" + "your-password-here" + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / "secret-scanner.py"), str(target)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout


# ============================================================
# The promise that the handoff is never the thing that is lost
# ============================================================

def test_a_failed_redaction_quarantines_instead_of_choosing_a_worse_loss(
        tmp_path, monkeypatch, capsys):
    """Three outcomes are possible when redaction fails and only one is right.

    Lose the handoff: unrecoverable, because this hook runs after the session's
    context is discarded and nobody can regenerate what it never wrote.

    Write it raw into the tracked archive: resurrects the incident this slice
    exists to remove. The wall refuses, the backup of the irreplaceable half of
    the workspace is blocked, and nobody finds out, because this hook's stderr
    is read by no one. Rarer and undiagnosed is a worse failure, not a better
    one. This was the plan's first answer and it was rejected.

    Quarantine: the memory is preserved outside the tracked tree, the wall is
    left unarmed, and the state is loud. That is the term pinned here.
    """
    module = _load_hook(tmp_path, monkeypatch)
    marker = "SENSITIVE-MARKER-" + "abc123"

    def _boom(_text):
        raise RuntimeError("redactor exploded")

    monkeypatch.setattr(module, "redact", _boom)
    _feed(module, monkeypatch, marker + " and some ordinary prose")

    quarantined = [p for p in module.QUARANTINE_DIR.rglob("*") if p.is_file()]
    assert quarantined, "the handoff was lost when the redactor raised"
    assert any(marker in p.read_text(encoding="utf-8") for p in quarantined)
    assert "redactor exploded" in capsys.readouterr().err

    # Nothing OUTSIDE the quarantine carries the text, or the tracked tree is
    # poisoned again and the quarantine bought nothing.
    outside = [p for p in module.HANDOFF_DIR.rglob("*")
               if p.is_file() and module.QUARANTINE_DIR not in p.parents]
    assert outside, "no pointer was written, so the next session is told nothing"
    for path in outside:
        assert marker not in path.read_text(encoding="utf-8"), f"{path} leaked it"
