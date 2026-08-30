"""SQLite `=` is case-sensitive and `LIKE` is not, so the three legs of the
cookie-match OR disagreed about which stored host answers a request.

`scripts/utils/cookie_domains.host_match_sql` builds a WHERE clause with three
legs: the host-only row, the domain-cookie row, and a subdomain LIKE. Under
SQLite's defaults the two `=` legs use the BINARY collation (case-SENSITIVE)
while `LIKE` is ASCII-case-INSENSITIVE. A store holding `EXAMPLE.com` was
therefore invisible to a request for `example.com`, while `.EXAMPLE.COM` in the
same store matched - so the row `host_rank` would have chosen as rank 0, the
exact host, was the one the query never selected.

The module's whole reason to exist is that two hand-written copies of this rule
were wrong. A case rule that changes halfway through one WHERE clause is the
same defect, one layer down.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.cookie_domains import host_match_sql, host_rank, pick_per_name


def _store(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE cookies(host_key TEXT, name TEXT, value TEXT)")
    conn.executemany("INSERT INTO cookies VALUES(?,?,?)", rows)
    return conn


def _select(conn, domain, include_subdomains=True):
    where, params = host_match_sql("host_key", domain, include_subdomains)
    # noqa S608: the same shape both readers use - the interpolated fragment is
    # the helper's own output over a checked column literal, and the domain
    # travels as a bound parameter.
    return conn.execute(
        f"SELECT host_key, name, value FROM cookies WHERE {where}", params  # noqa: S608
    ).fetchall()


def test_an_uppercase_host_only_row_answers_a_lowercase_request():
    """The defect, in one row: `EXAMPLE.com` matched nothing at all."""
    conn = _store([("EXAMPLE.com", "SID", "real")])
    assert _select(conn, "example.com") == [("EXAMPLE.com", "SID", "real")]


def test_every_leg_of_the_or_agrees_about_case():
    """All three legs answer, so the ranker sees the whole candidate set."""
    conn = _store([
        ("EXAMPLE.com", "SID", "host-only"),
        (".EXAMPLE.COM", "SID", "domain"),
        ("A.Example.Com", "SID", "subdomain"),
    ])
    hosts = {row[0] for row in _select(conn, "example.com")}
    assert hosts == {"EXAMPLE.com", ".EXAMPLE.COM", "A.Example.Com"}


def test_the_ranker_can_now_reach_the_row_it_would_have_chosen():
    """End to end: the apex wins, which it could not when the SQL dropped it.

    `host_rank` lowercases both sides, so it always considered `EXAMPLE.com` the
    rank-0 answer. Before the fix the query never handed it that row, and the
    subdomain value won by default - a map that authenticates against the wrong
    host, which is the exact outcome this module was written to stop.
    """
    conn = _store([
        ("A.Example.Com", "SID", "subdomain-token"),
        ("EXAMPLE.com", "SID", "apex-token"),
    ])
    winners, dropped = pick_per_name(_select(conn, "example.com"), "example.com")
    assert winners["SID"][1] == "apex-token"
    assert dropped == [("SID", "A.Example.Com", "EXAMPLE.com")]
    assert host_rank("EXAMPLE.com", "example.com") < host_rank("A.Example.Com", "example.com")


def test_the_exact_host_leg_is_case_insensitive_too():
    """`include_subdomains=False` must not disagree with the branch above it."""
    conn = _store([("EXAMPLE.com", "SID", "real")])
    assert _select(conn, "example.com", include_subdomains=False) == [
        ("EXAMPLE.com", "SID", "real")
    ]


@pytest.mark.parametrize(
    "host",
    ["notexample.com", "example.com.evil.net", "EVIL.COM", "myXsite.com"],
)
def test_case_insensitivity_did_not_widen_what_matches(host):
    """A foreign host stays foreign; only the case rule changed.

    `myXsite.com` is the LIKE-metacharacter case the module already guards: it
    must not answer a request for `my_site.com`.
    """
    conn = _store([(host, "SID", "foreign")])
    assert _select(conn, "example.com") == []
    assert _select(conn, "my_site.com") == []


def test_the_documented_usage_call_satisfies_the_real_signature():
    """The module docstring is the only documented call of `pick_per_name`.

    It omitted the required `domain` argument, so every adopter who copied it
    got `TypeError: missing 1 required positional argument` before a row was
    read. The check binds the docstring's call to the LIVE signature through
    `inspect.Signature.bind`, so it decays neither when the example is reworded
    nor when the signature gains a parameter.
    """
    import ast
    import inspect

    import scripts.utils.cookie_domains as mod

    calls = []
    for line in (mod.__doc__ or "").splitlines():
        stripped = line.strip()
        if "pick_per_name(" not in stripped:
            continue
        try:
            tree = ast.parse(stripped)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "pick_per_name"
            ):
                calls.append(node)

    assert calls, "the Usage block no longer shows a pick_per_name call"

    signature = inspect.signature(pick_per_name)
    for call in calls:
        assert not any(
            isinstance(arg, ast.Starred) for arg in call.args
        ), "the documented call must be checkable, not splatted"
        signature.bind(
            *[object() for _ in call.args],
            **{kw.arg: object() for kw in call.keywords},
        )
