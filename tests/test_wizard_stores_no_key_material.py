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

Widened 2026-09-01. Every test below built its own state dict and called
`save_answers` by hand, so the file measured `_mask_secret` and `save_answers`
and never the path that actually handles a credential. MEASURED against this
file, by mutation, on that date:

    "value": masked,  ->  "value": value          in cmd_question's secret branch
        -> SURVIVED (caught only by a neighbour, tests/test_apply_wizard_answers
           .py::test_secret_masks_value_in_answers, and only because that test's
           fixture value happens to contain a substring it searches for)
    _log(... f"[written, len={len(value)}]")  ->  f"[written, {value}]"
        -> SURVIVED tree-wide. Nothing in the suite reads `.setup/wizard.log`
           for content at all, and that file had no mode of its own, so a
           credential written there sat world-readable at rest.

So the last section runs the real wizard in a child process with an invented
key and asks the only question that generalises: which bytes of that key exist
anywhere afterwards, in any file, on stdout, or on stderr. `.env` is the one
permitted answer.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

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


# ============================================================
# End to end: the real wizard, and every byte it leaves behind
# ============================================================

# Invented. `sk-ant-api03-` is the published prefix shape; everything after it is
# filler typed for this test and is not a credential of any kind.
E2E_KEY = "sk-ant-api03-" + "Zq7WmBd9Tv1KxR4NpJ6HcYs2" * 3  # pragma: allowlist secret - invented filler; this test exists to prove no key material escapes
WINDOW = 8  # the mask legitimately keeps four characters, so eight is the floor


def _windows(secret: str, size: int = WINDOW) -> list[str]:
    return [secret[i:i + size] for i in range(len(secret) - size + 1)]


def _leaks(haystack: str, secret: str) -> list[str]:
    """Every `WINDOW`-length run of `secret` that survives in `haystack`.

    A whole-string search is the assertion that was not there; it is also the
    weakest one available. The 2026-08-23 defect kept ten characters and no
    whole key, so a test looking for the key would have passed over it. Windows
    catch a prefix, a suffix, and any slice in between, and eight is short
    enough to be well below what `_mask_secret` is allowed to keep and long
    enough that a real key's entropy makes a coincidence implausible.
    """
    return sorted({w for w in _windows(secret) if w in haystack})


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "wizard-questions.yaml").write_text(
        yaml.safe_dump([{
            "id": "anthropic_api_key", "audience": ["public", "exec"],
            "type": "secret",
            "required": True, "prompt": "key?", "example": "e",
            "target": {"env_var": "ANTHROPIC_API_KEY"},
        }]), encoding="utf-8")
    # Not ceo-master: the wizard refuses that outright, which would make every
    # assertion below green over a run that never handled a key.
    (tmp_path / ".workspace-identity.json").write_text(
        json.dumps({"role": "exec", "slug": "marlow-carter",
                    "type": "exec-workspace"}),
        encoding="utf-8")
    return tmp_path


@pytest.fixture
def answered(workspace):
    """Run the real script the way the setup wizard runs it."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "apply-wizard-answers.py"),
         "--question", "anthropic_api_key", "--value-from-stdin"],
        cwd=workspace, input=json.dumps({"value": E2E_KEY}),
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result


def test_the_probe_can_see_a_leak_when_there_is_one():
    """The control. Without it, every assertion below is green over a search
    that finds nothing because it looks for nothing."""
    assert _leaks(f"KEY={E2E_KEY}\n", E2E_KEY), "the window search is inert"
    assert not _leaks("************" + E2E_KEY[-4:], E2E_KEY), (
        "the permitted four-character tail is being read as a leak")


def test_the_env_file_is_the_one_place_the_key_exists(answered, workspace):
    env = (workspace / ".env").read_text(encoding="utf-8")
    assert f"ANTHROPIC_API_KEY={E2E_KEY}" in env
    assert stat.S_IMODE((workspace / ".env").stat().st_mode) & 0o077 == 0


def test_no_other_file_in_the_workspace_holds_any_run_of_the_key(answered,
                                                                 workspace):
    """Every file the run left behind, `.env` excepted. Not a named list: the
    defect this file was written for was a second file nobody had thought of."""
    checked = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.name == ".env":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        checked.append(path.relative_to(workspace).as_posix())
        assert not _leaks(text, E2E_KEY), (
            f"{path.relative_to(workspace)} holds key material: "
            f"{_leaks(text, E2E_KEY)}")
    assert ".setup/answers.json" in checked, checked
    assert ".setup/wizard.log" in checked, (
        f"the wizard's own diary was not written, so this test proved nothing "
        f"about it. Files seen: {checked}")


def test_neither_stream_carries_the_key(answered):
    """stdout is the machine-readable result the calling skill parses, and
    stderr is what lands in a terminal scrollback and a transcript."""
    assert not _leaks(answered.stdout, E2E_KEY), answered.stdout
    assert not _leaks(answered.stderr, E2E_KEY), answered.stderr


def test_the_diary_records_the_variable_and_the_length_and_nothing_else(
        answered, workspace):
    """What the log is FOR, pinned, so 'carries no key' is not satisfied by an
    empty file. The length is metadata the operator asked for; the value is not.
    """
    log = (workspace / ".setup" / "wizard.log").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" in log
    assert f"len={len(E2E_KEY)}" in log


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_the_diary_is_not_world_readable(answered, workspace):
    """`.env` and `answers.json` are both 0600. This file sits between them in
    `.setup/`, names every credential variable on the machine and its length,
    and was created at umask defaults."""
    mode = stat.S_IMODE((workspace / ".setup" / "wizard.log").stat().st_mode)
    assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0, (
        f"wizard.log is mode {oct(mode)}; group or other can read it")
