#!/usr/bin/env python3
"""One correct way to match cookie rows against a domain, and to pick a winner.

Two cookie readers - `scripts/utils/chromium_cookies.py` and
`scripts/utils/firefox_cookies.py` - each wrote the same two rules by hand, and
each got both of them wrong in the same way.

**The match.** Both built the subdomain leg as a bare LIKE parameter:

    "WHERE host = ? OR host = ? OR host LIKE ?", (domain, f".{domain}", f"%.{domain}")

SQLite LIKE treats `%` and `_` as wildcards, and neither reader escaped either
one. Measured 2026-08-28 against a real SQLite table with those exact params:
asking for `my_site.com` also returned `.myXsite.com`, because `_` matched `X`;
asking for `%.com` returned every row in the table, including `.evil.com`. The
domain arrives straight from the operator's keyboard (`/setup-browser-cookies
<domain>`), so this needs no hostile input to fire, and the caller then writes
the foreign host's live session token into the export as if it belonged to the
domain that was asked for.

`chromium_cookies.py` already carried the CORRECT form of this rule ninety lines
below the broken one, in `_merge_playwright._is_this_domain`, with a docstring
explaining the dot-boundary defect it had just fixed. The SQL copy was never
touched. The same correct rule is also hand-written at `scripts/firecrawl.py`
and `scripts/osint-advanced-sync.py`. Three correct copies, two broken ones,
one rule.

**The winner.** Both readers then flattened the rows into `{name: value}` with
a plain `cookies[name] = value`, over a query with no ORDER BY. Two rows sharing
a name - which is ordinary for `SID`, `SESSION`, `csrftoken`, `li_at` - collapse
to whichever row the table scan reached last. Measured: the same query over the
same two rows returned `SID = REAL` or `SID = SUBDOMAIN` depending only on
insertion order. `scripts/linkedin-activity.py` asserts that `li_at` is present
and then authenticates with it, so the coin flip decides which session it uses
and nothing in the output says a collision happened.

A `dict[str, str]` genuinely cannot carry two hosts, and that flat shape is the
documented contract of both readers. This module does not change it. It makes
the choice deterministic, makes it the RIGHT one, and hands the caller the list
of what was dropped so the drop can be reported instead of hidden.

Usage:

    from scripts.utils.cookie_domains import host_match_sql, pick_per_name

    where, params = host_match_sql("host_key", domain, include_subdomains)
    rows = conn.execute(f"SELECT host_key, name, value FROM cookies WHERE {where}", params)
    winners, dropped = pick_per_name((r[0], r[1], r[2]) for r in rows)
"""
from __future__ import annotations

__all__ = ["host_match_sql", "host_rank", "pick_per_name", "LIKE_ESCAPE"]

# Backslash is the conventional LIKE escape and is not legal in a hostname, so
# it can never collide with a real host_key.
LIKE_ESCAPE = "\\"


def _escape_like(value: str) -> str:
    """Neutralise the three characters SQLite LIKE reads as syntax.

    The escape character itself goes first. Escaping `%` and `_` before `\\`
    would then re-escape the backslashes this function just introduced.
    """
    out = value.replace(LIKE_ESCAPE, LIKE_ESCAPE + LIKE_ESCAPE)
    out = out.replace("%", LIKE_ESCAPE + "%")
    return out.replace("_", LIKE_ESCAPE + "_")


def host_match_sql(column: str, domain: str, include_subdomains: bool = True):
    """Return (where_fragment, params) matching `column` against `domain`.

    With `include_subdomains`, matches the host-only row (`example.com`), the
    domain row (`.example.com`) and any subdomain row (`a.example.com`,
    `.a.example.com`). Without it, only the exact host.

    `column` is interpolated into SQL and must therefore be a literal the caller
    controls, never operator input. `domain` is always a bound parameter, and
    its LIKE metacharacters are escaped.
    """
    if not domain:
        raise ValueError("domain must be non-empty")
    if not column.isidentifier():
        raise ValueError(f"column must be a plain identifier, got {column!r}")

    if not include_subdomains:
        return f"{column} = ?", (domain,)

    where = (
        f"{column} = ? OR {column} = ? "
        f"OR {column} LIKE ? ESCAPE '{LIKE_ESCAPE}'"
    )
    return where, (domain, f".{domain}", f"%.{_escape_like(domain)}")


def host_rank(host: str, domain: str) -> tuple:
    """Sort key for how well `host` answers a request for `domain`. Lower wins.

    A browser would send several of these for one request; a flat name->value
    map has to choose one. The order is specific-to-the-asked-host first:

      0. `example.com`      - the host-only cookie for exactly this host
      1. `.example.com`     - the domain cookie for this host
      2. a subdomain, shallower before deeper, then alphabetically

    A subdomain cookie ranking BELOW the apex is the part that matters. A
    request to `example.com` would never carry `accounts.example.com`'s cookie
    at all, so letting it overwrite the apex value - which is what row order did
    - produced a map that authenticates against the wrong host.

    The final element is the host string itself, so two hosts of equal depth
    still order deterministically rather than by table scan.
    """
    bare = host.lstrip(".").lower()
    want = domain.lstrip(".").lower()
    if bare == want:
        return (0 if not host.startswith(".") else 1, 0, host)
    return (2, bare.count("."), host)


def pick_per_name(rows, domain: str):
    """Reduce (host, name, payload) rows to one payload per name.

    Returns `(winners, dropped)`.

      winners: {name: (host, payload)} - the best-ranked row for each name.
      dropped: [(name, losing_host, winning_host)] - every row a winner beat,
               so the caller can report the collision instead of hiding it.

    `payload` is opaque here. `chromium_cookies` passes the undecrypted row so
    that only the winners are ever decrypted; `firefox_cookies` passes the
    plaintext value.
    """
    winners: dict[str, tuple[str, object]] = {}
    ranks: dict[str, tuple] = {}
    dropped: list[tuple[str, str, str]] = []

    for host, name, payload in rows:
        rank = host_rank(host, domain)
        if name not in winners:
            winners[name] = (host, payload)
            ranks[name] = rank
            continue
        if rank < ranks[name]:
            dropped.append((name, winners[name][0], host))
            winners[name] = (host, payload)
            ranks[name] = rank
        else:
            dropped.append((name, host, winners[name][0]))

    return winners, dropped
