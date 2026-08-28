"""Shard 41: one cookie reader that answered confidently with the wrong bytes.

Four claims this file makes, each measured before it was fixed.

- Chromium schema 24 puts a 32-byte SHA-256 of the host ahead of the cookie
  value. The reader never read the schema version and never stripped it, and
  `errors="replace"` turned those 32 binary bytes into replacement characters
  instead of an error, so an 18-character token came back as 48 characters and
  every layer above reported success. This machine's own Brave profile is
  `meta.version = 24` with 130 v10 cookies, so it was every cookie, every time.
- The subdomain leg of the query was a bare LIKE parameter. SQLite LIKE reads
  `%` and `_` as wildcards; nothing escaped either. Asking for `my_site.com`
  also returned `.myXsite.com`; asking for `%.com` returned every row. The
  domain comes straight off the operator's keyboard.
- Two rows sharing a name collapsed to whichever the table scan reached last.
  The same query over the same two rows returned `SID = REAL` or
  `SID = SUBDOMAIN` purely by insertion order.
- `--out` printed "mode 0600" while an existing store stayed 0644, and wrote in
  place with O_TRUNC rather than atomically.

The last two defects existed identically in `scripts/utils/firefox_cookies.py`,
which is the reader `scripts/linkedin-activity.py` actually runs. The correct
form of the domain-boundary rule already sat ninety lines below the broken one
in `chromium_cookies.py` itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import chromium_cookies as CC  # noqa: E402
from scripts.utils import firefox_cookies as FC  # noqa: E402
from scripts.utils.cookie_domains import (  # noqa: E402
    host_match_sql,
    host_rank,
    pick_per_name,
)


# ============================================================
# The match: a domain is data, never LIKE syntax
# ============================================================

def _host_table(hosts):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE cookies(host_key TEXT)")
    conn.executemany("INSERT INTO cookies VALUES (?)", [(h,) for h in hosts])
    return conn


def _matched(hosts, domain, include_subdomains=True):
    conn = _host_table(hosts)
    where, params = host_match_sql("host_key", domain, include_subdomains)
    # noqa S608: same shape as the two readers - the interpolated fragment is
    # the helper's own output, and the domain travels as a bound parameter.
    rows = conn.execute(f"SELECT host_key FROM cookies WHERE {where}", params)  # noqa: S608
    return sorted(r[0] for r in rows)


def test_an_underscore_in_the_domain_is_a_letter_not_a_wildcard():
    """Measured before the fix: `my_site.com` also returned `.myXsite.com`,
    because SQLite LIKE reads `_` as "any single character"."""
    hosts = [".my_site.com", ".myXsite.com", ".a.myXsite.com"]

    assert _matched(hosts, "my_site.com") == [".my_site.com"]


def test_a_percent_in_the_domain_does_not_return_the_whole_table():
    """Measured before the fix: `%.com` returned every row, including
    `.evil.com`. Under --store that writes a foreign host's live session token
    into the export as if it belonged to the domain that was asked for."""
    hosts = [".my_site.com", ".evil.com", ".bank.com"]

    assert _matched(hosts, "%.com") == []


def test_a_subdomain_match_carries_the_escaping_too():
    """The LIKE leg is the ONLY path here, so this fails if ESCAPE is dropped.

    The apex test above passes through the `host_key = '.' || domain` leg even
    with a broken pattern, which is how a first draft of this file let the
    dropped-ESCAPE mutation survive. A subdomain row can only be reached by the
    pattern, so it measures the pattern.
    """
    hosts = ["www.my_site.com", "www.myXsite.com"]

    assert _matched(hosts, "my_site.com") == ["www.my_site.com"]


def test_a_backslash_in_the_domain_does_not_break_the_escaping():
    """The escape character has to be escaped first, or escaping `%` and `_`
    re-escapes the backslashes just introduced.

    The domain carries BOTH a backslash and an underscore on purpose. With only
    a backslash the two orders produce the same pattern, so the case proved
    nothing; with both, escaping `_` first leaves the backslash doubled around
    it and the intended host stops matching at all.
    """
    hosts = [r"www.a\_b.com", r"www.a\xb.com", "www.axyb.com"]

    assert _matched(hosts, r"a\_b.com") == [r"www.a\_b.com"]


@pytest.mark.parametrize("host,wanted", [
    ("example.com", True),
    (".example.com", True),
    ("a.example.com", True),
    (".a.example.com", True),
    ("notexample.com", False),
    (".myexample.com", False),
    ("example.com.evil.net", False),
])
def test_the_dot_boundary_holds_in_the_query_the_way_it_holds_in_the_merge(host, wanted):
    """`_merge_playwright._is_this_domain` already enforced this boundary, with
    a docstring about the defect it fixed. The SQL copy ninety lines above it
    was never touched."""
    assert (_matched([host], "example.com") == [host]) is wanted


def test_exact_host_mode_matches_only_the_exact_host():
    hosts = ["example.com", ".example.com", "a.example.com"]

    assert _matched(hosts, "example.com", include_subdomains=False) == ["example.com"]


def test_the_column_name_may_not_be_operator_input():
    """`column` is interpolated into SQL, so it is checked. The domain never is:
    it is always a bound parameter."""
    with pytest.raises(ValueError):
        host_match_sql("host_key; DROP TABLE cookies", "example.com")


def test_an_empty_domain_is_refused():
    with pytest.raises(ValueError):
        host_match_sql("host_key", "")


def test_the_wildcard_guard_is_not_vacuous():
    """If the escaping were removed, these tests must fail. Reproduce the old
    unescaped query here and show it over-matches, so a future edit that drops
    ESCAPE cannot pass by making the corpus empty."""
    conn = _host_table([".my_site.com", ".myXsite.com"])
    rows = conn.execute(
        "SELECT host_key FROM cookies WHERE host_key LIKE ?", ("%.my_site.com",)
    ).fetchall()

    assert len(rows) == 2, "the old form must still over-match, or this file proves nothing"


# ============================================================
# The winner: deterministic, and the one the browser would send
# ============================================================

@pytest.mark.parametrize("reverse", [False, True])
def test_the_apex_cookie_beats_a_subdomain_whatever_the_row_order(reverse):
    """Measured before the fix: the same two rows returned REAL or SUBDOMAIN
    depending only on which was inserted first. A request to `example.com` would
    never carry `accounts.example.com`'s cookie at all."""
    rows = [(".example.com", "SID", "REAL"), ("accounts.example.com", "SID", "SUB")]
    if reverse:
        rows = list(reversed(rows))

    winners, dropped = pick_per_name(rows, "example.com")

    assert winners["SID"] == (".example.com", "REAL")
    assert dropped == [("SID", "accounts.example.com", ".example.com")]


def test_the_host_only_cookie_beats_the_domain_cookie():
    """Both would be sent; a flat map has to choose. The host-only row is the
    one scoped to exactly the host that was asked about."""
    rows = [(".example.com", "SID", "DOMAIN"), ("example.com", "SID", "HOSTONLY")]

    winners, _ = pick_per_name(rows, "example.com")

    assert winners["SID"][1] == "HOSTONLY"


def test_a_shallower_subdomain_beats_a_deeper_one():
    rows = [("a.b.example.com", "SID", "DEEP"), ("b.example.com", "SID", "SHALLOW")]

    winners, _ = pick_per_name(rows, "example.com")

    assert winners["SID"][1] == "SHALLOW"


def test_two_hosts_of_equal_depth_still_order_deterministically():
    """Not by table scan. The host string is the final element of the rank."""
    a = pick_per_name([("x.e.com", "S", "X"), ("y.e.com", "S", "Y")], "e.com")[0]
    b = pick_per_name([("y.e.com", "S", "Y"), ("x.e.com", "S", "X")], "e.com")[0]

    assert a["S"] == b["S"]


def test_every_loser_is_reported_not_hidden():
    """`scripts/linkedin-activity.py` asserts `li_at` exists and authenticates
    with it. Which of two it got was a coin flip, and nothing said so."""
    rows = [(".x.com", "li_at", "A"), ("www.x.com", "li_at", "B"),
            ("m.x.com", "li_at", "C")]

    _winners, dropped = pick_per_name(rows, "x.com")

    assert len(dropped) == 2
    assert {name for name, _l, _k in dropped} == {"li_at"}


def test_distinct_names_never_collide():
    rows = [(".x.com", "a", "1"), (".x.com", "b", "2")]

    winners, dropped = pick_per_name(rows, "x.com")

    assert {n: v for n, (_h, v) in winners.items()} == {"a": "1", "b": "2"}
    assert dropped == []


def test_host_rank_puts_the_apex_ahead_of_every_subdomain():
    """The property the ordering rests on, stated directly."""
    apex = host_rank("example.com", "example.com")
    dotted = host_rank(".example.com", "example.com")
    sub = host_rank("a.example.com", "example.com")

    assert apex < dotted < sub


# ============================================================
# The bytes: schema 24 puts a 32-byte host hash ahead of the value
# ============================================================

def _cbc_blob(value: bytes, key: bytes, host: bytes | None) -> bytes:
    """A v10 blob in Chromium's stored format. `host` set means schema >= 24."""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # content-guard: ok library class, not an org

    plain = (hashlib.sha256(host).digest() if host else b"") + value
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()  # content-guard: ok library class, not an org
    return b"v10" + enc.update(padded) + enc.finalize()


def test_the_host_hash_is_stripped_when_the_schema_says_it_is_there():
    key = CC._derive_pbkdf2(b"peanuts", iterations=1)
    blob = _cbc_blob(b"REAL-SESSION-VALUE", key, host=b"example.com")

    assert CC._decrypt_blob_aescbc(blob, key, hash_prefix=True) == "REAL-SESSION-VALUE"


def test_an_older_schema_still_reads_without_stripping():
    """Stripping unconditionally would break every pre-24 profile."""
    key = CC._derive_pbkdf2(b"peanuts", iterations=1)
    blob = _cbc_blob(b"OLD-VALUE", key, host=None)

    assert CC._decrypt_blob_aescbc(blob, key, hash_prefix=False) == "OLD-VALUE"


def test_undecodable_bytes_raise_instead_of_becoming_replacement_characters():
    """This is what hid the defect for as long as it existed. With
    `errors="replace"` the 32 binary bytes came back as a string, so nothing
    above could tell a broken value from a good one."""
    key = CC._derive_pbkdf2(b"peanuts", iterations=1)
    blob = _cbc_blob(b"REAL-SESSION-VALUE", key, host=b"example.com")

    with pytest.raises(ValueError, match="not a valid cookie value"):
        CC._decrypt_blob_aescbc(blob, key, hash_prefix=False)


@pytest.mark.parametrize("rows,wanted", [
    ([("version", "24")], 24),
    ([("version", "23")], 23),
    ([("mmap_status", "-1")], 0),
    ([("version", "not-a-number")], 0),
    ([], 0),
])
def test_the_schema_version_is_read_and_degrades_to_zero(rows, wanted):
    """Degrading to 0 reproduces the old behaviour on that one DB rather than
    corrupting a modern one, which is the safe direction."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE meta(key TEXT, value TEXT)")
    conn.executemany("INSERT INTO meta VALUES (?,?)", rows)

    assert CC._schema_version(conn) == wanted


def test_a_missing_meta_table_is_version_zero_not_a_crash():
    conn = sqlite3.connect(":memory:")

    assert CC._schema_version(conn) == 0


# ============================================================
# End to end over a synthetic profile
# ============================================================

def _chromium_profile(tmp_path: Path, cookie_rows, schema_version=24) -> Path:
    """A user-data tree the reader accepts: Local State plus Network/Cookies."""
    ud = tmp_path / "User Data"
    (ud / "Default" / "Network").mkdir(parents=True)
    (ud / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Default": {"name": "ClaudeCode"}}}})
    )
    db = ud / "Default" / "Network" / "Cookies"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE meta(key TEXT, value TEXT)")
    conn.execute("INSERT INTO meta VALUES ('version', ?)", (str(schema_version),))
    conn.execute(
        "CREATE TABLE cookies(host_key TEXT, name TEXT, value TEXT, "
        "encrypted_value BLOB, expires_utc INT)"
    )
    conn.executemany("INSERT INTO cookies VALUES (?,?,?,?,?)", cookie_rows)
    conn.commit()
    conn.close()
    return ud


@pytest.fixture
def fake_keys(monkeypatch):
    key = CC._derive_pbkdf2(b"peanuts", iterations=1)
    monkeypatch.setattr(CC, "_get_keys", lambda browser, user_data: {"v10": key, "v11": key})
    return key


def test_a_schema_24_profile_yields_the_real_token_not_the_hash(tmp_path, monkeypatch, fake_keys):
    """The headline. Before the fix this returned 48 characters where the token
    is 18, and the CLI printed a green success line over it."""
    blob = _cbc_blob(b"REAL-SESSION-VALUE", fake_keys, host=b".example.com")
    ud = _chromium_profile(tmp_path, [(".example.com", "SID", "", blob, 0)], 24)
    monkeypatch.setattr(CC, "_resolve_user_data", lambda browser: ud)

    cookies, failures = CC._read_cookies("example.com")

    assert failures == []
    assert cookies == {"SID": (".example.com", "REAL-SESSION-VALUE")}


def test_the_public_map_stays_flat_name_to_value(tmp_path, monkeypatch, fake_keys):
    """`get_cookies` is the documented API and its shape does not change."""
    blob = _cbc_blob(b"V", fake_keys, host=b".example.com")
    ud = _chromium_profile(tmp_path, [(".example.com", "SID", "", blob, 0)], 24)
    monkeypatch.setattr(CC, "_resolve_user_data", lambda browser: ud)

    assert CC.get_cookies("example.com") == {"SID": "V"}


def test_a_v20_cookie_lands_in_failures_rather_than_vanishing(tmp_path, monkeypatch, fake_keys):
    """It used to leave an empty dict that read exactly like an empty profile."""
    ud = _chromium_profile(tmp_path, [(".example.com", "SID", "", b"v20" + b"\x00" * 40, 0)], 24)
    monkeypatch.setattr(CC, "_resolve_user_data", lambda browser: ud)

    cookies, failures = CC._read_cookies("example.com")

    assert cookies == {}
    assert [(n, h) for n, h, _r in failures] == [("SID", ".example.com")]
    assert "v20" in failures[0][2]


def test_an_expired_cookie_is_still_dropped_and_a_session_cookie_kept(
        tmp_path, monkeypatch, fake_keys):
    ud = _chromium_profile(tmp_path, [
        (".example.com", "OLD", "stale", b"", 1),
        (".example.com", "LIVE", "fresh", b"", 0),
    ], 24)
    monkeypatch.setattr(CC, "_resolve_user_data", lambda browser: ud)

    cookies, _ = CC._read_cookies("example.com")

    assert set(cookies) == {"LIVE"}


def test_a_losing_cookie_is_never_decrypted(tmp_path, monkeypatch, fake_keys):
    """The collision is resolved before decryption, so a subdomain row that
    cannot be read does not become a failure the operator has to explain."""
    good = _cbc_blob(b"REAL", fake_keys, host=b".example.com")
    ud = _chromium_profile(tmp_path, [
        (".example.com", "SID", "", good, 0),
        ("accounts.example.com", "SID", "", b"v20" + b"\x00" * 40, 0),
    ], 24)
    monkeypatch.setattr(CC, "_resolve_user_data", lambda browser: ud)

    cookies, failures = CC._read_cookies("example.com")

    assert failures == [], "the losing v20 row must never be decrypted"
    assert cookies["SID"][1] == "REAL"


# ============================================================
# The CLI: what it writes, and what it says about it
# ============================================================

def _run(monkeypatch, capsys, argv, detailed, failures=()):
    monkeypatch.setattr(CC, "_read_cookies", lambda *a, **k: (detailed, list(failures)))
    monkeypatch.setattr(sys, "argv", ["chromium_cookies.py", *argv])
    return CC._main(), capsys.readouterr()


def test_an_existing_store_is_tightened_to_0600_and_the_line_says_what_is_true(tmp_path):
    """Measured before the fix: a store pre-created at 0644 stayed 0644 while
    the success line printed "mode 0600" over live session tokens. `os.open`'s
    mode argument applies only when O_CREAT actually creates."""
    out = tmp_path / "cookies.json"
    out.write_text("[]")
    os.chmod(out, 0o644)

    reported = CC._write_secret_json(out, [{"name": "a", "value": "b"}])

    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert reported == "0o600"


def test_the_reported_mode_is_measured_not_asserted(tmp_path):
    """If the file ends up as anything other than 0600, the line must say so
    rather than repeat the intention. `.claude/rules/scope-claims.md`.

    The mismatch is forced with a umask, which patches nothing: a umask can only
    REMOVE permission bits, so 0600 created under 0o200 lands as 0o400. An
    earlier version of this test monkeypatched `os.chmod` instead, and went
    vacuous the moment that call left the code - it then passed against a
    hardcoded "0o600" return.
    """
    out = tmp_path / "cookies.json"
    old = os.umask(0o200)
    try:
        reported = CC._write_secret_json(out, [])
    finally:
        os.umask(old)

    assert stat.S_IMODE(out.stat().st_mode) == 0o400, "the umask did not bite"
    assert reported == "0o400", "the reported mode was not read off the file"


def test_the_write_is_atomic_and_leaves_no_temp_behind(tmp_path, monkeypatch):
    """`~/.claude/CLAUDE.md` requires a temp plus `os.replace` for state files.
    An in-place O_TRUNC leaves a truncated store when the run is interrupted."""
    out = tmp_path / "cookies.json"
    out.write_text('[{"name": "old"}]')
    before = out.read_bytes()

    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(json, "dump", boom)
    with pytest.raises(KeyboardInterrupt):
        CC._write_secret_json(out, [{"name": "new"}])

    assert out.read_bytes() == before, "the old store was destroyed mid-write"
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_partial_decryption_failure_refuses_the_write_and_exits_non_zero(
        tmp_path, monkeypatch, capsys):
    """The empty-read guard covered TOTAL failure only. On a Chrome M127+
    profile, where v20 blobs sit beside older ones, partial is the normal case,
    and a partial store silently replaced a working session."""
    store = tmp_path / "cookies.json"
    store.write_text(json.dumps([{"name": "session", "domain": ".x.com"}]))
    before = store.read_bytes()

    code, out = _run(
        monkeypatch, capsys,
        ["x.com", "--out", str(store), "--playwright"],
        {"OK": (".x.com", "v")},
        failures=[("BROKEN", ".x.com", "App-bound v20 encrypted cookie detected")],
    )

    assert code == 4
    assert store.read_bytes() == before
    assert "could not be decrypted" in out.err
    assert "v20" in out.err


def test_a_total_decryption_failure_no_longer_reports_an_empty_profile(
        monkeypatch, capsys):
    """Before the fix this printed "No cookies found. Is the profile logged in?"
    on stdout and exited 0. The skill's own troubleshooting table copied that
    wrong cause. Measured end to end on a synthetic all-v20 profile."""
    code, out = _run(
        monkeypatch, capsys, ["x.com"], {},
        failures=[("SID", ".x.com", "App-bound v20 encrypted cookie detected")],
    )

    assert code == 4
    assert "Is the profile logged in?" not in out.out
    assert "could not be decrypted" in out.err


def test_the_cli_carries_exact_host_all_the_way_into_the_merge(
        tmp_path, monkeypatch, capsys):
    """The merge test below exercises `_merge_playwright` directly. This one
    runs the whole command, because the defect can also come back as a wrong
    argument at the one call site rather than as a wrong rule in the function.
    """
    store = tmp_path / "cookies.json"
    store.write_text(json.dumps([{"name": "sub", "domain": ".accounts.x.com"}]))

    code, _out = _run(
        monkeypatch, capsys,
        ["x.com", "--exact-host", "--out", str(store), "--playwright"],
        {"fresh": ("x.com", "v")},
    )

    assert code == 0
    assert {c["name"] for c in json.loads(store.read_text())} == {"sub", "fresh"}


def test_the_cli_without_exact_host_still_evicts_the_subdomains(
        tmp_path, monkeypatch, capsys):
    """The other direction, so the argument cannot be pinned to a constant."""
    store = tmp_path / "cookies.json"
    store.write_text(json.dumps([{"name": "sub", "domain": ".accounts.x.com"}]))

    code, _out = _run(
        monkeypatch, capsys,
        ["x.com", "--out", str(store), "--playwright"],
        {"fresh": ("x.com", "v")},
    )

    assert code == 0
    assert {c["name"] for c in json.loads(store.read_text())} == {"fresh"}


def test_a_clean_read_still_exits_zero(tmp_path, monkeypatch, capsys):
    """The failure gate must not fire when nothing failed."""
    out_file = tmp_path / "plain.json"

    code, _out = _run(monkeypatch, capsys,
                      ["x.com", "--out", str(out_file)], {"SID": (".x.com", "abc")})

    assert code == 0
    assert json.loads(out_file.read_text()) == {"SID": "abc"}


# ============================================================
# The export: the browser's own scoping survives the round trip
# ============================================================

def test_a_host_only_cookie_is_not_widened_to_every_subdomain(tmp_path):
    """Before the fix every entry was stamped `.{domain}`, so a token the
    browser scoped to `accounts.google.com` alone was offered by Playwright to
    `mail.google.com` too."""
    store = tmp_path / "cookies.json"

    merged = CC._merge_playwright(
        store, "google.com", {"SID": ("accounts.google.com", "v")}
    )

    assert [c["domain"] for c in merged] == ["accounts.google.com"]


def test_a_domain_cookie_keeps_its_leading_dot(tmp_path):
    store = tmp_path / "cookies.json"

    merged = CC._merge_playwright(store, "google.com", {"SID": (".google.com", "v")})

    assert [c["domain"] for c in merged] == [".google.com"]


def test_a_blank_host_falls_back_to_the_asked_for_domain(tmp_path):
    """The schema should never produce this, and an entry with no scope at all
    is worse than a conservative one."""
    store = tmp_path / "cookies.json"

    merged = CC._merge_playwright(store, "google.com", {"SID": ("", "v")})

    assert [c["domain"] for c in merged] == [".google.com"]


def test_exact_host_does_not_evict_the_subdomains_it_never_read(tmp_path):
    """`--exact-host` reads the exact host only, and the merge still evicted
    every stored subdomain entry. Silent session loss on an advertised flag."""
    store = tmp_path / "cookies.json"
    store.write_text(json.dumps([
        {"name": "sub", "domain": ".accounts.x.com"},
        {"name": "old", "domain": "x.com"},
    ]))

    merged = CC._merge_playwright(
        store, "x.com", {"fresh": ("x.com", "v")}, include_subdomains=False
    )

    assert {c["name"] for c in merged} == {"sub", "fresh"}


def test_with_subdomains_the_eviction_is_unchanged(tmp_path):
    """The narrowing applies only to the exact-host read."""
    store = tmp_path / "cookies.json"
    store.write_text(json.dumps([{"name": "sub", "domain": ".accounts.x.com"}]))

    merged = CC._merge_playwright(
        store, "x.com", {"fresh": ("x.com", "v")}, include_subdomains=True
    )

    assert {c["name"] for c in merged} == {"fresh"}


def test_a_store_that_is_not_utf8_is_treated_as_empty_as_documented(tmp_path):
    """The catch named `json.JSONDecodeError` and `OSError`. A file of non-UTF-8
    bytes raises `UnicodeDecodeError`, which is neither, so the documented
    recovery crashed on one of the two ways a file is unparseable."""
    store = tmp_path / "cookies.json"
    store.write_bytes(b"\xff\xfe\x00binary")

    merged = CC._merge_playwright(store, "x.com", {"fresh": ("x.com", "v")})

    assert [c["name"] for c in merged] == ["fresh"]


# ============================================================
# The Linux keyring degrades the way its docstring promises
# ============================================================

def _fake_secretstorage(monkeypatch, collection):
    mod = types.ModuleType("secretstorage")
    mod.dbus_init = lambda: types.SimpleNamespace(close=lambda: None)
    mod.get_default_collection = lambda bus: collection
    monkeypatch.setitem(sys.modules, "secretstorage", mod)
    return mod


def test_a_keyring_that_raises_still_leaves_the_v10_key(monkeypatch, capsys):
    """The docstring says v11 is best-effort. Only ImportError and dbus_init
    were guarded, so a raise from get_default_collection, is_locked, or
    get_secret killed the whole read instead of degrading."""
    class Exploding:
        def is_locked(self):
            raise RuntimeError("D-Bus went away")

    _fake_secretstorage(monkeypatch, Exploding())

    keys = CC._get_keys_linux("brave")

    assert set(keys) == {"v10"}
    assert "keyring lookup" in capsys.readouterr().err


def test_a_readable_keyring_still_yields_the_v11_key(monkeypatch):
    """The guard must not swallow the success path."""
    class Item:
        def get_secret(self):
            return b"secret"

    class Good:
        def is_locked(self):
            return False

        def search_items(self, _query):
            return [Item()]

    _fake_secretstorage(monkeypatch, Good())

    keys = CC._get_keys_linux("brave")

    assert set(keys) == {"v10", "v11"}
    assert keys["v11"] == CC._derive_pbkdf2(b"secret", iterations=1)


def test_a_locked_collection_still_warns_and_degrades(monkeypatch, capsys):
    class Locked:
        def is_locked(self):
            return True

    _fake_secretstorage(monkeypatch, Locked())

    keys = CC._get_keys_linux("brave")

    assert set(keys) == {"v10"}
    assert "locked" in capsys.readouterr().err


# ============================================================
# macOS: report what the tool said, not one guessed cause
# ============================================================

def test_the_keychain_error_carries_the_real_message(monkeypatch):
    """`security` writes the reason to stderr and stderr=PIPE captured it. The
    message discarded that and asserted one cause of several, which
    `.claude/rules/scope-claims.md` forbids."""
    import subprocess as sp

    def boom(*a, **k):
        raise sp.CalledProcessError(44, a[0], output=b"", stderr=b"SecKeychainSearchCopyNext: not found")

    monkeypatch.setattr(CC.subprocess, "check_output", boom)

    with pytest.raises(RuntimeError, match="not found"):
        CC._get_keys_mac("Brave Safe Storage")


def test_a_silent_keychain_failure_says_so_rather_than_inventing_a_cause(monkeypatch):
    import subprocess as sp

    def boom(*a, **k):
        raise sp.CalledProcessError(1, a[0], output=b"", stderr=b"")

    monkeypatch.setattr(CC.subprocess, "check_output", boom)

    with pytest.raises(RuntimeError, match="no message on stderr"):
        CC._get_keys_mac("Brave Safe Storage")


# ============================================================
# The Gecko twin carries the same two rules
# ============================================================

def _gecko_profile(tmp_path: Path, rows) -> Path:
    root = tmp_path / "Floorp"
    prof = root / "Profiles" / "abc123.ClaudeCode"
    prof.mkdir(parents=True)
    (root / "profiles.ini").write_text(
        "[Profile0]\nName=ClaudeCode\nIsRelative=1\nPath=Profiles/abc123.ClaudeCode\n"
    )
    conn = sqlite3.connect(prof / "cookies.sqlite")
    conn.execute("CREATE TABLE moz_cookies(name TEXT, value TEXT, host TEXT, expiry INT)")
    conn.executemany("INSERT INTO moz_cookies VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return root


def test_the_gecko_reader_escapes_the_domain_too(tmp_path, monkeypatch):
    """`scripts/linkedin-activity.py` runs this one, and it had the identical
    unescaped LIKE."""
    root = _gecko_profile(tmp_path, [
        ("T", "OURS", ".my_site.com", 0),
        ("T", "THEIRS", ".myXsite.com", 0),
    ])
    monkeypatch.setattr(FC, "_resolve_browser_root", lambda browser: root)

    assert FC.get_cookies("my_site.com", "ClaudeCode") == {"T": "OURS"}


@pytest.mark.parametrize("reverse", [False, True])
def test_the_gecko_reader_picks_the_same_winner_either_way(tmp_path, monkeypatch, reverse):
    """`li_at` on `.linkedin.com` and on `www.linkedin.com` decided which
    session the caller authenticated with, by row order, silently."""
    rows = [("li_at", "REAL", ".linkedin.com", 0),
            ("li_at", "SUB", "www.linkedin.com", 0)]
    if reverse:
        rows = list(reversed(rows))
    root = _gecko_profile(tmp_path, rows)
    monkeypatch.setattr(FC, "_resolve_browser_root", lambda browser: root)

    assert FC.get_cookies("linkedin.com", "ClaudeCode") == {"li_at": "REAL"}


def test_the_gecko_reader_still_drops_expired_and_keeps_session_cookies(tmp_path, monkeypatch):
    root = _gecko_profile(tmp_path, [
        ("OLD", "stale", ".x.com", 1),
        ("LIVE", "fresh", ".x.com", 0),
    ])
    monkeypatch.setattr(FC, "_resolve_browser_root", lambda browser: root)

    assert FC.get_cookies("x.com", "ClaudeCode") == {"LIVE": "fresh"}


# ============================================================
# No third copy of either rule
# ============================================================

_HANDMADE_LIKE = re.compile(r"LIKE\s+\?(?!\s*ESCAPE)", re.IGNORECASE)
_HANDMADE_SUFFIX = re.compile(r'f"%\.\{')


# The one module allowed to build a LIKE pattern: it IS the rule, it quotes the
# broken form in its docstring to explain what it replaced, and its behaviour is
# pinned directly by the query tests at the top of this file rather than by the
# shape detector below.
_RULE_OWNER = Path("scripts/utils/cookie_domains.py")


def _python_sources():
    for path in sorted((ROOT / "scripts").rglob("*.py")):
        if ".venv" in path.parts or path.relative_to(ROOT) == _RULE_OWNER:
            continue
        yield path


def test_exactly_one_module_is_exempt_from_the_shape_detector():
    """An exemption list that grows is how a detector stops detecting."""
    assert (ROOT / _RULE_OWNER).is_file()
    assert _RULE_OWNER not in {p.relative_to(ROOT) for p in _python_sources()}
    assert len(list(_python_sources())) > 100, "the corpus must not be empty"


def test_no_module_builds_a_like_pattern_without_an_escape_clause():
    """Both readers wrote this rule by hand and both got it wrong. The next one
    must go through `scripts/utils/cookie_domains.host_match_sql`."""
    offenders = [
        f"{p.relative_to(ROOT)}:{i}"
        for p in _python_sources()
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if _HANDMADE_LIKE.search(line)
    ]

    assert offenders == [], f"unescaped LIKE parameter: {offenders}"


def test_no_module_builds_the_subdomain_pattern_by_hand():
    offenders = [
        f"{p.relative_to(ROOT)}:{i}"
        for p in _python_sources()
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if _HANDMADE_SUFFIX.search(line)
    ]

    assert offenders == [], f"hand-built subdomain LIKE pattern: {offenders}"


def test_the_detector_still_fires_on_the_shape_it_is_looking_for():
    """A pattern that matches nothing passes everything."""
    assert _HANDMADE_LIKE.search('"WHERE host = ? OR host LIKE ?"')
    assert not _HANDMADE_LIKE.search("\"host LIKE ? ESCAPE '\\\\'\"")
    assert _HANDMADE_SUFFIX.search('params = (domain, f"%.{domain}")')


def test_both_readers_actually_import_the_shared_helper():
    """The detector above only proves the broken shape is absent. This proves
    the correct one is present, so deleting the query entirely cannot pass."""
    for name in ("chromium_cookies.py", "firefox_cookies.py"):
        text = (ROOT / "scripts" / "utils" / name).read_text(encoding="utf-8")
        assert "from scripts.utils.cookie_domains import" in text, name
        assert "host_match_sql(" in text, name
        assert "pick_per_name(" in text, name
