"""`.setup/answers.json` must hold no part of a secret that carries entropy.

Found by the 2026-08-23 engine audit.

`_mask_secret` built `value[:10] + "-REDACTED-" + value[-4:]` and that string was
written to `.setup/answers.json`. Fourteen characters of every real credential
sat at rest in a second file, and the module's own comment for the secret branch
said the opposite: "The real secret exists only in .env."

The prefix was not even used. `_display_value`, the only consumer, renders
`"************" + val[-4:]` -- the last four and nothing else. So ten characters
of key material were persisted to serve no reader.

Ten characters matter differently per key format. For `sk-ant-api03-...` the
prefix is all format and no entropy; for `ghp_16C7e42F...` six of the ten are
real. And a prefix plus a suffix is a verified anchor: someone holding a stolen
`answers.json` can confirm a candidate key without ever seeing the whole thing.

`.env` gets `os.chmod(0o600)`. `answers.json` got nothing, and it is exactly the
kind of state file that reaches a backup or a sync.

The guard is written against the STORED string rather than against `_mask_secret`
alone, because the defect was what reached disk.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _wizard():
    path = ROOT / "scripts" / "apply-wizard-answers.py"
    spec = importlib.util.spec_from_file_location("wizard_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


W = _wizard()

# Real-shaped keys, no real values. Each is the published prefix plus filler.
SAMPLE_KEYS = [
    "sk-ant-api03-" + "A9f2Kd7Qx1" * 8,
    "ghp_" + "16C7e42FzQ8v" * 3,
    "xoxb-" + "1234567890-0987654321-" + "aBcDeFgHiJkLmNoPqRsTuVwX",
    "AKIA" + "IOSFODNN7EXAMPLE",
]


@pytest.mark.parametrize("key", SAMPLE_KEYS, ids=lambda k: k[:8])
def test_the_mask_keeps_no_leading_characters_of_the_key(key):
    masked = W._mask_secret(key)
    assert key[:10] not in masked, (
        f"the mask still carries the first ten characters of the key: {masked!r}"
    )
    # Nothing longer than the display tail may survive anywhere in the mask.
    for n in range(5, 15):
        assert key[:n] not in masked, f"{n} leading characters survived: {masked!r}"


@pytest.mark.parametrize("key", SAMPLE_KEYS, ids=lambda k: k[:8])
def test_the_mask_still_serves_its_only_reader(key):
    """`_display_value` renders `val[-4:]`. Break that and the dashboard shows
    the wrong four characters, which is worse than showing none."""
    masked = W._mask_secret(key)
    entry = {"status": "answered", "value": masked}
    shown = W._display_value({"type": "secret"}, entry)
    assert shown.endswith(key[-4:]), f"{shown!r} does not end with the real tail"
    assert key[:10] not in shown


def test_a_short_value_is_fully_hidden():
    assert W._mask_secret("abc") == "****"
    assert W._mask_secret("12345678") == "****"


def test_the_stored_state_carries_no_prefix(tmp_path):
    """End to end: what actually lands in answers.json."""
    key = SAMPLE_KEYS[1]
    state = {"schema_version": W.SCHEMA_VERSION if hasattr(W, "SCHEMA_VERSION") else 1,
             "answers": {"q": {"value": W._mask_secret(key), "env_written": True,
                               "status": "answered"}}}
    W.save_answers(tmp_path, state)
    written = (tmp_path / ".setup" / "answers.json").read_text(encoding="utf-8")
    assert key[:10] not in written
    assert key not in written
    assert json.loads(written)["answers"]["q"]["value"].endswith(key[-4:])


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_answers_json_is_not_world_readable(tmp_path):
    """`.env` is chmod 0600 at line 236. This file holds the same class of
    residue and had no mode set at all."""
    W.save_answers(tmp_path, {"answers": {}})
    path = tmp_path / ".setup" / "answers.json"
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0, (
        f"answers.json is mode {oct(mode)}; group or other can read it"
    )


def test_the_comment_that_the_defect_contradicted_is_still_true():
    """The module says 'The real secret exists only in .env.' That sentence was
    false while ten characters lived in answers.json. If the sentence is ever
    deleted, this guard is protecting a preference rather than a contract."""
    src = (ROOT / "scripts" / "apply-wizard-answers.py").read_text(encoding="utf-8")
    assert "The real secret exists only in .env" in src
