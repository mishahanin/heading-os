"""The bearer-token unit contract, including the tokens nobody sent on purpose.

*A malformed token crashed the auth boundary.* `validate` was
`secrets.compare_digest(provided or "", expected)`, and `compare_digest`
accepts two `str` arguments only while BOTH are ASCII. One byte above 0x7F in
the `Authorization` header (uvicorn decodes it as latin-1 and hands it
straight to `app._require_token`) raised `TypeError: comparing strings with
non-ASCII characters is not supported` out of the route, which FastAPI turns
into 500 Internal Server Error. Measured 2026-08-31 against the real ASGI app,
driving the scope uvicorn builds, before the fix:

    raw 0xFF byte          -> UNCAUGHT TypeError: comparing strings with
                              non-ASCII characters is not supported
    utf-8 cyrillic bytes   -> UNCAUGHT TypeError: (same)
    valid ascii wrong      -> 401
    100k ascii             -> 401

and at this unit level:

    validate('\xff', 't1')  -> RAISED TypeError
    validate('п', 't1') -> RAISED TypeError
    validate('\U0001f600', 't1') -> RAISED TypeError

Nothing was disclosed and nothing was let through; the request failed either
way. What was wrong is that an unauthenticated caller chose between 401 and
500 by flipping one bit, on the one boundary reachable before any other check.

*Why the old file could not see it.* Its four `validate` cases were all
7-to-9-character ASCII (`"abc123def"`, `"xyz789xyz"`, `"abc"`, `""`, `None`)  # pragma: allowlist secret
- typed-out filler quoted to record what the OLD cases were, never credentials.
Every input that reaches `compare_digest` at all was ASCII, so the one
argument shape that raises was never constructed. The endpoint-level sibling
in `test_endpoints.py` had the same hole for the same reason: an HTTP client
cannot put a non-ASCII header on the wire, so the case only appears if you
drive the ASGI scope by hand, which is what that file now does.

The over-long and valid-UTF-8-but-wrong shapes are checked here too, since
they are the same question asked of different bytes; both were already
correct (401, measured above) and are now pinned rather than assumed.
"""
from pathlib import Path

import pytest

from scripts.bridge_daemon.auth import generate_token, get_or_create_token, validate

def test_generate_token_deterministic_per_workspace(workspace_root):
    t1 = generate_token(workspace_root)
    t2 = generate_token(workspace_root)
    assert t1 != t2  # nonce makes each call distinct

def test_get_or_create_persists(workspace_root):
    t1 = get_or_create_token(workspace_root)
    t2 = get_or_create_token(workspace_root)
    assert t1 == t2  # second call reads the persisted token
    token_file = workspace_root / ".daemon-state" / "token"
    assert token_file.read_text().strip() == t1

def test_validate_accepts_matching_token():
    assert validate("abc123def", "abc123def") is True  # pragma: allowlist secret

def test_validate_rejects_mismatch():
    assert validate("abc123def", "xyz789xyz") is False  # pragma: allowlist secret

def test_validate_handles_none_and_empty():
    # None or empty `provided` -> False (does not raise)
    assert validate(None, "abc") is False
    assert validate("", "abc") is False
    # None or empty `expected` -> False (short-circuit, depends on Fix 1)
    assert validate("abc", None) is False
    assert validate("abc", "") is False


# --- the malformed tokens nobody sent on purpose (2026-08-31) ---

# A real bearer token is a sha256 hex digest, so every case below is a
# non-token. The question is only whether each is REFUSED or CRASHES.
MALFORMED = [
    pytest.param("\xff", id="raw-0xff-latin1-decoded"),
    pytest.param("пароль", id="cyrillic"),
    pytest.param("\U0001f600", id="emoji-astral"),
    pytest.param("t1\xff", id="valid-prefix-then-high-byte"),
    pytest.param("\udcff", id="lone-surrogate-from-surrogateescape"),
    pytest.param("a" * 100_000, id="100k-ascii"),
    pytest.param("\xff" * 10_000, id="10k-non-ascii"),
    pytest.param("wrongtoken", id="valid-utf8-not-the-secret"),
]


@pytest.mark.parametrize("provided", MALFORMED)
def test_validate_refuses_a_malformed_token_without_raising(provided):
    """The whole finding: these used to raise TypeError, not return False.

    A refusal and a crash are both "the request failed", which is why this
    survived review. They are not the same answer: one is 401 from the auth
    check, the other is 500 from the error middleware.
    """
    assert validate(provided, "t1") is False


@pytest.mark.parametrize("provided", MALFORMED)
def test_the_expected_token_side_is_symmetric(provided):
    """A hostile value in `expected` must not raise either.

    `expected` comes from the token file, so a corrupt or hand-edited one
    reaches this argument. It has the same encode step now, and this is the
    case that would have gone missing if the fix had only touched `provided`.
    """
    assert validate("t1", provided) is False


def test_a_matching_non_ascii_pair_still_compares_equal():
    """Encoding is a fix, not a blanket refusal of non-ASCII.

    Nothing generates such a token today, since `generate_token` returns a
    hex digest, but a `validate` that answered False for every non-ASCII input
    would pass the two tests above while quietly being a reject-list rather
    than a comparison, and the next caller with a non-hex token would be
    locked out with no way to see why.
    """
    assert validate("пароль", "пароль") is True
    assert validate("\U0001f600", "\U0001f600") is True
    assert validate("пароль", "паролЬ") is False


def test_validate_still_rejects_a_one_character_difference():
    """The comparison must not have been widened into a prefix or length test."""
    token = generate_token(Path("/tmp/x"))  # noqa: S108 - a path STRING handed to the token deriver, never opened
    assert validate(token, token) is True
    assert validate(token[:-1], token) is False
    assert validate(token + "0", token) is False
    assert validate(token[:-1] + ("1" if token[-1] != "1" else "2"), token) is False


def test_validate_compares_in_constant_time_via_compare_digest():
    """The timing property is the reason this is not `==`, so pin the call.

    The three behavioural tests above force an encode-then-compare shape, but
    they cannot tell `compare_digest(a, b)` from `a == b` on the same bytes,
    and the second one leaks the length of the matching prefix.

    Asked of `validate`'s own AST rather than of the file's text, so a comment
    mentioning the name is not evidence and a re-wrap is not a failure. Not
    monkeypatched onto the `secrets` module either: rebinding a stdlib
    attribute is process-wide, and a test that does it is one interpreter
    ordering away from taking strangers down with it.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(validate))
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else
        getattr(node.func, "id", None)
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert "compare_digest" in called, (
        f"validate stopped routing through a constant-time compare: {called}")
    assert "encode" in called, (
        "validate no longer encodes to bytes; compare_digest on two str "
        f"arguments raises TypeError on any non-ASCII input: {called}")
    assert not any(isinstance(node, ast.Compare)
                   and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops)
                   for node in ast.walk(tree)), (
        "validate grew a plain ==/!= comparison; the secret must only ever "
        "be compared through compare_digest")
