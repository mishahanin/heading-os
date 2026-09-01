"""A cookie export that invented its attributes, and two resolvers that overread.

`chromium_cookies._merge_playwright` stamped `{"path": "/", "secure": True,
"httpOnly": False, "sameSite": "Lax"}` onto every exported cookie, and the
SELECT never read the columns holding the truth. Two of the four constants are
wrong in a direction that matters: a real HttpOnly session token exported as
`httpOnly: false` becomes readable by page JavaScript inside the automated
context, and a cookie the browser set without Secure, exported as `secure: true`,
is never sent over `http://` so the imported session silently does not
authenticate behind a green success line.

`claude_models.load_all(refresh=True)` promises "one fetch, not one per family"
and made TWO on a degraded API, because it never touched `_FETCH_FAILED` and the
comprehension below then let `latest()` fire its own.

`claude_models._api_key` tested `if key:` on the environment variable, so a
whitespace-only value was truthy, returned None, and the `.env` fallback never
ran even with a valid key in the file.

`colors.supports_ansi` argues in its docstring that the environment is the
honest signal, then read the environment only under `os.name == "nt"` - so
`TERM=dumb` was honoured on the platform where it is never set and ignored on
the one where it occurs.

The cookie tests build a synthetic Chromium DB under `tmp_path`. Nothing reads
the operator's real browser profile, and nothing reaches the network.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.utils.claude_models as claude_models
import scripts.utils.chromium_cookies as chromium_cookies
from scripts.utils.colors import supports_ansi

# Chromium's `samesite` enum, and what Playwright calls each value.
UNSPECIFIED, NO_RESTRICTION, LAX, STRICT = -1, 0, 1, 2


# --------------------------------------------------------------------------
# chromium_cookies
# --------------------------------------------------------------------------

def _build_profile(tmp_path: Path, rows: list[dict]) -> Path:
    """A user-data tree holding one profile and a synthetic Cookies DB."""
    user_data = tmp_path / "user-data"
    profile = user_data / "Default"
    profile.mkdir(parents=True)
    conn = sqlite3.connect(profile / "Cookies")
    conn.execute(
        "CREATE TABLE cookies("
        " host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB,"
        " expires_utc INTEGER, path TEXT, is_secure INTEGER,"
        " is_httponly INTEGER, samesite INTEGER)"
    )
    conn.execute("CREATE TABLE meta(key TEXT, value TEXT)")
    conn.execute("INSERT INTO meta VALUES('version', '24')")
    for row in rows:
        conn.execute(
            "INSERT INTO cookies VALUES(?,?,?,?,?,?,?,?,?)",
            (row["host_key"], row["name"], row["value"], b"", 0,
             row.get("path", "/"), row.get("is_secure", 0),
             row.get("is_httponly", 0), row.get("samesite", UNSPECIFIED)),
        )
    conn.commit()
    conn.close()
    return user_data


@pytest.fixture
def reader(tmp_path, monkeypatch):
    """Point `_read_cookies` at a synthetic profile with no key acquisition."""
    def build(rows):
        user_data = _build_profile(tmp_path, rows)
        monkeypatch.setattr(chromium_cookies, "_resolve_user_data", lambda b: user_data)
        monkeypatch.setattr(chromium_cookies, "find_profile_folder", lambda u, n: "Default")
        monkeypatch.setattr(chromium_cookies, "_get_keys", lambda b, u: {})
        return user_data

    return build


def test_an_httponly_token_is_exported_as_httponly(reader, tmp_path):
    """The defect, on the row that matters most: a real session token."""
    reader([{"host_key": "example.invalid", "name": "SID", "value": "token",
             "is_httponly": 1, "is_secure": 1, "samesite": LAX}])

    payload = chromium_cookies._merge_playwright(
        tmp_path / "store.json", "example.invalid",
        chromium_cookies._read_cookies("example.invalid")[0],
    )

    assert len(payload) == 1
    assert payload[0]["httpOnly"] is True
    assert payload[0]["secure"] is True


def test_a_non_secure_cookie_is_not_exported_as_secure(reader, tmp_path):
    """The other direction: a stamped `secure: true` silently kills the session."""
    reader([{"host_key": "example.invalid", "name": "SID", "value": "token",
             "is_secure": 0, "is_httponly": 0}])

    payload = chromium_cookies._merge_playwright(
        tmp_path / "store.json", "example.invalid",
        chromium_cookies._read_cookies("example.invalid")[0],
    )

    assert payload[0]["secure"] is False


def test_a_path_scoped_cookie_keeps_its_path(reader, tmp_path):
    """Stamping `/` widens the cookie the same way stamping the domain did."""
    reader([{"host_key": "example.invalid", "name": "ADMIN", "value": "v",
             "path": "/admin"}])

    payload = chromium_cookies._merge_playwright(
        tmp_path / "store.json", "example.invalid",
        chromium_cookies._read_cookies("example.invalid")[0],
    )

    assert payload[0]["path"] == "/admin"


@pytest.mark.parametrize(
    "stored,expected",
    [(UNSPECIFIED, "Lax"), (NO_RESTRICTION, "None"), (LAX, "Lax"),
     (STRICT, "Strict"), (99, "Lax")],
    ids=["unspecified", "none", "lax", "strict", "unknown-degrades"],
)
def test_the_samesite_enum_maps_to_playwrights_spelling(reader, tmp_path,
                                                        stored, expected):
    reader([{"host_key": "example.invalid", "name": "SID", "value": "v",
             "samesite": stored}])

    payload = chromium_cookies._merge_playwright(
        tmp_path / "store.json", "example.invalid",
        chromium_cookies._read_cookies("example.invalid")[0],
    )

    assert payload[0]["sameSite"] == expected


def test_two_rows_carry_their_own_flags_rather_than_one_shared_stamp(reader, tmp_path):
    """A single constant would make these two agree; the DB says they differ."""
    reader([
        {"host_key": "example.invalid", "name": "SID", "value": "a",
         "is_httponly": 1, "is_secure": 1, "samesite": STRICT, "path": "/"},
        {"host_key": "example.invalid", "name": "PREF", "value": "b",
         "is_httponly": 0, "is_secure": 0, "samesite": NO_RESTRICTION,
         "path": "/prefs"},
    ])

    payload = chromium_cookies._merge_playwright(
        tmp_path / "store.json", "example.invalid",
        chromium_cookies._read_cookies("example.invalid")[0],
    )
    by_name = {c["name"]: c for c in payload}

    assert by_name["SID"]["httpOnly"] is True
    assert by_name["PREF"]["httpOnly"] is False
    assert by_name["SID"]["sameSite"] == "Strict"
    assert by_name["PREF"]["sameSite"] == "None"
    assert by_name["PREF"]["path"] == "/prefs"


@pytest.mark.parametrize("stored", [None, ""], ids=["null-column", "empty"])
def test_a_row_with_no_path_falls_back_to_root_not_to_the_word_None(
        reader, tmp_path, stored):
    """`str(path) if path else "/"`, whose fallback half had no witness.

    Every other row in this file supplies a path. MEASURED 2026-09-01: dropping
    the `if path else "/"` and writing `str(path)` left this file green at 24
    passed, and a NULL `path` column then exported as the four-character string
    `"None"`. Playwright would scope that cookie to a path no page is ever
    served from, so the imported session silently does not authenticate -
    exactly the failure mode the stamped `secure: true` produced, arriving
    through the one attribute that was NOT stamped.

    A NULL path column is not hypothetical: the schema does not declare `path`
    NOT NULL, and a row written by an older Chromium can carry one.
    """
    reader([{"host_key": "example.invalid", "name": "SID", "value": "v",
             "path": stored}])

    payload = chromium_cookies._merge_playwright(
        tmp_path / "store.json", "example.invalid",
        chromium_cookies._read_cookies("example.invalid")[0],
    )

    assert payload[0]["path"] == "/", payload[0]


@pytest.mark.parametrize(
    "raw",
    [b"\xff\xfe[]", b'[{"name": "caf\xe9"}]', b"\x80\x81\x82"],
    ids=["bom-like", "latin1-payload", "binary"],
)
def test_an_undecodable_store_is_treated_as_empty_not_as_a_crash(
        reader, tmp_path, raw):
    """The recovery `_merge_playwright`'s own docstring describes, unmeasured.

    That docstring says the previous catch "named `json.JSONDecodeError` and
    `OSError`, which miss the `UnicodeDecodeError` a store holding non-UTF-8
    bytes raises, so the documented recovery crashed on one of the two ways a
    file is unparseable", and that `ValueError` now covers both because each is
    a subclass of it. MEASURED 2026-09-01: narrowing that `except` back to
    `(OSError, json.JSONDecodeError)` left this file green at 24 passed - the
    fix was written, explained at length, and guarded by nothing.

    `UnicodeDecodeError` is a SIBLING of `json.JSONDecodeError`, not a subclass,
    so the clause covering the parse never covered the decode above it. The
    caller re-runs this precisely because the store is unusable, which is when
    the crash lands.
    """
    reader([{"host_key": "example.invalid", "name": "SID", "value": "v"}])
    store = tmp_path / "store.json"
    store.write_bytes(raw)

    payload = chromium_cookies._merge_playwright(
        store, "example.invalid",
        chromium_cookies._read_cookies("example.invalid")[0],
    )

    assert [c["name"] for c in payload] == ["SID"], (
        "an unreadable store was not recovered from as if empty")


@pytest.mark.parametrize("text", ['{"cookies": []}', '"a string"', "7", "null"],
                         ids=["object", "string", "number", "null"])
def test_a_store_that_decodes_to_the_wrong_shape_is_also_treated_as_empty(
        reader, tmp_path, text):
    """The third way a store is unusable: it parses, and is not a list.

    `isinstance(loaded, list)` is the guard, and it had no witness - measured
    2026-09-01, `existing = loaded` unconditionally left this file green at 30
    passed. The rows above cover unreadable bytes and the row below covers a
    good store; a store that is valid JSON of the wrong shape is neither, and it
    is the shape a half-finished hand edit leaves behind.
    """
    reader([{"host_key": "example.invalid", "name": "SID", "value": "v"}])
    store = tmp_path / "store.json"
    store.write_text(text, encoding="utf-8")

    payload = chromium_cookies._merge_playwright(
        store, "example.invalid",
        chromium_cookies._read_cookies("example.invalid")[0],
    )

    assert [c["name"] for c in payload] == ["SID"]
    assert all(isinstance(c, dict) for c in payload), payload


def test_a_readable_store_still_keeps_another_domains_cookies(reader, tmp_path):
    """Anchor: treating EVERY store as empty would pass the rows above and
    silently drop every other domain's session on each import."""
    reader([{"host_key": "example.invalid", "name": "SID", "value": "v"}])
    store = tmp_path / "store.json"
    store.write_text(
        '[{"name": "OTHER", "value": "keep", "domain": "other.invalid", '
        '"path": "/"}]', encoding="utf-8")

    payload = chromium_cookies._merge_playwright(
        store, "example.invalid",
        chromium_cookies._read_cookies("example.invalid")[0],
    )

    assert {c["name"] for c in payload} == {"SID", "OTHER"}


def test_the_flat_get_cookies_shape_is_unchanged(reader):
    """The public contract is {name: value}; widening the tuple must not leak."""
    reader([{"host_key": "example.invalid", "name": "SID", "value": "token",
             "is_httponly": 1}])

    assert chromium_cookies.get_cookies("example.invalid") == {"SID": "token"}


def test_host_only_scoping_is_still_preserved(reader, tmp_path):
    """The `domain` fix this function already carried, kept alongside the new ones."""
    reader([{"host_key": "accounts.example.invalid", "name": "SID", "value": "v"}])

    payload = chromium_cookies._merge_playwright(
        tmp_path / "store.json", "example.invalid",
        chromium_cookies._read_cookies("example.invalid")[0],
    )

    assert payload[0]["domain"] == "accounts.example.invalid"


# --------------------------------------------------------------------------
# claude_models
# --------------------------------------------------------------------------

@pytest.fixture
def models(monkeypatch):
    """Isolate the module's global state and count its API calls."""
    calls: list[int] = []
    monkeypatch.setattr(claude_models, "_cached", lambda allow_stale=False: {})
    monkeypatch.setattr(claude_models, "_read_json", lambda p: {})
    monkeypatch.setattr(claude_models, "_write_cache", lambda d: None)
    monkeypatch.setattr(claude_models, "_RESOLVED", {})
    monkeypatch.setattr(claude_models, "_FETCH_FAILED", False)

    def serve(result):
        def fetch():
            calls.append(1)
            return dict(result)

        monkeypatch.setattr(claude_models, "fetch_from_api", fetch)
        return calls

    return serve


def test_a_degraded_refresh_makes_exactly_one_fetch(models):
    """The defect: an unreachable API cost two full timeouts, not one."""
    calls = models({})

    claude_models.load_all(refresh=True)

    assert len(calls) == 1


def test_the_two_refresh_entry_points_agree_on_the_failure_flag(models):
    """`latest(refresh=True)` already made one; `load_all` must match it."""
    calls = models({})

    claude_models.latest("opus", refresh=True)
    latest_count = len(calls)
    calls.clear()
    claude_models._RESOLVED.clear()
    claude_models._FETCH_FAILED = False
    claude_models.load_all(refresh=True)

    assert len(calls) == latest_count == 1


def test_a_degraded_refresh_still_answers_from_the_baseline(models):
    """One fetch must not become zero answers."""
    models({})

    resolved = claude_models.load_all(refresh=True)

    assert set(resolved) == set(claude_models.FAMILIES)
    assert all(resolved[f] == claude_models.BASELINE[f] for f in claude_models.FAMILIES)


def test_a_healthy_refresh_still_fetches_once_and_uses_the_answer(models):
    calls = models({f: f"{f}-2026" for f in claude_models.FAMILIES})

    resolved = claude_models.load_all(refresh=True)

    assert len(calls) == 1
    assert resolved == {f: f"{f}-2026" for f in claude_models.FAMILIES}


@pytest.mark.parametrize("value", ["   ", "\t", "\n", " \t "])
def test_a_whitespace_env_key_falls_through_to_dotenv(monkeypatch, value):
    """The defect: a truthy-but-blank env var hid a valid key in `.env`."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", value)
    import scripts.utils.paths as paths_module
    monkeypatch.setattr(
        paths_module, "load_env",
        lambda: os.environ.__setitem__("ANTHROPIC_API_KEY", "sk-from-dotenv"),
    )

    assert claude_models._api_key() == "sk-from-dotenv"


def test_a_real_env_key_is_still_preferred(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  sk-from-env  ")
    import scripts.utils.paths as paths_module
    monkeypatch.setattr(
        paths_module, "load_env",
        lambda: pytest.fail("`.env` must not be read when the environment answers"),
    )

    assert claude_models._api_key() == "sk-from-env"


def test_no_key_anywhere_is_still_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import scripts.utils.paths as paths_module
    monkeypatch.setattr(paths_module, "load_env", lambda: None)

    assert claude_models._api_key() is None


# --------------------------------------------------------------------------
# colors
# --------------------------------------------------------------------------

def test_term_dumb_is_honoured_on_this_platform(monkeypatch):
    """The defect: read only under `os.name == "nt"`, ignored where it occurs."""
    monkeypatch.setenv("TERM", "dumb")
    assert supports_ansi() is False


def test_the_tui_terminal_still_gets_colour(monkeypatch):
    """The unconditional POSIX branch is deliberate and must survive."""
    monkeypatch.setenv("TERM", "xterm-256color")
    assert supports_ansi() is True


def test_an_unset_term_still_gets_colour_on_posix(monkeypatch):
    """A hook writing to a pipe has no TERM, and colour does render there."""
    monkeypatch.delenv("TERM", raising=False)
    if os.name == "nt":
        pytest.skip("the POSIX branch is what this pins")
    assert supports_ansi() is True
