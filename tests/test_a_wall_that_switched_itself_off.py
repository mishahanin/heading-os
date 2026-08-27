"""Shard scripts-utils-01-p2: a leak wall disabled by the very condition it
guards against, plus four smaller failures of the same family.

* ``git_push._is_split_engine`` exempted any repository whose data root resolved
  onto itself. That collapse happens when ``get_data_root()`` rule 2 finds
  ``crm/contacts/`` or ``knowledge/`` INSIDE the workspace - which is exactly the
  private data the engine wall exists to stop. Reproduced on 2026-08-25 against a
  scratch engine clone holding ``knowledge/note.md`` and ``outputs/deal.md``:
  ``_is_split_engine`` False, ``_roots_unreadable`` None, the wall skipped, and
  ``scan_engine_repo`` - never called - would have flagged all four artifacts.
  The engine repository is PUBLIC.

* ``embeddings.model_digest`` compared model FAMILIES, so ``bge-m3:567m`` on a
  host that also holds ``bge-m3:latest`` got ``:latest``'s digest. That digest is
  the only signal ``scripts/memory-index.py`` has for "the weights under this tag
  changed", so it was being read off a different model.

* ``gmail_auth.get_service`` let the credential parser raise. A truncated token
  file gives JSONDecodeError and one missing ``refresh_token`` gives ValueError;
  the only caller-side handler catches FileNotFoundError.

* ``firefox_cookies._snapshot_db`` stranded a copy of the cookie store in the
  system temp directory on any failure. The caller unlinks only on the return path.

* ``healthchecks_setup.write_env`` replaced ``.env`` with a fresh 0644 tempfile,
  widening the permissions of the file holding every credential this workspace
  loads, and left a full copy at ``.env.tmp`` if the write failed partway.

Run: python3 -m pytest tests/test_a_wall_that_switched_itself_off.py
"""
from __future__ import annotations

import http.server
import json
import socketserver
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import embeddings as em  # noqa: E402
from scripts.utils import firefox_cookies as fc  # noqa: E402
from scripts.utils import git_push as gp  # noqa: E402
from scripts.utils import healthchecks_setup as hs  # noqa: E402


# ============================================================
# The wall that switched itself off
# ============================================================

def _engine_tree(tmp_path: Path, *, marker: bool = True,
                 private: bool = True) -> Path:
    repo = tmp_path / "engine"
    (repo / ".claude").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("# engine\n", encoding="utf-8")
    if marker:
        (repo / "scripts" / "utils").mkdir(parents=True)
        (repo / "scripts" / "utils" / "engine_guard.py").write_text("x\n", encoding="utf-8")
    if private:
        (repo / "knowledge").mkdir()
        (repo / "knowledge" / "note.md").write_text("private\n", encoding="utf-8")
    return repo


def _pin_roots(monkeypatch, engine: Path, data: Path) -> None:
    monkeypatch.setattr(gp, "get_workspace_root", lambda: engine)
    monkeypatch.setattr(gp, "get_data_root", lambda: data)


def test_a_collapsed_data_root_no_longer_exempts_the_engine(tmp_path, monkeypatch):
    """The condition that collapses the data root IS private data in the engine."""
    repo = _engine_tree(tmp_path)
    _pin_roots(monkeypatch, repo, repo)
    assert gp._is_split_engine(repo) is True


def test_a_split_data_root_still_walls_the_engine(tmp_path, monkeypatch):
    repo = _engine_tree(tmp_path)
    _pin_roots(monkeypatch, repo, tmp_path / "data")
    assert gp._is_split_engine(repo) is True


def test_the_data_overlay_itself_is_still_exempt(tmp_path, monkeypatch):
    """It legitimately carries private content; walling it refuses every push."""
    repo = _engine_tree(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    _pin_roots(monkeypatch, repo, data)
    assert gp._is_split_engine(data) is False


def test_a_collapsed_root_without_the_engine_marker_is_exempt(tmp_path, monkeypatch):
    """The marker is what identifies the engine; nothing else is walled."""
    repo = _engine_tree(tmp_path, marker=False)
    _pin_roots(monkeypatch, repo, repo)
    assert gp._is_split_engine(repo) is False


def test_unresolvable_roots_still_answer_false_here(tmp_path, monkeypatch):
    """`_roots_unreadable` owns that case and REFUSES; this one must not guess."""
    def _boom():
        raise RuntimeError("no roots")
    monkeypatch.setattr(gp, "get_workspace_root", _boom)
    monkeypatch.setattr(gp, "get_data_root", _boom)
    assert gp._is_split_engine(_engine_tree(tmp_path)) is False


def test_the_wall_finds_the_artifacts_once_it_runs(tmp_path, monkeypatch):
    """End to end: the collapsed-root tree is scanned, and the scan is not empty."""
    from scripts.utils.engine_guard import scan_engine_repo
    repo = _engine_tree(tmp_path)
    (repo / "outputs").mkdir()
    (repo / "outputs" / "deal.md").write_text("private\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A", "-f"],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True, timeout=60)
    _pin_roots(monkeypatch, repo, repo)
    assert gp._is_split_engine(repo) is True
    assert "outputs/deal.md" in scan_engine_repo(repo)


def test_the_docstring_states_why_the_collapse_is_not_an_exemption():
    doc = " ".join(gp._is_split_engine.__doc__.split())
    assert "collapsed" in doc
    assert "no longer exempts" in doc


# ============================================================
# The digest read off a different model
# ============================================================

class _Tags(http.server.BaseHTTPRequestHandler):
    payload: dict = {}

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's name
        body = json.dumps(self.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def tags_host():
    def _serve(models: list[dict]) -> str:
        handler = type("H", (_Tags,), {"payload": {"models": models}})
        server = socketserver.TCPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    servers: list[socketserver.TCPServer] = []
    yield _serve
    for s in servers:
        s.shutdown()
        s.server_close()


_TWO_TAGS = [{"name": "bge-m3:latest", "digest": "AAAA"},
             {"name": "bge-m3:567m", "digest": "BBBB"}]


def test_a_specific_tag_gets_its_own_digest(tags_host):
    """It got `:latest`'s digest, under the `:567m` name."""
    host = tags_host(_TWO_TAGS)
    assert em.model_digest(model="bge-m3:567m", host=host) == "BBBB"


def test_a_bare_name_resolves_to_latest(tags_host):
    host = tags_host(_TWO_TAGS)
    assert em.model_digest(model="bge-m3", host=host) == "AAAA"
    assert em.model_digest(model="bge-m3:latest", host=host) == "AAAA"


def test_an_unknown_family_has_no_digest(tags_host):
    assert em.model_digest(model="nope", host=tags_host(_TWO_TAGS)) is None


def test_a_host_holding_only_one_tag_still_answers(tags_host):
    """A machine that pulled one specific tag must not lose provenance."""
    host = tags_host([{"name": "bge-m3:567m", "digest": "BBBB"}])
    assert em.model_digest(model="bge-m3", host=host) == "BBBB"


def test_two_candidates_and_no_exact_match_answer_nothing(tags_host):
    """An unproven digest beats a confidently wrong one."""
    host = tags_host([{"name": "bge-m3:567m", "digest": "BBBB"},
                      {"name": "bge-m3:q8", "digest": "CCCC"}])
    assert em.model_digest(model="bge-m3", host=host) is None


def test_an_entry_with_no_digest_is_not_an_empty_string(tags_host):
    host = tags_host([{"name": "bge-m3:latest"}])
    assert em.model_digest(model="bge-m3", host=host) is None


# ============================================================
# The token file that raised instead of re-authorising
# ============================================================

@pytest.mark.parametrize("body,why", [
    ('{"token": "abc', "truncated JSON"),
    ('{"token": "abc"}', "valid JSON with no refresh_token"),
])
def test_an_unusable_token_re_authorises_instead_of_raising(
        tmp_path, monkeypatch, capsys, body, why):
    """The only caller-side handler catches FileNotFoundError, so this escaped."""
    from scripts.utils import gmail_auth

    token = tmp_path / "token.json"
    token.write_text(body, encoding="utf-8")
    monkeypatch.setattr(gmail_auth, "token_path", lambda: str(token))
    monkeypatch.setattr(gmail_auth, "creds_path", lambda: str(tmp_path / "absent.json"))

    # Reaching the missing client-secrets file proves the credential parse was
    # survived; the consent flow is never started because the secrets are absent.
    with pytest.raises(FileNotFoundError):
        gmail_auth.get_service()
    err = capsys.readouterr().err
    assert "unusable" in err, why
    assert "re-authorising" in err


def test_a_usable_token_is_not_reported_as_broken(tmp_path, monkeypatch, capsys):
    from scripts.utils import gmail_auth

    token = tmp_path / "token.json"
    token.write_text(json.dumps({
        "token": "x", "refresh_token": "y",
        "client_id": "id", "client_secret": "secret",
        "scopes": list(gmail_auth.SCOPES),
    }), encoding="utf-8")
    monkeypatch.setattr(gmail_auth, "token_path", lambda: str(token))
    monkeypatch.setattr(gmail_auth, "creds_path", lambda: str(tmp_path / "absent.json"))

    # Until 2026-08-27 this test REACHED GOOGLE. The comment above the suppress
    # said "the refresh needs a network", and it took one: the token has no
    # expiry, so `creds.valid` is False and `get_service` POSTed this fabricated
    # refresh_token to https://oauth2.googleapis.com/token on every run of the
    # suite. Measured by replacing `socket.socket.connect` with a raiser and
    # reading the traceback. `contextlib.suppress(Exception)` then hid the
    # outcome, so a test whose subject is one stderr message quietly made the
    # whole suite depend on the internet and on a third party's endpoint.
    #
    # The refusal below is the network, refused. Reaching it at all still means
    # the decision under test - "a readable token is not called unusable" - was
    # taken correctly at the parse, several branches earlier.
    reached = []

    def _no_network(*args, **kwargs):
        reached.append(True)
        raise RuntimeError("refresh refused: this test does not use the network")

    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.refresh", _no_network, raising=True)

    # Subject first. Whatever `get_service` raises, the claim under test is the
    # stderr line, so it is asserted before the exception type. Ordering it the
    # other way sent a reader chasing a FileNotFoundError from the consent-flow
    # branch when the real regression was the parse three branches earlier.
    raised = None
    try:
        gmail_auth.get_service()
    except BaseException as exc:  # noqa: BLE001 - narrowed by the last assert
        raised = exc
    assert "unusable" not in capsys.readouterr().err
    assert reached, "the refresh was never attempted; this test lost its path"
    assert isinstance(raised, RuntimeError) and "refresh refused" in str(raised), (
        f"expected the refusal this test installs, got {raised!r}")


# ============================================================
# The cookie snapshot left in the system temp directory
# ============================================================

def test_a_failed_snapshot_leaves_no_copy_of_the_cookie_store(tmp_path):
    tmpdir = Path(tempfile.gettempdir())
    before = set(tmpdir.glob("ff_cookies_*.sqlite"))
    not_a_db = tmp_path / "cookies.sqlite"
    not_a_db.write_text("not a database", encoding="utf-8")

    with pytest.raises(Exception):  # noqa: B017 - sqlite3.DatabaseError, via any driver
        fc._snapshot_db(not_a_db)

    assert set(tmpdir.glob("ff_cookies_*.sqlite")) - before == set()


def test_a_good_snapshot_still_returns_a_file(tmp_path):
    import sqlite3
    src = tmp_path / "cookies.sqlite"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE moz_cookies (name TEXT)")
    conn.commit()
    conn.close()

    snap = fc._snapshot_db(src)
    try:
        assert snap.is_file()
    finally:
        snap.unlink(missing_ok=True)


# ============================================================
# The secrets file that lost its permissions
# ============================================================

def test_writing_one_variable_does_not_widen_the_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    env.chmod(0o600)
    monkeypatch.setattr(hs, "_ENV_FILE", env)

    hs.write_env({"B": "2"})

    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert env.read_text(encoding="utf-8") == "A=1\nB=2\n"


def test_an_existing_key_is_replaced_in_place(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("A=1\nB=old\n", encoding="utf-8")
    env.chmod(0o600)
    monkeypatch.setattr(hs, "_ENV_FILE", env)

    hs.write_env({"B": "new"})

    assert env.read_text(encoding="utf-8") == "A=1\nB=new\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_no_tmp_copy_of_the_secrets_is_left_behind(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    monkeypatch.setattr(hs, "_ENV_FILE", env)
    hs.write_env({"B": "2"})
    assert list(tmp_path.glob(".env.tmp")) == []


def test_a_missing_env_falls_back_to_owner_only(tmp_path, monkeypatch):
    """No mode to preserve means the restrictive one, never the umask's."""
    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    monkeypatch.setattr(hs, "_ENV_FILE", env)
    real_stat = Path.stat

    def _no_stat(self, *a, **k):
        if self == env:
            raise OSError("gone")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", _no_stat)
    hs.write_env({"B": "2"})
    monkeypatch.undo()
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
