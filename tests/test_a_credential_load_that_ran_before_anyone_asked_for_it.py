"""Two provisioning paths that demanded a credential the path never used.

`gmail_auth.get_service` carries a comment listing "a revoked grant" among the
conditions its `except` turns into a re-authorisation. A revoked grant does not
surface there: `from_authorized_user_file` succeeds on it (the file is valid
JSON and carries a `refresh_token`), and the revocation appears only when
`creds.refresh(Request())` reaches Google, as
`google.auth.exceptions.RefreshError`. That call sat outside every handler, so
the operator of a revoked grant got the exact outcome the comment says was
fixed: a traceback instead of a re-authorisation, on a headless machine where
the traceback is the whole failure.

`healthchecks_setup.run_setup` called `load_env_key()` - which `sys.exit`s on a
missing `.env` or key - before `dry_run` was consulted. The dry path issues no
request and writes no file, so the preview was unavailable in the one situation
a preview exists for: a machine where provisioning has not happened yet.

Neither test reaches the network: the Gmail credentials object is a local fake
and the Healthchecks dry path is asserted precisely because it makes no call.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.utils.gmail_auth as gmail_auth
import scripts.utils.healthchecks_setup as healthchecks_setup


# --------------------------------------------------------------------------
# gmail_auth.get_service
# --------------------------------------------------------------------------

class _Creds:
    """A saved token that loads cleanly and fails only when it reaches Google."""

    def __init__(self, error):
        self.valid = False
        self.expired = True
        # noqa S105: a fixture label, not a credential. Nothing authenticates
        # with it - the fake `refresh` below raises or flips a flag.
        self.refresh_token = "saved-refresh-token"  # noqa: S105
        self._error = error
        self.refresh_calls = 0

    def refresh(self, request):
        self.refresh_calls += 1
        if self._error is not None:
            raise self._error
        self.valid = True

    def to_json(self):
        return '{"refresh_token": "saved-refresh-token"}'


@pytest.fixture
def gmail(tmp_path, monkeypatch):
    """Point the module at a scratch token and an ABSENT client-secrets file.

    The absent secrets file is the probe: reaching it proves the code fell
    through to the consent flow, without ever opening a browser.
    """
    token = tmp_path / "gmail_token.json"
    token.write_text("{}", encoding="utf-8")
    secrets = tmp_path / "credentials.json"

    monkeypatch.setattr(gmail_auth, "token_path", lambda: str(token))
    monkeypatch.setattr(gmail_auth, "creds_path", lambda: str(secrets))

    state = {}

    def install(creds):
        state["creds"] = creds
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            lambda *a, **k: creds,
        )
        return creds

    install.token = token
    install.secrets = secrets
    install.state = state
    return install


def test_a_revoked_grant_falls_through_to_re_authorisation(gmail, capsys):
    """The defect: RefreshError propagated straight out of `get_service`."""
    from google.auth.exceptions import RefreshError

    creds = gmail(_Creds(RefreshError("Token has been expired or revoked.")))

    with pytest.raises(FileNotFoundError) as caught:
        gmail_auth.get_service()

    # FileNotFoundError names the client-secrets file, which is only reached on
    # the consent branch: the refresh failure was handled, not propagated.
    assert "client secrets not found" in str(caught.value)
    assert creds.refresh_calls == 1
    assert "re-authorising" in capsys.readouterr().err


def test_the_refresh_failure_names_itself_rather_than_going_silent(gmail, capsys):
    """A fall-through that says nothing is a different defect from a crash."""
    from google.auth.exceptions import RefreshError

    gmail(_Creds(RefreshError("invalid_grant")))
    with pytest.raises(FileNotFoundError):
        gmail_auth.get_service()

    err = capsys.readouterr().err
    assert "RefreshError" in err
    assert "invalid_grant" in err


@pytest.mark.parametrize("error", [
    ValueError("Authorized user info was not in the expected format, "
               "missing fields refresh_token."),
    __import__("json").JSONDecodeError("Expecting value", "", 0),
])
def test_an_unusable_saved_token_falls_through_to_re_authorisation(gmail, capsys,
                                                                   monkeypatch,
                                                                   error):
    """The handler is `except (ValueError, json.JSONDecodeError)` and only the
    JSON half had a case.

    Its own comment names both: "the library raises JSONDecodeError on a
    truncated file and ValueError on valid JSON missing `refresh_token`".
    MEASURED 2026-09-01 by narrowing the handler to `json.JSONDecodeError`
    alone: the whole `tests/security/` tree plus this file stayed green, while a
    token file that is perfectly good JSON with a field missing raised out of
    `get_service` on a headless machine, which is the exact outcome that comment
    says was fixed.
    """
    def refuse(*_a, **_k):
        raise error

    gmail(_Creds(None))
    # Patched AFTER the fixture, so this is the loader `get_service` reaches.
    # monkeypatch, not a hand-rolled save-and-restore: an assignment left behind
    # by a failing assert would follow the whole pytest session.
    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.from_authorized_user_file", refuse)

    with pytest.raises(FileNotFoundError) as caught:
        gmail_auth.get_service()

    assert "client secrets not found" in str(caught.value)
    err = capsys.readouterr().err
    assert "is unusable" in err
    assert type(error).__name__ in err


def test_a_refresh_that_succeeds_is_not_thrown_away(gmail, tmp_path):
    """The guard must not push a healthy expired token into the consent flow."""
    creds = gmail(_Creds(None))
    written = {}
    import scripts.utils.gmail_auth as module

    def fake_build(service, version, credentials):
        written["credentials"] = credentials
        return "gmail-service"

    import googleapiclient.discovery

    original = googleapiclient.discovery.build
    googleapiclient.discovery.build = fake_build
    try:
        assert module.get_service() == "gmail-service"
    finally:
        googleapiclient.discovery.build = original

    assert creds.refresh_calls == 1
    assert written["credentials"] is creds
    # The refreshed token was persisted, not discarded.
    assert "saved-refresh-token" in gmail.token.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# healthchecks_setup.run_setup
# --------------------------------------------------------------------------

SPEC = {
    "env_key": "BOND_HC_SENTINEL",
    "name": "bond-sentinel",
    "tags": "bond critical",
    "desc": "Sentinel liveness",
    "grace": 1200,
    "timeout": 900,
}


def test_a_dry_run_previews_without_a_provisioned_env(tmp_path, monkeypatch, capsys):
    """The defect: `sys.exit` on a missing `.env` before `dry_run` was read."""
    monkeypatch.setattr(healthchecks_setup, "_ENV_FILE", tmp_path / "absent.env")

    healthchecks_setup.run_setup([SPEC], dry_run=True)

    out = capsys.readouterr().out
    assert "DRY: would upsert bond-sentinel" in out
    assert ".env not touched" in out


def test_a_dry_run_makes_no_request_and_writes_no_file(tmp_path, monkeypatch):
    """What makes skipping the key load correct: the path uses neither."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(healthchecks_setup, "_ENV_FILE", env_file)

    def refuse(*a, **k):
        raise AssertionError("the dry path must not reach the network")

    monkeypatch.setattr(healthchecks_setup.requests, "post", refuse)
    monkeypatch.setattr(
        healthchecks_setup, "write_env",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("the dry path must not write .env")
        ),
    )

    healthchecks_setup.run_setup([SPEC], dry_run=True)

    assert not env_file.exists()


def test_a_real_run_still_demands_the_key(tmp_path, monkeypatch):
    """The skip is scoped to `dry_run`; a live run must still refuse without it."""
    monkeypatch.setattr(healthchecks_setup, "_ENV_FILE", tmp_path / "absent.env")

    with pytest.raises(SystemExit) as caught:
        healthchecks_setup.run_setup([SPEC], dry_run=False)

    assert ".env not found" in str(caught.value)


def test_a_real_run_still_demands_the_key_when_env_exists_without_it(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SOMETHING_ELSE=1\n", encoding="utf-8")
    monkeypatch.setattr(healthchecks_setup, "_ENV_FILE", env_file)

    with pytest.raises(SystemExit) as caught:
        healthchecks_setup.run_setup([SPEC], dry_run=False)

    assert "HEALTHCHECKS_API_KEY not set" in str(caught.value)


# --------------------------------------------------------------------------
# The twin read that never got the first one's guard
# --------------------------------------------------------------------------

# Invalid as UTF-8 at byte 0. No credential is expressed here; the point is
# that the file cannot be decoded at all.
UNDECODABLE_ENV = b"\xff\xfeHEALTHCHECKS_API_KEY\x00\n"


def test_an_undecodable_env_is_refused_by_the_reader_with_a_reason(tmp_path,
                                                                   monkeypatch):
    """Anchor for the twin below: `load_env_key` already handles this shape, and
    its docstring says so."""
    env_file = tmp_path / ".env"
    env_file.write_bytes(UNDECODABLE_ENV)
    monkeypatch.setattr(healthchecks_setup, "_ENV_FILE", env_file)

    with pytest.raises(SystemExit) as caught:
        healthchecks_setup.load_env_key()

    assert "could not read" in str(caught.value)


def test_an_undecodable_env_is_refused_by_the_writer_too(tmp_path, monkeypatch):
    """The finding. `write_env` reads the SAME file twenty lines below
    `load_env_key` and had no guard at all.

    MEASURED 2026-09-01: `UnicodeDecodeError` out of `write_env` with no
    handler anywhere, which no AST sweep for a NARROW handler can see because
    there is no handler to be narrow. The refusal has to name the recovery,
    because by the time this runs the checks already exist on healthchecks.io
    and only the ping URLs are lost.
    """
    env_file = tmp_path / ".env"
    env_file.write_bytes(UNDECODABLE_ENV)
    monkeypatch.setattr(healthchecks_setup, "_ENV_FILE", env_file)

    with pytest.raises(SystemExit) as caught:
        healthchecks_setup.write_env({"BOND_HC_SENTINEL": "https://hc.test/abc"})

    message = str(caught.value)
    assert "could not read" in message
    assert "re-run" in message


def test_the_writer_still_writes_a_readable_env(tmp_path, monkeypatch):
    """The other jaw. A writer that refuses every file writes nothing at all,
    and the ping URLs are the whole product of this module."""
    env_file = tmp_path / ".env"
    env_file.write_text("SOMETHING_ELSE=1\n", encoding="utf-8")
    monkeypatch.setattr(healthchecks_setup, "_ENV_FILE", env_file)

    healthchecks_setup.write_env({"BOND_HC_SENTINEL": "https://hc.test/abc"})

    written = env_file.read_text(encoding="utf-8")
    assert "BOND_HC_SENTINEL=https://hc.test/abc" in written
    assert "SOMETHING_ELSE=1" in written
