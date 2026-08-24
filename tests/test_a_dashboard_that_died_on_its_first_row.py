"""A loop that read a name it never bound, and two fleet tools that disagreed.

Covers the k3 audit shard `scripts-00-p1` for `scripts/admin-health.py`,
`scripts/action-queue-execute.py` and `scripts/aggregate-crm.py`.

*A crash the empty case hid.* `collect_exec_state` unpacked each row into
`for slug, _repo_path in exec_repos:` and then called
`read_last_commit(repo_path)` -- a name bound nowhere in that scope. The FIRST
iteration raised NameError, so the fleet dashboard produced no table, no JSON
and no degraded row: it just died. With zero execs the loop body never runs,
which is how it shipped, and it is the same shape as the `cmd_add` NameError
found in `scripts/google-contacts.py` hours earlier.

*A contract broken on one branch.* `send_card`'s docstring promises `attempt`
on every `send_failed` result, and every path carried it except the
`telegram_send` 501. Adding it exposed a second problem: `attempt` was derived
BELOW that branch, so naming it there would itself have been a NameError. The
derivation moved above the first branch that reports it.

*Two tools, one repo name, two answers.* `admin-health.py` resolved an exec's
overlay repo through the roster's `data_repo` field; `aggregate-crm.py`
hardcoded `heading-os-data-{slug}`. An exec whose row named a different repo
was cloned correctly by the dashboard and 404'd by the aggregation, which then
contributed zero of their contacts and exited 0. One resolver now, in
`scripts/utils/workspace.py`.

One audit item was refuted rather than fixed: `cmd_retry` is not dead code.
`ACTIVE_STATUSES` in `scripts/bridge_daemon/sources/action_queue.py` does
contain `send_failed`, and the test below pins that.

Nothing here sends, clones, or reaches GitHub.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from scripts.bridge_daemon.sources.action_queue import ACTIVE_STATUSES
from scripts.utils.workspace import repo_name_for

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_").replace(".py", ""), str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _code(name: str) -> str:
    """Source minus whole-line comments; each fix left one quoting the old code."""
    text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
    return "\n".join(ln for ln in text.split("\n") if not ln.lstrip().startswith("#"))


# ============================================================
# The dashboard survives its first row
# ============================================================

@pytest.fixture(scope="module")
def health():
    return _load("admin-health.py")


def test_collect_exec_state_does_not_raise_on_a_non_empty_fleet(health, tmp_path,
                                                                monkeypatch):
    """The whole defect: with one exec, the first iteration raised NameError."""
    contacts = tmp_path / "bond" / "crm" / "contacts"
    contacts.mkdir(parents=True)
    (contacts / "m.md").write_text("x\n", encoding="utf-8")
    (contacts / "q.md").write_text("x\n", encoding="utf-8")
    (contacts / "README.md").write_text("not a contact\n", encoding="utf-8")

    seen_paths = []

    def _last_commit(path):
        seen_paths.append(path)
        return "2026-08-24T10:00:00+00:00"

    monkeypatch.setattr(health, "get_per_exec_contacts_dir", lambda _s: contacts)
    monkeypatch.setattr(health, "read_last_commit", _last_commit)

    repo = tmp_path / "bond-repo"
    records = health.collect_exec_state([("bond", repo)])

    assert len(records) == 1
    assert records[0]["slug"] == "bond"
    assert records[0]["last_commit"] == "2026-08-24T10:00:00+00:00"
    assert records[0]["contact_count"] == 2, "README.md is not a contact"
    assert seen_paths == [repo], (
        "the row's own repo path must reach read_last_commit"
    )


def test_every_exec_gets_its_own_repo_path(health, tmp_path, monkeypatch):
    """A single shared name would have passed the one-row test above."""
    contacts = tmp_path / "c"
    contacts.mkdir()
    seen = []
    monkeypatch.setattr(health, "get_per_exec_contacts_dir", lambda _s: contacts)
    monkeypatch.setattr(health, "read_last_commit",
                        lambda p: seen.append(p) or None)

    a, b = tmp_path / "a-repo", tmp_path / "b-repo"
    health.collect_exec_state([("alpha", a), ("bravo", b)])
    assert seen == [a, b]


def test_an_empty_fleet_is_still_fine(health):
    assert health.collect_exec_state([]) == []


def test_the_loop_binds_the_name_it_reads():
    code = _code("admin-health.py")
    start = code.index("def collect_exec_state(")
    body = code[start:code.index("def calculate_status(", start)]
    assert "for slug, repo_path in exec_repos:" in body
    assert "for slug, _repo_path in exec_repos:" not in body, (
        "unpacking into a throwaway name and then reading the real one is the bug"
    )


# ============================================================
# --help says what the module says
# ============================================================

def test_the_help_text_no_longer_claims_a_sync_handshake():
    code = _code("admin-health.py")
    assert "monitor executive workspace sync status" not in code, (
        "the column, the function and a whole regression test were renamed to "
        "stop claiming this; --help was the last surface still saying it"
    )
    assert "last commit and contact count" in code.lower()


# ============================================================
# One repo name, one resolver
# ============================================================

def test_the_repo_name_falls_back_to_the_convention(monkeypatch):
    import scripts.utils.workspace as ws
    monkeypatch.setattr(ws, "load_fleet", lambda: [{"slug": "bond"}])
    assert ws.repo_name_for("bond") == "heading-os-data-bond"


def test_the_roster_data_repo_wins_over_the_convention(monkeypatch):
    import scripts.utils.workspace as ws
    monkeypatch.setattr(ws, "load_fleet",
                        lambda: [{"slug": "bond", "data_repo": "custom-overlay"}])
    assert ws.repo_name_for("bond") == "custom-overlay"


def test_an_unknown_slug_falls_back_rather_than_raising(monkeypatch):
    import scripts.utils.workspace as ws
    monkeypatch.setattr(ws, "load_fleet", lambda: [{"slug": "other"}])
    assert ws.repo_name_for("bond") == "heading-os-data-bond"


def test_a_blank_data_repo_is_not_a_repo_name(monkeypatch):
    import scripts.utils.workspace as ws
    monkeypatch.setattr(ws, "load_fleet",
                        lambda: [{"slug": "bond", "data_repo": ""}])
    assert ws.repo_name_for("bond") == "heading-os-data-bond"


def test_the_resolver_is_importable_from_the_shared_seam():
    assert callable(repo_name_for)


@pytest.mark.parametrize("script", ["admin-health.py", "aggregate-crm.py"])
def test_both_fleet_tools_resolve_the_repo_name_through_the_seam(script):
    code = _code(script)
    assert "repo_name_for" in code
    assert 'f"heading-os-data-{slug}"' not in code, (
        f"{script} hardcoding the convention is how the two tools drifted"
    )


def test_the_clone_command_uses_the_resolver():
    code = _code("aggregate-crm.py")
    assert 'f"{org}/{repo_name_for(slug)}"' in code


# ============================================================
# send_card keeps its own contract
# ============================================================

@pytest.fixture(scope="module")
def executor():
    return _load("action-queue-execute.py")


def test_the_unimplemented_telegram_send_still_reports_attempt(executor, tmp_path):
    card = {"id": "a1", "action_type": "telegram_send", "attempt": 3}
    result = executor.send_card(tmp_path, card)
    assert result["result"] == "send_failed"
    assert result["classification"] == "permanent"
    assert result["attempt"] == 3, (
        "every other send_failed path carries attempt; a caller persisting it "
        "uniformly hit KeyError on this one branch"
    )


def test_a_card_with_no_attempt_reports_zero(executor, tmp_path):
    result = executor.send_card(tmp_path, {"id": "a1",
                                           "action_type": "telegram_send"})
    assert result["attempt"] == 0


@pytest.mark.parametrize("bad", [-4, "3", None, True, 2.5])
def test_a_nonsense_attempt_becomes_zero(executor, tmp_path, bad):
    """`True` matters: isinstance(True, int) is True, so a bool would pass as 1."""
    result = executor.send_card(tmp_path, {"id": "a1",
                                           "action_type": "telegram_send",
                                           "attempt": bad})
    assert result["attempt"] == 0


def test_the_attempt_is_derived_before_the_first_branch_that_reports_it():
    code = _code("action-queue-execute.py")
    start = code.index("def send_card(")
    body = code[start:]
    derive = body.index('attempt = card.get("attempt")')
    telegram = body.index('"telegram executor not implemented (501)"')
    assert derive < telegram, (
        "naming attempt in a branch above its own derivation is a NameError"
    )


# ============================================================
# The refuted item, pinned
# ============================================================

def test_a_failed_send_is_still_an_active_card():
    """The audit suspected `cmd_retry` was dead code. It is not.

    `approve_and_send` guards on the card being active, so a retry works only
    while `send_failed` counts as active. Pinned here because the audit could
    not see this file and the question will be asked again.
    """
    assert "send_failed" in ACTIVE_STATUSES
