#!/usr/bin/env python3
"""Three ways `checkpoint-save.py` broke the one promise it is built on.

The hook is PostCompact (matcher `manual|auto`). It runs AFTER the session
context has been discarded, so whatever it fails to write is gone for good, and
its own comments say so four times. Each defect below is a place where the file
did the thing it warns about. MEASURED 2026-08-31 against this tree.

1. HIGH. The refs line asked `get_data_root()` and `data_overlay_present()`
   OUTSIDE every try. `HEADING_OS_DATA` is pinned per host, and a pinned
   directory that has been moved or deleted makes `data_overlay_present()` raise
   `DataRootError` by design ("Refusing to fall back"). `CP.handoff_dir()` was
   fixed on 2026-08-30 to catch exactly that, and it does, twice; then the hook
   raised it a third time one level up. Measured with the variable pointed at a
   path that does not exist:

       checkpoint: cannot resolve the data overlay (...); filing the handoff
       checkpoint: cannot resolve the data overlay (...); filing the handoff
       main() RAISED: DataRootError
       files under project: []

   Zero files. No archive, no quarantine, no pointer pair, no state reset, no
   systemMessage: the whole handoff, for a variable naming a directory that
   moved. Invisible because every reader stops at `handoff_dir()`, which handles
   this case and is quoted in the comment four lines below the raising line.

2. MEDIUM. The hook never asked `CP.session_id_is_known()`. With no `session_id`
   in the payload and no CLAUDE_CODE_SESSION_ID exported, `CP.session_id()`
   returns the shared sentinel, so `.latest/session/` is a bucket every id-less
   save writes. Measured:

       systemMessage: Saved handoff: outputs/.../..._compact-manual_session.md
       session_id_is_known: False   session_slug: session
       wrote .latest/session/{summary,prompt}.md and the shared pair

   The sentence names a slug and says nothing about it being shared, which is
   obligation 2 of `.claude/rules/scope-claims.md` unmet. Scope limit, kept
   rather than dropped: the 1,131 fallback-slug archives in the live tree from
   2026-08 all ALSO lack `trigger`, so they are probes and suite runs, not
   production compacts. What the measurement establishes is the missing refusal
   and the missing marker.

   The same defect one field over: `prune_pointer_dirs(hdir, session_slug)` was
   handed the ARTIFACT slug, which on the quarantine branch is the literal
   "unredacted", so the live session's own pointer dir became a prune candidate.
   The state path beside it was switched to `CP.session_slug(payload)` on
   2026-08-20 for this exact reason.

3. LOW. Module scope inlined `CP.force_utf8()`'s two `reconfigure` calls without
   its try/except, in the file whose premise is that an import-time failure costs
   a handoff. Measured with a stdout whose `reconfigure` raises
   ValueError("underlying buffer has been detached"), which is what a detached
   stream answers:

       import RAISED: ValueError: underlying buffer has been detached
       CP.force_utf8() SURVIVED

   The import died before `main()` existed, so the try/except inside `main()`
   that the file relies on never ran.

What this file pins, and each with a passing twin so no guard here can be green
over a one-sided corpus: a moved overlay costs no file AND a mounted overlay
still yields data-root-relative refs; an id-less save is labelled AND a known
session is not; the live pointer dir survives a quarantined save AND a genuinely
dead one is still pruned; a hostile stream cannot kill the import AND an ordinary
stream is still reconfigured to UTF-8.

Every test redirects both roots (`HEADING_OS_DATA`, `CLAUDE_PROJECT_DIR`) into
`tmp_path`. Nothing reaches the operator's live archive or a running session's
state file.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import time
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
SAVE_HOOK = ENGINE / ".claude" / "hooks" / "checkpoint-save.py"

SESSION = "dropped3-1111-2222-3333-444444444444"


def _load(name: str):
    """Import the hook by path. Import time is when it resolves HANDOFF_DIR and
    reconfigures the streams, so the caller arranges the world first."""
    spec = importlib.util.spec_from_file_location(name, str(SAVE_HOOK))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _project(tmp_path: Path, monkeypatch) -> Path:
    project = tmp_path / "project"
    (project / ".claude" / "state").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    # An inherited id would make the id-less cases below silently id-ful, and
    # Claude Code exports this to every child process.
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    return project


def _overlay(tmp_path: Path, monkeypatch) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(data))
    return data


def _run(mod, monkeypatch, payload: dict) -> str:
    """Drive main() and return its systemMessage.

    `redirect_stdout` rather than a monkeypatched `sys.stdout`, because undoing
    the patch to read the capture would undo every other patch the test set up
    too, including the roots.
    """
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        assert mod.main() == 0
    return json.loads(captured.getvalue())["systemMessage"]


def _files(root: Path) -> list[str]:
    return sorted(
        str(p.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and p.suffix != ".lock"
    )


# ---------------------------------------------------------------------------
# 1. A pinned data root that has moved
# ---------------------------------------------------------------------------

def test_a_moved_data_overlay_no_longer_takes_the_whole_handoff_with_it(
    tmp_path, monkeypatch
):
    """`HEADING_OS_DATA` naming a directory that is gone must cost nothing.

    Not sabotage and not hypothetical: the variable is pinned per host, the
    workspace has been relocated before, and the refusal it triggers is
    deliberate in `scripts/utils/paths.py`. The assertion is on FILES rather than
    on a return code, because a hook that exits 0 having written nothing is the
    failure this file is about.
    """
    project = _project(tmp_path, monkeypatch)
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "overlay-that-moved"))

    mod = _load("cksave_moved_overlay")
    message = _run(mod, monkeypatch, {
        "session_id": SESSION,
        "trigger": "manual",
        "compact_summary": "the body that used to be lost",
        "transcript_path": "",
        "cwd": str(project),
    })

    archive = project / ".claude" / "state" / "handoff"
    bodies = sorted(archive.glob("*.md"))
    assert bodies, (
        f"no handoff body was written; the project tree holds {_files(project)}"
    )
    assert "the body that used to be lost" in bodies[0].read_text(encoding="utf-8")

    latest = archive / ".latest"
    for pointer in ("summary.md", "prompt.md"):
        assert (latest / pointer).is_file(), f"the shared {pointer} is missing"
        assert (latest / mod.CP.safe_slug(SESSION) / pointer).is_file(), (
            f"the per-session {pointer} is missing"
        )
    assert (project / ".claude" / "state"
            / f"checkpoint-{mod.CP.session_slug({'session_id': SESSION})}.json"
            ).is_file(), "the state reset never happened"
    assert message.startswith("Saved handoff: "), message


def test_the_refs_fall_back_to_the_root_the_archive_landed_under(
    tmp_path, monkeypatch
):
    """Surviving is half of it. The refs must describe where the file IS.

    The fallback is the project root because that is where `handoff_dir()`
    redirects the archive on this same failure. Any other choice sends every ref
    down `_ref`'s absolute-path branch, which is legal output and a worse
    outcome: the pointer, the systemMessage and the state entry then all name an
    absolute path a data-root-relative reader cannot resolve.
    """
    project = _project(tmp_path, monkeypatch)
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "overlay-that-moved"))

    mod = _load("cksave_moved_overlay_refs")
    message = _run(mod, monkeypatch, {
        "session_id": SESSION,
        "trigger": "auto",
        "compact_summary": "ref probe",
        "transcript_path": "",
        "cwd": str(project),
    })

    ref = message[len("Saved handoff: "):].strip()
    assert not ref.startswith("/"), f"the ref went absolute: {ref}"
    assert (project / ref).is_file(), (
        f"the systemMessage named {ref}, which does not exist under the project "
        f"root; the tree holds {_files(project)}"
    )


def test_a_mounted_overlay_still_follows_the_data_seam(tmp_path, monkeypatch):
    """The passing twin. A blanket fallback to the project root would satisfy
    every assertion above and quietly strand the operator's whole archive inside
    the engine clone, so the healthy path is measured in the same file."""
    data = _overlay(tmp_path, monkeypatch)
    project = _project(tmp_path, monkeypatch)

    mod = _load("cksave_live_overlay")
    message = _run(mod, monkeypatch, {
        "session_id": SESSION,
        "trigger": "manual",
        "compact_summary": "seam probe",
        "transcript_path": "",
        "cwd": str(project),
    })

    ref = message[len("Saved handoff: "):].strip()
    assert ref.startswith("outputs/operations/handoff-archive/"), ref
    assert (data / ref).is_file(), f"{ref} is not under the data root"


# ---------------------------------------------------------------------------
# 2. An id-less payload writes a cross-session bucket
# ---------------------------------------------------------------------------

def _id_less_payload(project: Path) -> dict:
    return {
        "trigger": "manual",
        "compact_summary": "id-less probe body",
        "transcript_path": "",
        "cwd": str(project),
    }


def test_an_id_less_save_names_the_bucket_it_wrote(tmp_path, monkeypatch):
    """`.latest/session/` is shared by every id-less save, so a sentence about
    whose handoff it holds is a claim the method never established.

    Both surfaces are asserted, because they reach different readers: the
    systemMessage is what the operator and the model see on the turn, and the
    shared pointer's `## Next steps` is what `scripts/next-signal.py` renders for
    `/next` on some later day.
    """
    data = _overlay(tmp_path, monkeypatch)
    project = _project(tmp_path, monkeypatch)

    mod = _load("cksave_idless")
    payload = _id_less_payload(project)
    assert not mod.CP.session_id_is_known(payload), (
        "the payload was supposed to carry no resolvable id; the premise of "
        "this test is gone"
    )
    slug = mod.CP.session_slug(payload)
    assert slug == mod.CP.FALLBACK_SESSION_ID

    message = _run(mod, monkeypatch, payload)

    bucket = data / "outputs" / "operations" / "handoff-archive" / ".latest"
    assert (bucket / slug / "summary.md").is_file(), (
        "the per-session pointer was not written at all, so there is no bucket "
        "to label and this test measures nothing"
    )
    assert slug in message and "shared" in message, (
        f"the systemMessage does not name the shared bucket: {message!r}"
    )
    pointer = (bucket / "summary.md").read_text(encoding="utf-8")
    assert "No session id reached this hook" in pointer, (
        f"the shared pointer carries no marker:\n{pointer}"
    )
    steps = pointer.split("## Next steps", 1)[1].split("\n##", 1)[0]
    assert "shared fallback slug" in steps, (
        "the marker is outside `## Next steps`, which is one of the two sections "
        f"next-signal.py renders, so /next will never show it:\n{pointer}"
    )


def test_a_known_session_is_not_labelled_a_bucket(tmp_path, monkeypatch):
    """The passing twin, and the one that matters most here: a marker printed
    unconditionally is noise, and noise on the alarm channel is how a real alarm
    stops being read."""
    data = _overlay(tmp_path, monkeypatch)
    project = _project(tmp_path, monkeypatch)

    mod = _load("cksave_known_session")
    message = _run(mod, monkeypatch, {
        "session_id": SESSION,
        "trigger": "manual",
        "compact_summary": "known-session probe",
        "transcript_path": "",
        "cwd": str(project),
    })

    assert "shared fallback slug" not in message, message
    assert message.startswith("Saved handoff: "), message
    bucket = data / "outputs" / "operations" / "handoff-archive" / ".latest"
    for pointer in (bucket / "summary.md",
                    bucket / mod.CP.safe_slug(SESSION) / "summary.md"):
        text = pointer.read_text(encoding="utf-8")
        assert "shared fallback slug" not in text, (
            f"{pointer} labels a session that named itself:\n{text}"
        )


def test_the_environment_alone_still_answers_the_id(tmp_path, monkeypatch):
    """`session_id_is_known` reads the payload OR CLAUDE_CODE_SESSION_ID, and so
    must the label. A check that only looked at `payload["session_id"]` would
    call every `/checkpoint`-shaped invocation a shared bucket."""
    _overlay(tmp_path, monkeypatch)
    project = _project(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)

    mod = _load("cksave_env_session")
    message = _run(mod, monkeypatch, _id_less_payload(project))
    assert "shared fallback slug" not in message, message


# ---------------------------------------------------------------------------
# 2b. The prune keep-slug: the same wrong-slug family, one field over
# ---------------------------------------------------------------------------

OLD = 30 * 86400  # comfortably past KEEP_DAYS, so the 14-day cutoff bites


def _seed_pointer_dir(latest: Path, slug: str) -> Path:
    directory = latest / slug
    directory.mkdir(parents=True)
    (directory / "summary.md").write_text(f"seeded pointer for {slug}\n",
                                          encoding="utf-8")
    old = time.time() - OLD
    for path in (directory / "summary.md", directory):
        os.utime(path, (old, old))
    return directory


def test_a_quarantined_save_does_not_prune_the_live_session_pointer(
    tmp_path, monkeypatch
):
    """On the quarantine branch the artifact slug is the literal "unredacted", so
    passing it as `keep_slug` protected a directory nobody else owns and left the
    real session's pointer dir on the prune list. Driven through the real
    quarantine path by a raising redactor, with the seeded dirs aged past
    KEEP_DAYS so the decision is actually taken rather than deferred by the cap.
    """
    data = _overlay(tmp_path, monkeypatch)
    project = _project(tmp_path, monkeypatch)

    mod = _load("cksave_prune_slug")
    latest = data / "outputs" / "operations" / "handoff-archive" / ".latest"
    live = _seed_pointer_dir(latest, mod.CP.safe_slug(SESSION))
    dead = _seed_pointer_dir(latest, "a-session-that-ended-long-ago")

    def _raise(_text):
        raise RuntimeError("test: redactor down")

    monkeypatch.setattr(mod, "redact", _raise)
    message = _run(mod, monkeypatch, {
        "session_id": SESSION,
        "trigger": "manual",
        "compact_summary": "quarantine probe",
        "transcript_path": "",
        "cwd": str(project),
    })
    assert "QUARANTINED" in message, (
        f"the quarantine branch was not taken, so the artifact slug is not "
        f'"unredacted" and this test measures nothing: {message!r}'
    )

    assert live.is_dir(), (
        "the live session's pointer dir was pruned by its own save; the keep "
        "slug named the artifact, not the session"
    )
    assert not dead.exists(), (
        "no pruning happened at all, so the assertion above is green over an "
        "inert prune rather than a correct one"
    )


# ---------------------------------------------------------------------------
# 3. Stream setup at module scope
# ---------------------------------------------------------------------------

class _Hostile:
    """A stream that refuses `reconfigure` and delegates everything else.

    The refusal is real rather than invented: a `TextIOWrapper` whose buffer has
    been detached answers exactly this, and the delegation is what lets the
    handler's own stderr line get out.
    """

    def __init__(self, real):
        self._real = real
        self.asked = 0

    def reconfigure(self, **kwargs):
        self.asked += 1
        raise ValueError("underlying buffer has been detached")

    def __getattr__(self, name):
        return getattr(self._real, name)


class _Recorder:
    """Same delegation, but it accepts and remembers the call."""

    def __init__(self, real):
        self._real = real
        self.calls: list[dict] = []

    def reconfigure(self, **kwargs):
        self.calls.append(kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_a_stream_that_refuses_reconfigure_cannot_kill_the_import(
    tmp_path, monkeypatch, capsys
):
    """An import-time failure here costs the handoff outright.

    Nothing in `main()` can help: the module never finishes executing, so
    `main()` does not exist to be called and the try/except this file leans on
    never runs. Both streams are made hostile, so the fix cannot pass by
    guarding one of them.
    """
    _overlay(tmp_path, monkeypatch)
    project = _project(tmp_path, monkeypatch)

    # Held in locals and never undone. `monkeypatch.undo()` would restore the
    # REAL `HEADING_OS_DATA` mid-test, and main() resolves the archive again from
    # the environment, so the run below would write a handoff into the operator's
    # live overlay.
    out, err = _Hostile(sys.stdout), _Hostile(sys.stderr)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    mod = _load("cksave_hostile_streams")
    assert out.asked and err.asked, (
        "neither stream was asked to reconfigure, so the import never reached "
        "the line under test"
    )

    assert "stream reconfigure failed" in capsys.readouterr().err, (
        "the failure was swallowed; a guard that logs nothing is how stream "
        "setup breaks a hook silently"
    )

    # And the hook still works, which is the point of surviving the import.
    message = _run(mod, monkeypatch, {
        "session_id": SESSION,
        "trigger": "manual",
        "compact_summary": "hostile-stream probe",
        "transcript_path": "",
        "cwd": str(project),
    })
    assert message.startswith("Saved handoff: "), message


def test_an_ordinary_stream_is_still_reconfigured_to_utf8(tmp_path, monkeypatch):
    """The passing twin. Deleting the two calls outright would satisfy the test
    above and lose what they are for: Windows defaults to CP1252, and the hook
    writes a systemMessage that can carry any character the summary did."""
    _overlay(tmp_path, monkeypatch)
    _project(tmp_path, monkeypatch)

    out, err = _Recorder(sys.stdout), _Recorder(sys.stderr)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    _load("cksave_recorded_streams")

    for recorded in (out.calls, err.calls):
        assert recorded == [{"encoding": "utf-8", "errors": "replace"}], recorded
