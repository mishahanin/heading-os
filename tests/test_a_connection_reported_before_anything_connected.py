#!/usr/bin/env python3
"""`exchange-task.py connect` printed "Connected as ..." before authenticating.

With `autodiscover=False`, exchangelib performs no network I/O while an
`Account` is constructed: the connection is made lazily, on the first folder
access. `connect()` printed a green `[OK] Connected as <address>` immediately
after that construction, so a wrong password, a wrong server or an unreachable
host still produced the line. The failure then surfaced further down, inside
`list_tasks` or `create_task`, where the traceback reads as a task-operations
bug rather than the login failure it is. The operator was told the one thing
that had not been tested.

The fix resolves `account.tasks` before the claim, which is the folder every
caller in that file uses anyway, and exits 1 naming the address and the server
when it fails.

Nothing here reaches Exchange or the network. `connect()` is driven with the
module's exchangelib names replaced by recorders, which is also what makes the
laziness visible: the fake `Account` raises on attribute access exactly the way
a real one does when the credentials are wrong.

Run: .venv/bin/python -m pytest \
     tests/test_a_connection_reported_before_anything_connected.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SCRIPT = ROOT / "scripts" / "exchange-task.py"


@pytest.fixture(scope="module")
def task():
    spec = importlib.util.spec_from_file_location("exchange_task_connect", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CONFIG = {
    "EXCHANGE_EMAIL": "someone@example.invalid",
    "EXCHANGE_USERNAME": "someone",
    "EXCHANGE_PASSWORD": "not-a-real-password",  # pragma: allowlist secret
    "EXCHANGE_SERVER": "mail.example.invalid",
}


class _LazyAccount:
    """An account whose folder access is where the connection actually happens.

    `raises` is what a real one does with bad credentials or an unreachable
    host: construction succeeds, the first folder resolution fails.
    """

    def __init__(self, *, raises=None, **kwargs):
        self._raises = raises
        self.kwargs = kwargs
        self.touched = False

    @property
    def tasks(self):
        self.touched = True
        if self._raises is not None:
            raise self._raises
        return object()


def _arm(mod, monkeypatch, *, raises=None):
    """Replace every exchangelib name `connect` reaches, and the lazy import."""
    made = {}

    def account(**kwargs):
        made["account"] = _LazyAccount(raises=raises, **kwargs)
        return made["account"]

    monkeypatch.setattr(mod, "_ensure_exchangelib", lambda: None)
    monkeypatch.setattr(mod, "Credentials", lambda **kw: kw, raising=False)
    monkeypatch.setattr(mod, "Configuration", lambda **kw: kw, raising=False)
    monkeypatch.setattr(mod, "DELEGATE", "delegate", raising=False)
    monkeypatch.setattr(mod, "Account", account, raising=False)
    return made


def test_a_login_that_fails_is_not_announced_as_a_connection(task, monkeypatch, capsys):
    """The measured defect. The old code printed the green line here."""
    _arm(task, monkeypatch, raises=RuntimeError("401 Unauthorized"))

    with pytest.raises(SystemExit) as excinfo:
        task.connect(dict(CONFIG))

    assert excinfo.value.code == 1
    cap = capsys.readouterr()
    assert "Connected as" not in cap.out, (
        f"a failed login was announced as a connection: {cap.out!r}")
    assert "Could not connect" in cap.err, cap.err
    # Both halves of what the operator has to check are named.
    assert CONFIG["EXCHANGE_EMAIL"] in cap.err
    assert CONFIG["EXCHANGE_SERVER"] in cap.err
    assert "401 Unauthorized" in cap.err, (
        "the server's own reason was dropped, which is the part that says "
        "whether it is the password or the host")


def test_the_connection_is_actually_attempted_before_the_claim(task, monkeypatch, capsys):
    """The laziness itself, pinned. Constructing the Account is not a connection,
    so a version of this that only wrapped the constructor in a try would pass
    the failure test above while proving nothing."""
    made = _arm(task, monkeypatch)

    task.connect(dict(CONFIG))

    assert made["account"].touched, (
        "connect() returned without resolving a folder, so nothing it printed "
        "was measured: with autodiscover=False the constructor does no I/O")
    assert "Connected as" in capsys.readouterr().out


def test_a_working_login_still_returns_the_account(task, monkeypatch):
    """The anchor. A connect() that always exited 1 would satisfy the first
    test and break every command in the file."""
    _arm(task, monkeypatch)

    account = task.connect(dict(CONFIG))

    assert account is not None
    assert account.tasks is not None


def test_the_constructor_still_gets_the_arguments_it_needs(task, monkeypatch):
    """The probe must not have changed how the account is built. `autodiscover`
    staying False is the whole reason the lazy resolution exists."""
    made = _arm(task, monkeypatch)
    task.connect(dict(CONFIG))

    kwargs = made["account"].kwargs
    assert kwargs["autodiscover"] is False, (
        "autodiscover was turned on to make the constructor connect. That is a "
        "different fix with a different cost, and it would leave this file's "
        "explanation of why the probe exists describing code that is gone")
    assert kwargs["primary_smtp_address"] == CONFIG["EXCHANGE_EMAIL"]
    assert kwargs["access_type"] == "delegate"
