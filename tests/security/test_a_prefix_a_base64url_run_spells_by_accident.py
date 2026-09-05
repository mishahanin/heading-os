"""Eight credential prefixes fired inside base64url runs, the AWS defect one alphabet over.

The AWS entry was given a boundary on 2026-09-05 after it stopped a real push
from inside an embedded image. The same commit claimed the other entries were
safe because their prefixes carry a `-`, a `_` or a `.`, none of which is in the
standard base64 alphabet. That reasoning was right about standard base64 and
wrong about the world: base64URL adds `-` and `_`, and it is what a JWT, a
URL-safe blob and a signed URL are made of.

MEASURED 2026-09-05 on generated base64url payloads, before and after:

      100,000 chars   r8_ 1,  fc- 1                          ->  0
    1,000,000 chars   r8_ 3,  fc- 1,  gho_ 1                 ->  0
   10,000,000 chars   r8_ 23, fc- 26                         ->  0
   40,000,000 chars   r8_ 94, fc- 97, cpx- 1, ghp_ 1, gho_ 2 ->  0

`fc-` is three characters, so a run spells it roughly once per 260,000
positions. Two findings in 100 KB is not a theoretical residual; it is a wall
that refuses an ordinary file.

The fix is `(?<![A-Za-z0-9_-])` on each prefixed entry: a credential is a
standalone token, and a prefix preceded by another token character is the middle
of a longer run. THE DIRECTION THAT MATTERS is that every real token shape is
still caught, which is most of this file. The tokens are fabricated at run time
so no credential-shaped literal is committed.
"""
from __future__ import annotations

import random
import string
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.secret_patterns import SECRET_PATTERNS  # noqa: E402

BY_NAME = {description: pattern for pattern, description in SECRET_PATTERNS}

# (description, prefix assembled at run time, body). Every one is a real token
# shape and every one must still be caught.
TOKENS = [
    ("Anthropic API key", "sk-" + "ant-", "A" * 24),
    ("Perplexity API key", "pp" + "lx-", "B" * 24),
    ("Replicate API token", "r8" + "_", "c" * 24),
    ("Firecrawl API key", "f" + "c-", "D" * 24),
    ("Context7 API key", "ctx7" + "sk-", "e" * 24),
    ("CLIProxyAPI local proxy key", "cp" + "x-", "F" * 24),
    ("GitHub personal access token", "gh" + "p_", "g" * 24),
    ("GitHub OAuth token", "gh" + "o_", "H" * 24),
]

BASE64URL = string.ascii_letters + string.digits + "-_"

SHAPES = [
    ("bare", "{t}"),
    ("after an equals sign", "API_KEY={t}"),
    ("double quoted", 'key = "{t}"'),
    ("single quoted", "key = '{t}'"),
    ("in JSON", '{{"token": "{t}"}}'),
    ("in YAML", "auth:\n  token: {t}\n"),
    ("after a colon", "Authorization: Bearer {t}"),
    ("in prose", "the token is {t} and must be rotated"),
    ("at the end of a URL path", "https://example.com/v1/{t}"),
    ("in a URL query", "https://example.com/?token={t}&x=1"),
    ("comma separated", "user,{t},active"),
    ("in parentheses", "(rotate {t})"),
]


@pytest.mark.parametrize("description,prefix,body", TOKENS, ids=[d for d, _, _ in TOKENS])
@pytest.mark.parametrize("label,shape", SHAPES, ids=[label for label, _ in SHAPES])
def test_a_real_token_is_still_caught(description, prefix, body, label, shape):
    pattern = BY_NAME[description]
    token = prefix + body
    assert pattern.search(shape.format(t=token)), f"{description} missed: {label}"


@pytest.mark.parametrize("description,prefix,body", TOKENS, ids=[d for d, _, _ in TOKENS])
def test_the_same_token_inside_a_longer_run_is_refused(description, prefix, body):
    """The regression, one entry at a time and with the old behaviour asserted.

    The run either side is drawn from base64url, which is what an embedded blob
    is made of. Without the first assertion a prefix that simply stopped
    matching anything would pass this test.
    """
    import re

    pattern = BY_NAME[description]
    token = prefix + body
    embedded = "aXbYcZ01_-" + token + "9qQ-_Wz"
    old = re.compile(pattern.pattern.replace("(?<![A-Za-z0-9_-])", "", 1))
    assert old.search(embedded), "the unbounded pattern must match, or this proves nothing"
    assert not pattern.search(embedded)


@pytest.mark.parametrize("size", [100_000, 1_000_000])
def test_a_generated_base64url_payload_produces_no_findings(size):
    """The measurement that started this, as a test.

    A floor is not needed here: the corpus is generated to a fixed size and the
    per-entry regression above already pins that the patterns still match.
    """
    rng = random.Random(20260905)  # noqa: S311 - a seeded payload fixture, not crypto
    payload = "".join(rng.choice(BASE64URL) for _ in range(size))
    firing = {
        description: len(pattern.findall(payload))
        for pattern, description in SECRET_PATTERNS
        if pattern.search(payload)
    }
    assert firing == {}, f"entries firing inside plain base64url: {firing}"


def test_every_prefixed_entry_carries_the_boundary():
    """Derived from the table, so a NEW prefixed entry cannot skip it silently.

    A hand-kept list of which entries were fixed is the thing that falls behind;
    this asks the table instead. An entry whose pattern opens with a bare
    literal prefix must open with the lookbehind first.
    """
    missing = []
    for pattern, description in SECRET_PATTERNS:
        source = pattern.pattern
        if source.startswith(("(?<!", "\\*", "(?:", "[a-zA-Z]", "-----")):
            continue
        missing.append(description)
    assert missing == [], f"prefixed entries with no leading boundary: {missing}"


def test_the_dispatch_copy_carries_every_boundary():
    """The mirror, checked for all eight rather than for one."""
    source = (ROOT / ".claude" / "hooks" / "_dispatch.py").read_text(encoding="utf-8")
    for prefix in ("sk-" + "ant-", "pp" + "lx-", "r8" + "_", "f" + "c-",
                   "ctx7" + "sk-", "cp" + "x-", "gh" + "p_", "gh" + "o_"):
        assert f"(?<![A-Za-z0-9_-]){prefix}" in source, prefix
