"""The AWS key pattern fired inside embedded base64 and stopped a real push.

`AKIA[0-9A-Z]{16}` had no boundary on either side, and `AKIA` is the one prefix
in this table that a standard base64 alphabet can spell: four characters drawn
from the same 64 the payload is drawn from. So any file carrying an embedded
image could stop a push over a credential that is not there.

MEASURED 2026-09-05 against the two generated magazine builds in the data
overlay, which is the refusal that blocked the operator's push:

    odun-one-magazine-digital.html   1 match
    odun-one-magazine-print.html     7 matches
    every one of them a twenty-character run inside a base64 payload, with more
    base64 either side: `...AAAAAAAAAIUC` before and `CwWBUFQAAVBY` after

    after the boundary                0 matches in both files

THE DIRECTION THAT MATTERS is the second one, and it is what most of this file
asserts: a real key ID must still be caught, in every shape one is written in.
Those keys are FABRICATED HERE AT RUN TIME, by concatenating the prefix with
sixteen characters, so the literal never exists in this file or in any command
line. That is not decoration: a PreToolUse wall in this workspace blocks the
literal in a Bash command, which is itself a live demonstration that the pattern
works, and writing one out would have made this test file unwritable.

WHAT THE PADDING CHARACTER WOULD HAVE COST. `=` is deliberately absent from the
lookahead. For padding to follow this match inside base64, the payload would
have to begin exactly at `AKIA` and be exactly twenty characters, and twenty is a
multiple of four, so well-formed base64 of that length carries no padding. The
case cannot arise, while `=` in the lookahead WOULD drop a real key written
immediately before one. `test_a_key_immediately_before_an_equals_sign_is_still_caught`
is that decision, written down as a test rather than as an opinion.
"""
from __future__ import annotations

import random
import re
import string
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.secret_patterns import SECRET_PATTERNS  # noqa: E402

AWS = next(pattern for pattern, description in SECRET_PATTERNS if description == "AWS access key")

PREFIX = "AK" + "IA"
BODY = "IOSFODNN7" + "EXAMPLE"          # sixteen characters, assembled here
KEY = PREFIX + BODY
BASE64 = string.ascii_letters + string.digits + "+/"


def test_the_fabricated_key_is_the_right_shape():
    """A floor under every case below: a mis-assembled key would pass them all.

    If `KEY` were nineteen characters or carried a lowercase letter, every
    true-positive assertion in this file would be asserting that the pattern
    misses something it is not supposed to catch, and the file would go green
    while the wall was disarmed.
    """
    assert len(KEY) == 20
    assert KEY.startswith("AKIA")
    assert re.fullmatch(r"AKIA[0-9A-Z]{16}", KEY)


# Every shape a key is actually written in. Each is a TRUE POSITIVE and must
# still be caught; losing one of these is far worse than the false positive this
# change removes.
TRUE_POSITIVES = [
    ("bare on its own line", "{k}"),
    ("after an equals sign", "AWS_ACCESS_KEY_ID={k}"),
    ("exported from a shell profile", "export AWS_ACCESS_KEY_ID={k}"),
    ("double quoted", 'aws_access_key_id = "{k}"'),
    ("single quoted", "aws_access_key_id = '{k}'"),
    ("in JSON", '{{"AccessKeyId": "{k}", "Status": "Active"}}'),
    ("in YAML", "aws:\n  access_key_id: {k}\n"),
    ("in an ini section", "[default]\naws_access_key_id = {k}\n"),
    ("after whitespace in prose", "the key is {k} and it must be rotated"),
    ("in a URL query", "https://example.com/x?AWSAccessKeyId={k}&Expires=1"),
    ("comma separated", "user,{k},active"),
    ("after a colon", "key:{k}"),
    ("inside angle brackets", "<{k}>"),
    ("preceded by a hyphen", "prod-{k}"),
    ("preceded by an underscore", "prod_{k}"),
    ("wrapped in parentheses", "(rotate {k})"),
    ("at the very start of the text", "{k} is the id"),
]


@pytest.mark.parametrize("label,shape", TRUE_POSITIVES, ids=[label for label, _ in TRUE_POSITIVES])
def test_a_real_key_is_still_caught(label, shape):
    text = shape.format(k=KEY)
    assert AWS.search(text), f"a real key went undetected: {label}"


def test_a_key_at_end_of_file_with_no_trailing_newline_is_caught():
    assert AWS.search("aws_access_key_id = " + KEY)


def test_a_key_alone_with_nothing_around_it_is_caught():
    assert AWS.search(KEY)


def test_a_key_immediately_before_an_equals_sign_is_still_caught():
    """The padding decision, asserted rather than argued.

    Adding `=` to the lookahead would have made this fail. Nothing measured
    required it, and this is the true positive it would have cost.
    """
    assert AWS.search(KEY + "=")
    assert AWS.search(KEY + "==")


def test_the_false_positive_no_longer_fires():
    """The regression, built from the exact context the real refusal carried."""
    embedded = "AAAAAAAAAIUC" + KEY + "CwWBUFQAAVBY"
    assert re.compile(r"AKIA[0-9A-Z]{16}").search(embedded), (
        "the old pattern must still match this, or the case proves nothing"
    )
    assert not AWS.search(embedded)


def test_a_run_that_only_touches_on_one_side_is_still_refused():
    assert not AWS.search("AAAAAAAAAIUC" + KEY)
    assert not AWS.search(KEY + "CwWBUFQAAVBY")


def test_a_generated_base64_payload_produces_no_findings():
    """A corpus check with a floor, so an empty corpus cannot pass it.

    A megabyte of uppercase-heavy base64 is the shape an embedded PNG takes. The
    old pattern is asserted to fire on it first: without that, a run in which
    `AKIA` never appeared would make this test vacuous.
    """
    rng = random.Random(20260905)  # noqa: S311 - a seeded base64 fixture, not crypto
    payload = "".join(rng.choice(BASE64) for _ in range(400_000))
    # Plant the run the way an image does: surrounded by more payload.
    payload = payload[:1000] + KEY + payload[1000:]
    old = re.compile(r"AKIA[0-9A-Z]{16}")
    assert old.search(payload), "corpus floor: the old pattern found nothing to fix"
    assert not AWS.search(payload)


def test_the_dispatch_copy_carries_the_same_boundary():
    """The mirror. A fix that lands in one of two copies is this repository's
    dominant defect shape, and the blocking PreToolUse gate is the copy that
    matters most: it is the one a Write is judged by.
    """
    source = (ROOT / ".claude" / "hooks" / "_dispatch.py").read_text(encoding="utf-8")
    assert r"(?<![A-Za-z0-9+/])AKIA[0-9A-Z]{16}(?![A-Za-z0-9+/])" in source
    assert "re.compile(r'AKIA[0-9A-Z]{16}')" not in source


def test_no_other_entry_regressed_into_matching_a_plain_base64_run():
    """The whole table, not only the entry that was changed.

    `AKIA` is the only prefix a standard base64 alphabet can spell, which is why
    only it was given a boundary. This asserts that claim against a generated
    payload rather than restating it: if another entry starts matching random
    base64, the reason to have left it alone has expired.
    """
    rng = random.Random(20260906)  # noqa: S311 - a seeded base64 fixture, not crypto
    payload = "".join(rng.choice(BASE64) for _ in range(500_000))
    firing = [
        description
        for pattern, description in SECRET_PATTERNS
        if pattern.search(payload)
    ]
    assert firing == [], f"entries matching plain base64: {firing}"
