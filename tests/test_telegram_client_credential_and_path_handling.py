"""Security contract for the Telegram skill's client script.

The script authenticates a REAL Telegram account, so its session directory is a
full account takeover if it leaks and its outbound `send-file` reaches third
parties. Four defects were found on 2026-08-31 and are pinned here:

* F2 -- the 2FA password arrived as an argv value, and the script printed an
  instruction teaching the operator to pass it that way. An argv value lands in
  the process table, the shell history, and the session transcript.
* F3 -- the session directory was created at the umask default, so Telethon's
  sqlite auth key sat in a world-traversable directory.
* F4 -- `.code_hash` was written with a plain truncating `open(..., 'w')` at the
  umask default, instead of the workspace's tmp-then-`os.replace` convention.
* F6 -- `send-file` resolved a relative path against the ENGINE clone while
  `download` resolved against the DATA overlay, so one file gave two different
  answers about where the workspace is.

Nothing here reaches the network or the operator's real session: every test
runs against `tmp_path` with a fake client, `HEADING_OS_DATA` is pinned to a
sandbox, and `socket.socket.connect` is blocked for the whole module.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import socket
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".claude" / "skills" / "telegram" / "scripts" / "telegram_client.py"


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """A test that reaches the network is itself a finding."""

    def _refuse(self, address):  # pragma: no cover - only runs on a violation
        raise AssertionError(f"test attempted a network connection to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)


@pytest.fixture(scope="module")
def tg():
    """Import the skill script without letting it load the operator's .env.

    The module calls ``load_env(WORKSPACE_ROOT)`` at import time, which would
    pull the real TELEGRAM_* credentials into this process. The from-import
    reads the attribute off ``scripts.utils.workspace`` at exec time, so
    neutering it there before exec keeps the real values out of the test run.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import scripts.utils.workspace as ws

    real_load_env = ws.load_env
    ws.load_env = lambda *a, **k: None
    try:
        spec = importlib.util.spec_from_file_location("telegram_client_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        ws.load_env = real_load_env
    return module


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch, tg):
    """Pin every path the script can touch inside tmp_path."""
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(data_root))
    session_dir = tmp_path / "sessions" / "telegram"
    monkeypatch.setattr(tg, "SESSION_DIR", str(session_dir))
    monkeypatch.setattr(tg, "SESSION_PATH", str(session_dir / "telegram"))
    monkeypatch.setenv("TELEGRAM_API_ID", "10000001")
    monkeypatch.setenv("TELEGRAM_API_HASH", "0" * 32)
    monkeypatch.setenv("TELEGRAM_PHONE", "+10000000000")
    monkeypatch.delenv("TELEGRAM_2FA_PASSWORD", raising=False)
    return SimpleNamespace(data_root=data_root, session_dir=session_dir)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


class FakeEntity:
    """Stands in for a Telethon User. Invented placeholder, no real account."""

    id = 999000111
    first_name = "Pat"
    last_name = "Placeholder"
    username = "placeholder_user"
    phone = None
    bot = False


class FakeClient:
    """Records what it was asked to do. Never touches a socket."""

    def __init__(self, *, code_hash="fakehash-0000", sign_in_2fa=False):
        self._code_hash = code_hash
        self._sign_in_2fa = sign_in_2fa
        self.sign_in_calls: list[dict] = []
        self.sent_files: list[str] = []

    async def send_code_request(self, phone):
        return SimpleNamespace(phone_code_hash=self._code_hash)

    async def sign_in(self, *args, **kwargs):
        self.sign_in_calls.append(kwargs)
        if self._sign_in_2fa and "password" not in kwargs:
            from telethon.errors import SessionPasswordNeededError

            raise SessionPasswordNeededError(request=None)
        return FakeEntity()

    async def get_me(self):
        return FakeEntity()

    async def get_entity(self, identifier):
        return FakeEntity()

    async def send_file(self, entity, path, caption=""):
        self.sent_files.append(path)
        return SimpleNamespace(id=4242)


def _args(**kw):
    base = {"json": False}
    base.update(kw)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------
# F2 -- the 2FA password must never travel as an argv value
# --------------------------------------------------------------------------

def test_verify_refuses_a_2fa_password_passed_on_the_command_line(tg, capsys):
    """A password in argv is already exposed. Refuse loudly; never use it."""
    with pytest.raises(SystemExit) as exc:
        # client is None on purpose: the refusal must land before anything
        # touches a client, so any use of it would raise AttributeError.
        asyncio.run(tg.cmd_verify(None, _args(code="00000", password="s3cret-placeholder")))  # pragma: allowlist secret
    assert exc.value.code == 2, "refusal must exit 2, distinct from the missing-credentials exit 1"
    err = capsys.readouterr().err
    assert "process table" in err
    assert "TELEGRAM_2FA_PASSWORD" in err
    assert "s3cret-placeholder" not in err, "the refusal must not echo the exposed value"


def test_verify_still_parses_the_password_flag_so_the_operator_sees_the_refusal(tg):
    """Deleting the flag outright would give an argparse error, not guidance."""
    parsed = tg.build_parser().parse_args(["verify", "00000", "--password", "x"])
    assert parsed.password == "x"  # noqa: S105  # pragma: allowlist secret


def test_the_two_step_guidance_never_teaches_the_command_line_form(tg, capsys):
    """The printed instruction is half the defect. It must not recommend argv."""
    client = FakeClient(sign_in_2fa=True)
    session_dir = Path(tg.SESSION_DIR)
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / ".code_hash").write_text("fakehash-0000")

    with pytest.raises(SystemExit) as exc:
        asyncio.run(tg.cmd_verify(client, _args(code="00000", password=None)))
    assert exc.value.code == 1
    out = capsys.readouterr()
    combined = out.out + out.err
    assert "--password" not in combined, "the script must not teach the unsafe form"
    assert "TELEGRAM_2FA_PASSWORD" in combined


def test_the_2fa_password_is_read_in_process_from_the_environment(tg, monkeypatch):
    """The safe channel: the gitignored .env, read in-process, never via argv."""
    monkeypatch.setenv("TELEGRAM_2FA_PASSWORD", "env-placeholder-pw")
    client = FakeClient(sign_in_2fa=True)
    session_dir = Path(tg.SESSION_DIR)
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / ".code_hash").write_text("fakehash-0000")

    asyncio.run(tg.cmd_verify(client, _args(code="00000", password=None)))

    assert client.sign_in_calls[-1].get("password") == "env-placeholder-pw"
    assert not (session_dir / ".code_hash").exists(), "the code hash is consumed on success"


# --------------------------------------------------------------------------
# F3 -- the session directory holds the account's auth key
# --------------------------------------------------------------------------

def test_a_new_session_directory_is_owner_only(tg):
    path = Path(tg.SESSION_DIR)
    assert not path.exists()
    tg.ensure_session_dir()
    assert _mode(path) == 0o700


def test_an_existing_session_directory_is_tightened(tg):
    """makedirs(mode=...) does NOT change a directory that already exists."""
    path = Path(tg.SESSION_DIR)
    path.mkdir(parents=True)
    os.chmod(path, 0o777)  # noqa: S103  # the loose mode IS the precondition
    assert _mode(path) == 0o777

    tg.ensure_session_dir()

    assert _mode(path) == 0o700


def test_a_refused_chmod_warns_instead_of_crashing(tg, monkeypatch, capsys):
    """Some filesystems refuse chmod. Degrade with a logged warning."""
    path = Path(tg.SESSION_DIR)
    path.mkdir(parents=True)

    real_chmod = os.chmod

    def _refuse(target, mode, *a, **k):
        if str(target) == str(path):
            raise PermissionError("chmod not supported on this filesystem")
        return real_chmod(target, mode, *a, **k)

    monkeypatch.setattr(os, "chmod", _refuse)

    tg.ensure_session_dir()  # must not raise

    assert "0700" in capsys.readouterr().err


def test_create_client_goes_through_the_hardened_directory_helper(tg, monkeypatch):
    """The helper is worthless if create_client still calls bare makedirs."""
    calls = []
    monkeypatch.setattr(tg, "ensure_session_dir", lambda *a, **k: calls.append(1))

    class _StubTelegramClient:
        def __init__(self, *a, **k):
            self.session = SimpleNamespace(_cursor=lambda: None, _conn=None)

    import telethon

    monkeypatch.setattr(telethon, "TelegramClient", _StubTelegramClient)
    tg.create_client()

    assert calls == [1]


# --------------------------------------------------------------------------
# F4 -- .code_hash is a credential-adjacent file
# --------------------------------------------------------------------------

def test_the_code_hash_is_written_owner_only(tg):
    client = FakeClient(code_hash="fakehash-1234")
    asyncio.run(tg.cmd_setup(client, _args()))

    hash_file = Path(tg.SESSION_DIR) / ".code_hash"
    assert hash_file.read_text() == "fakehash-1234"
    assert _mode(hash_file) == 0o600


def test_a_failed_code_hash_write_leaves_the_previous_value_intact(tg, monkeypatch):
    """tmp-then-replace, proven behaviourally: a plain open() would truncate."""
    import scripts.utils.atomic as atomic

    session_dir = Path(tg.SESSION_DIR)
    session_dir.mkdir(parents=True)
    hash_file = session_dir / ".code_hash"
    hash_file.write_text("previous-fakehash")

    monkeypatch.setattr(
        atomic, "os", _ReplaceRefuser(atomic.os), raising=True
    )

    client = FakeClient(code_hash="new-fakehash")
    with pytest.raises(OSError):
        asyncio.run(tg.cmd_setup(client, _args()))

    assert hash_file.read_text() == "previous-fakehash"
    leftovers = [p.name for p in session_dir.iterdir() if p.name != ".code_hash"]
    assert leftovers == [], f"tempfile orphans left behind: {leftovers}"


class _ReplaceRefuser:
    """Proxy over the `os` module whose replace() always fails."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        if name == "replace":
            def _boom(*a, **k):
                raise OSError("simulated crash between write and rename")

            return _boom
        return getattr(self._real, name)


# --------------------------------------------------------------------------
# F6 -- one file, one answer about where the workspace is
# --------------------------------------------------------------------------

def test_send_file_resolves_a_relative_path_under_the_data_root(tg, _sandbox):
    """The path shape every other skill produces must find its file."""
    target = _sandbox.data_root / "outputs" / "content" / "images" / "x.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"placeholder")

    # A same-named decoy in the engine clone must NOT win.
    client = FakeClient()
    asyncio.run(
        tg.cmd_send_file(
            client,
            _args(chat="@placeholder_user", path="outputs/content/images/x.png", caption=None),
        )
    )

    assert client.sent_files == [str(target)]


def test_send_file_refuses_a_relative_path_that_escapes_the_data_root(tg, capsys):
    client = FakeClient()
    with pytest.raises(SystemExit) as exc:
        asyncio.run(
            tg.cmd_send_file(
                client,
                _args(chat="@placeholder_user", path="../../.env", caption=None),
            )
        )
    assert exc.value.code == 1
    assert client.sent_files == [], "nothing may be sent after a refusal"
    assert "outside the data root" in capsys.readouterr().err


def test_send_file_still_accepts_an_explicit_absolute_path(tg, tmp_path):
    """Absolute paths stay the operator's explicit choice."""
    target = tmp_path / "elsewhere" / "deck.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"placeholder")

    client = FakeClient()
    asyncio.run(
        tg.cmd_send_file(
            client, _args(chat="@placeholder_user", path=str(target), caption=None)
        )
    )
    assert client.sent_files == [str(target)]


def test_send_file_and_download_agree_on_the_root(tg, _sandbox):
    """The two commands must not give different answers about the workspace."""
    from scripts.utils.workspace import get_outputs_dir

    download_default = Path(get_outputs_dir()) / "downloads"
    assert download_default.is_relative_to(_sandbox.data_root)
    assert Path(tg.resolve_send_path("outputs/x.png")).is_relative_to(_sandbox.data_root)


# --------------------------------------------------------------------------
# Lower item -- a swallowed exception
# --------------------------------------------------------------------------

def test_a_failed_sender_lookup_is_reported_not_swallowed(tg, capsys):
    class _Msg:
        sender = None
        sender_id = 999000222

        async def get_sender(self):
            raise RuntimeError("entity cache miss")

    name = asyncio.run(tg.get_sender_name(_Msg()))

    assert name == "User#999000222"
    assert "entity cache miss" in capsys.readouterr().err
