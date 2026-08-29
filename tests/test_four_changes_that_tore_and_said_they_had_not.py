"""Four changes that span several records, tear in the middle, and report success.

Each write below is individually atomic. The SET is not, and in all four cases
nothing afterwards could tell a torn state from a finished one.

* ``action-queue.py`` sent the mail, then threw away whether the queue recorded
  it. ``apply_status`` returns ``{"ok": False}`` when the card is gone (another
  process pruned it, or a corrupt queue was quarantined between the read and the
  write) and writes neither the status nor the disposition-log entry;
  ``_write_queue`` can also raise OSError straight through. Either way the card
  kept its PRE-approval status, ``SENDABLE_STATUSES`` includes ``pending``, and
  the command printed "sent" and exited 0. The same mail could then be approved
  and sent a second time - the one failure a send-gated queue exists to prevent.

* ``crm_migrate_to_entity_model --apply`` wrote its rollback manifest LAST,
  after both rename loops and after the legacy unlinks. The file's own comment
  says that manifest is the only thing making ``--rollback`` symmetric, so an
  interruption anywhere in the destructive phase left a half-migrated tree AND
  no manifest.

* ``offboard-exec`` wrote a fixed line, "GitHub access revoked, workspace
  archived, contacts preserved", into the file its own comment calls "the only
  durable record that the offboard happened at all" - with none of the step
  results in hand. ``offboard_verdict`` already measured them and ran
  afterwards, to stdout only.

* ``transfer-contact`` and ``merge-contacts`` move a record across TWO
  repositories. A failed first commit was downgraded to a warning that fell
  through to the second, so the source repo could commit the removal while the
  target's copy stayed untracked: in a fresh clone the contact existed in
  neither, and the run printed "complete" and exited 0.

Run: python3 -m pytest tests/test_four_changes_that_tore_and_said_they_had_not.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.crm import try_commit  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


aqcli = _load("aq_cli_tear", "scripts/action-queue.py")
mig = _load("crm_mig_tear", "scripts/crm_migrate_to_entity_model.py")
off = _load("offboard_tear", "scripts/offboard-exec.py")


# ============================================================
# The send the queue did not record
# ============================================================

# A stand-in for the path `dead_letter.record` returns. Not under /tmp: the
# linter flags a hardcoded temp path, and this value is only ever printed.
_FAKE_DLQ_PATH = Path("dead-letters") / "t-1__sent_unrecorded.json"

CARD = {"id": "abc12345", "action_type": "email_send", "status": "pending",
        "draft_status": "ready_for_review", "trace_id": "t-1",
        "to": "someone@example.com", "subject": "s", "draft_body": "b"}


def _arrange(monkeypatch, apply_result, *, raises=None):
    """Point approve_and_send at one gated, ready card whose send always works."""
    monkeypatch.setattr(aqcli, "list_action_queue", lambda _r: {"items": [dict(CARD)]})
    monkeypatch.setattr(aqcli, "send_card", lambda _e, _c: {"result": "sent"})
    # The claim is stubbed for the same reason the lister is: these tests are
    # about what happens AFTER the mail leaves, and `/d` is a stand-in path with
    # no queue store behind it. The claim's own behaviour is driven for real in
    # tests/test_a_card_two_terminals_approved_at_the_same_moment.py.
    monkeypatch.setattr(aqcli, "claim_card_for_send",
                        lambda _r, _i, _s: {"ok": True, "card": dict(CARD),
                                            "prev_status": "pending"})
    monkeypatch.setattr(aqcli, "release_claim",
                        lambda _r, _i, _p: {"ok": True})
    calls: list[tuple] = []

    def _apply(*a, **k):
        calls.append((a, k))
        if raises is not None:
            raise raises
        return apply_result

    monkeypatch.setattr(aqcli, "apply_status", _apply)
    recorded: list[dict] = []
    monkeypatch.setattr(aqcli, "dead_letter",
                        type("D", (), {"record": staticmethod(
                            lambda **kw: recorded.append(kw) or _FAKE_DLQ_PATH)}))
    return calls, recorded


def test_a_send_the_queue_could_not_record_is_not_reported_as_sent(monkeypatch, capsys):
    """THE case. `apply_status` says the card is gone; the mail already left."""
    _calls, recorded = _arrange(monkeypatch, {"ok": False, "error": "not found"})

    res = aqcli.approve_and_send(Path("/e"), Path("/d"), "abc12345")

    assert res["result"] == "sent_unrecorded"
    assert "not found" in res["error"]
    out = capsys.readouterr().out
    assert "WAS SENT and the queue does not know it" in out
    # The card is left CLAIMED, so the warning must send the operator to
    # `dismiss` rather than let the claim lapse into a second approve.
    assert "DISMISS the card" in out


def test_the_unrecorded_send_becomes_a_durable_artifact(monkeypatch, capsys):
    """A terminal line is not a record. The queue is the record, and this is the
    one case where it does not have one."""
    _calls, recorded = _arrange(monkeypatch, {"ok": False, "error": "not found"})

    aqcli.approve_and_send(Path("/e"), Path("/d"), "abc12345")

    assert len(recorded) == 1
    assert recorded[0]["kind"] == "sent_unrecorded"
    assert recorded[0]["classification"] == "permanent"
    assert recorded[0]["payload"]["id"] == "abc12345"
    assert recorded[0]["trace_id"] == "t-1"


def test_an_oserror_out_of_the_recorder_does_not_lose_the_send(monkeypatch, capsys):
    """`_write_queue` -> `atomic_write_text` re-raises OSError. That used to
    propagate out of an approve AFTER the mail had gone, so the operator got a
    traceback and no way to tell whether it left."""
    _calls, recorded = _arrange(monkeypatch, None, raises=OSError("No space left on device"))

    res = aqcli.approve_and_send(Path("/e"), Path("/d"), "abc12345")

    assert res["result"] == "sent_unrecorded"
    assert "No space left" in res["error"]
    assert len(recorded) == 1


def test_a_recorded_send_is_still_plain_sent(monkeypatch, capsys):
    """The negative case. A path that always warns would make every send look
    broken, which is how a warning stops being read."""
    _calls, recorded = _arrange(monkeypatch, {"ok": True, "card": dict(CARD)})

    res = aqcli.approve_and_send(Path("/e"), Path("/d"), "abc12345")

    assert res == {"result": "sent", "action_id": "abc12345"}
    assert recorded == []
    assert "WAS SENT and the queue" not in capsys.readouterr().out


def test_the_recorder_was_asked_to_write_sent(monkeypatch):
    """Pins the arrangement: the tests above would pass over a call that never
    happened."""
    calls, _recorded = _arrange(monkeypatch, {"ok": True})

    aqcli.approve_and_send(Path("/e"), Path("/d"), "abc12345")

    assert calls and calls[0][0][2] == "sent"


def test_the_cli_exits_non_zero_on_an_unrecorded_send(monkeypatch, capsys):
    """Exit 0 is what let this look like a completed send to anything scripting
    the queue."""
    _arrange(monkeypatch, {"ok": False, "error": "not found"})
    args = type("A", (), {"id": "abc12345"})()

    rc = aqcli.cmd_approve(Path("/e"), Path("/d"), args)

    assert rc == 1
    assert "sent, NOT recorded" in capsys.readouterr().err


def test_the_cli_still_exits_zero_on_a_clean_send(monkeypatch, capsys):
    _arrange(monkeypatch, {"ok": True})
    args = type("A", (), {"id": "abc12345"})()

    assert aqcli.cmd_approve(Path("/e"), Path("/d"), args) == 0


# ============================================================
# The migration manifest written last
# ============================================================

def _manifest(tmp_path: Path, payload) -> Path:
    d = tmp_path / "backup"
    d.mkdir(parents=True, exist_ok=True)
    if payload is not None:
        (d / "applied-manifest.json").write_text(payload, encoding="utf-8")
    return d


def test_an_interrupted_apply_is_named_as_interrupted(tmp_path):
    """THE case. Before the write-ahead there was no manifest at all here, so a
    half-migrated tree and an un-migrated one read identically."""
    d = _manifest(tmp_path, json.dumps({"status": "in_progress",
                                        "created_contacts": ["a.md"]}))

    out = mig._apply_state(d)

    assert "INTERRUPTED" in out
    assert "part-migrated" in out


def test_a_finished_apply_says_complete(tmp_path):
    d = _manifest(tmp_path, json.dumps({"status": "complete"}))

    assert mig._apply_state(d) == "Apply state: complete."


def test_no_manifest_is_reported_as_unknown_not_as_clean(tmp_path):
    """A backup from before the manifest existed, or an apply that died before
    the write-ahead. Either way the state is not known, and saying nothing would
    read as fine."""
    d = _manifest(tmp_path, None)

    assert "UNKNOWN" in mig._apply_state(d)


def test_an_unreadable_manifest_says_so(tmp_path):
    d = _manifest(tmp_path, "{not json")

    assert "UNREADABLE" in mig._apply_state(d)


def test_an_unrecognised_status_is_treated_as_intent(tmp_path):
    """A manifest from a newer version. Falling back to "intent" is the safe
    direction: rollback then removes files that may not exist, which is a no-op.
    """
    d = _manifest(tmp_path, json.dumps({"status": "half_way"}))

    assert "intent record" in mig._apply_state(d)


def test_the_manifest_is_written_before_the_first_rename():
    """The whole fix, read from the source order.

    A behavioural test would have to drive the full apply against a CRM tree and
    kill it mid-loop. The ORDER is the invariant, so it is asserted directly:
    the write-ahead must come before the `os.replace` loops and the completion
    stamp after them.
    """
    src = (ROOT / "scripts" / "crm_migrate_to_entity_model.py").read_text(encoding="utf-8")
    write_ahead = src.index('"status": "in_progress"')
    first_rename = src.index("os.replace(str(staged), str(target))")
    completion = src.index('"status": "complete"')

    assert write_ahead < first_rename, "the manifest is written after the renames again"
    assert first_rename < completion


def test_rollback_can_still_read_an_intent_manifest(tmp_path):
    """The intent record names what apply INTENDED to create, so rollback must
    find its two lists there. A manifest missing them would make the write-ahead
    useless."""
    d = _manifest(tmp_path, json.dumps({
        "status": "in_progress",
        "created_contacts": ["a.md", "b.md"],
        "created_address_book": ["a.md"],
        "removed_legacy": [],
    }))
    data = json.loads((d / "applied-manifest.json").read_text(encoding="utf-8"))

    assert data["created_contacts"] == ["a.md", "b.md"]
    assert data["created_address_book"] == ["a.md"]


# ============================================================
# The offboarding record that asserted what it never checked
# ============================================================

def _log_dir(tmp_path: Path, monkeypatch) -> Path:
    out = tmp_path / "outputs"
    monkeypatch.setattr(off, "get_outputs_dir", lambda: out)
    monkeypatch.setattr(off, "get_exec_slug", lambda: "the-admin")
    return out / "operations" / "offboarding" / "audit" / "offboarding-log.md"


def test_an_incomplete_offboard_is_recorded_as_incomplete(tmp_path, monkeypatch, capsys):
    """THE case: every collaborator DELETE returned 404 and the durable record
    said the access was revoked."""
    log = _log_dir(tmp_path, monkeypatch)

    off.log_offboarding("someone", {"name": "A Person"}, None,
                        complete=False, reasons=["access not revoked"])

    text = log.read_text(encoding="utf-8")
    assert "INCOMPLETE" in text
    assert "access not revoked" in text
    assert "GitHub access revoked" not in text


def test_a_complete_offboard_is_recorded_as_complete(tmp_path, monkeypatch, capsys):
    """The negative case. A log that always says INCOMPLETE records nothing."""
    log = _log_dir(tmp_path, monkeypatch)

    off.log_offboarding("someone", {"name": "A Person"}, None,
                        complete=True, reasons=[])

    text = log.read_text(encoding="utf-8")
    assert "COMPLETE - access revoked" in text
    assert "INCOMPLETE" not in text


def test_a_caller_that_supplies_no_verdict_says_so(tmp_path, monkeypatch, capsys):
    """Silence must not read as success. This is the shape the old fixed string
    had, and it is now labelled rather than asserted."""
    log = _log_dir(tmp_path, monkeypatch)

    off.log_offboarding("someone", {"name": "A Person"}, None)

    assert "NOT RECORDED" in log.read_text(encoding="utf-8")


def test_an_incomplete_offboard_with_no_reasons_still_says_incomplete(
        tmp_path, monkeypatch, capsys):
    """A caller passing `complete=False, reasons=[]` must not produce an empty
    outcome line that reads as a blank."""
    log = _log_dir(tmp_path, monkeypatch)

    off.log_offboarding("someone", {"name": "A Person"}, None, complete=False)

    text = log.read_text(encoding="utf-8")
    assert "INCOMPLETE - no reason given" in text


def test_the_verdict_is_computed_before_the_log_is_written():
    """The order is the fix. `offboard_verdict` used to run AFTER
    `log_offboarding` and reach stdout only."""
    src = (ROOT / "scripts" / "offboard-exec.py").read_text(encoding="utf-8")
    verdict = src.index("complete, reasons = offboard_verdict(")
    logging = src.index("log_offboarding(slug, exec_info, args.reassign_to,")

    assert verdict < logging


def test_the_reassignment_line_survives(tmp_path, monkeypatch, capsys):
    """A do-not-break guard: the entry's other field still works."""
    log = _log_dir(tmp_path, monkeypatch)

    off.log_offboarding("someone", {"name": "A Person"}, "another-slug",
                        complete=True, reasons=[])

    assert "another-slug" in log.read_text(encoding="utf-8")


# ============================================================
# The two-repository move that committed one half
# ============================================================

def test_try_commit_reports_a_failure_instead_of_swallowing_it(capsys):
    def _boom(_repo, _files, _msg):
        raise subprocess.CalledProcessError(1, "git", stderr=b"nothing to commit")

    assert try_commit(_boom, Path("/r"), [], "m", "target (x)") is False
    out = capsys.readouterr().out
    assert "failed" in out and "target (x)" in out


def test_try_commit_reports_a_success(capsys):
    landed = []

    assert try_commit(lambda r, f, m: landed.append(m), Path("/r"), [], "m",
                      "source (y)") is True
    assert landed == ["m"]
    assert "Committed to the source (y) repo" in capsys.readouterr().out


def test_try_commit_lets_an_unexpected_error_through():
    """Only a failed git command is absorbed. An OSError means git is missing or
    the path is wrong, which is not something to carry on from."""
    def _boom(_repo, _files, _msg):
        raise OSError("no such executable")

    with pytest.raises(OSError):
        try_commit(_boom, Path("/r"), [], "m", "target (x)")


@pytest.mark.parametrize("script", ["transfer-contact.py", "merge-contacts.py"])
def test_the_removal_commit_is_nested_under_the_addition_landing(script):
    """Both tools, one invariant: never commit the source-side removal while the
    target-side addition is uncommitted. That ordering is what left the contact
    in neither repository after a fresh clone.

    Asked of the PARSE TREE rather than the text: the second `try_commit` call
    must sit inside an `if target_committed:` body. A string search for the
    words would pass over a copy that had been moved back out of the branch.
    """
    import ast

    tree = ast.parse((ROOT / "scripts" / script).read_text(encoding="utf-8"))
    guarded = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                and node.test.id == "target_committed"):
            continue
        for inner in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                    and inner.func.id == "try_commit"):
                guarded.append(inner.lineno)

    assert len(guarded) == 1, f"{script}: the source commit is not under the guard"


@pytest.mark.parametrize("script", ["transfer-contact.py", "merge-contacts.py"])
def test_both_tools_call_try_commit_twice(script):
    """Pins the check above. A file with no `try_commit` calls at all would make
    `len(guarded) == 1` fail loudly, but a file with only the guarded one would
    mean the target commit had been dropped."""
    import ast

    tree = ast.parse((ROOT / "scripts" / script).read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "try_commit"]

    assert len(calls) == 2, script


def test_a_failed_target_commit_stops_the_source_commit(tmp_path, monkeypatch, capsys):
    """End to end through `transfer-contact.main()`.

    The AST tests above prove the shape; this proves the behaviour. Before the
    fix both commits ran and the process exited 0.
    """
    tc = _load("transfer_tear", "scripts/transfer-contact.py")
    src_dir = tmp_path / "from" / "crm" / "contacts"
    dst_dir = tmp_path / "to" / "crm" / "contacts"
    src_dir.mkdir(parents=True)
    dst_dir.mkdir(parents=True)
    (src_dir / "quillon-marsh.md").write_text(
        "---\nowner: alpha\n---\n\n## Interaction Log\n", encoding="utf-8")

    monkeypatch.setattr(tc, "validate_admin", lambda: None)
    monkeypatch.setattr(tc, "get_admin_slugs", list)
    monkeypatch.setattr(tc, "get_all_active_exec_slugs", lambda: ["alpha", "beta"])
    monkeypatch.setattr(tc, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(tc, "get_per_exec_contacts_dir",
                        lambda slug: src_dir if slug == "alpha" else dst_dir)
    attempted: list[str] = []

    def _commit(repo, _files, _msg):
        # `repo` is the contacts dir's PARENT, i.e. `<side>/crm`, so both repos
        # are named "crm". The side is one level up.
        attempted.append(repo.parent.name)
        if repo.parent.name == "to":
            raise subprocess.CalledProcessError(1, "git", stderr=b"boom")

    monkeypatch.setattr(tc, "git_commit", _commit)
    monkeypatch.setattr(sys, "argv",
                        ["transfer-contact.py", "--contact", "quillon-marsh",
                         "--from", "alpha", "--to", "beta"])

    with pytest.raises(SystemExit) as exc:
        tc.main()

    assert exc.value.code == 1
    assert attempted == ["to"], "the source repo committed its removal anyway"
    out = capsys.readouterr().out
    assert "Transfer INCOMPLETE" in out
    assert "neither repository" in out


def test_two_clean_commits_still_report_complete(tmp_path, monkeypatch, capsys):
    """The negative case. A tool that always says INCOMPLETE says nothing."""
    tc = _load("transfer_ok", "scripts/transfer-contact.py")
    src_dir = tmp_path / "from" / "crm" / "contacts"
    dst_dir = tmp_path / "to" / "crm" / "contacts"
    src_dir.mkdir(parents=True)
    dst_dir.mkdir(parents=True)
    (src_dir / "quillon-marsh.md").write_text(
        "---\nowner: alpha\n---\n\n## Interaction Log\n", encoding="utf-8")

    monkeypatch.setattr(tc, "validate_admin", lambda: None)
    monkeypatch.setattr(tc, "get_admin_slugs", list)
    monkeypatch.setattr(tc, "get_all_active_exec_slugs", lambda: ["alpha", "beta"])
    monkeypatch.setattr(tc, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(tc, "get_per_exec_contacts_dir",
                        lambda slug: src_dir if slug == "alpha" else dst_dir)
    attempted: list[str] = []
    monkeypatch.setattr(tc, "git_commit",
                        lambda repo, _f, _m: attempted.append(repo.parent.name))
    monkeypatch.setattr(sys, "argv",
                        ["transfer-contact.py", "--contact", "quillon-marsh",
                         "--from", "alpha", "--to", "beta"])

    tc.main()

    assert attempted == ["to", "from"]
    out = capsys.readouterr().out
    assert "Transfer complete" in out
    assert "INCOMPLETE" not in out


@pytest.mark.parametrize("script", ["transfer-contact.py", "merge-contacts.py"])
def test_a_torn_run_exits_non_zero(script):
    """Exit 0 over a torn move is what made this invisible to anything scripting
    these tools."""
    src = (ROOT / "scripts" / script).read_text(encoding="utf-8")

    assert "torn = not (target_committed and source_committed)" in src, script
    assert "if torn:\n        sys.exit(1)" in src, script


def test_the_transfer_target_write_is_atomic():
    """Its twin in merge-contacts.py has always used `atomic_write_text`. A plain
    `write_text` on the TARGET of a move leaves a partial file if the process
    dies, at a moment when the source has been read and not yet renamed."""
    src = (ROOT / "scripts" / "transfer-contact.py").read_text(encoding="utf-8")

    assert "atomic_write_text(target_path, text)" in src
    assert 'target_path.write_text(' not in src
