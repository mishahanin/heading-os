"""Shard scripts-15-p2: the key check that echoed the key, and four sections of
the health run that said more than they measured.

* `wizard-verify-key.py` never stripped the key before putting it in the
  `x-api-key` header. `http.client.putheader` rejects any header value holding a
  newline or carriage return with ValueError, which is not an OSError and so
  slipped past both handlers in `verify_anthropic`. The process died with
  CPython's exit code 1, and 1 means "invalid (401/403)" both in this script's
  docstring and to its only caller: the wizard told the operator a perfectly
  good key was invalid over a request that never left the machine. A trailing
  newline is the ordinary artifact of `WIZARD_VERIFY_KEY=$(cat keyfile)`. The
  ValueError message also echoes the header value, putting the credential
  verbatim into stderr and the wizard transcript.

* `workspace-health.check_build_sync` called `.get` on whatever `json.loads`
  returned. A BUILD.json holding a list, string, number or null parsed cleanly,
  so the handler never fired and AttributeError killed the WHOLE health run:
  `main` iterates the checks unguarded, so the remaining sections never ran and
  no summary printed, in front of `/push-updates`. The same handler missed
  UnicodeDecodeError, which is a ValueError and not an OSError.

* `check_pipeline_health`'s comment promised TBD, placeholder and empty fields.
  `placeholder_count` was assigned and never read, empty cells were never
  examined, and the TBD number was a whole-file substring count printed as
  "N TBD fields found".

* `check_docs_sync` never asked whether `templates/` exists, so a bare public
  engine clone with no data overlay produced six ACTIONs and exit 1. Its sibling
  `check_doc_versions` treats that identical state as legitimate, sixty lines
  below, with a comment saying so.

* `check_reference_validation` asserted "CLAUDE.md has no 'Reference Resources'
  section" from `checked == 0`, which is also what a present section with no
  backticked paths produces. The function already tracked section presence and
  never read it, so the operator got the wrong remediation. SUPERSEDED
  2026-08-30: the check was pointed at the reference index that exists, in the
  data overlay, and `CLAUDE_MD` was deleted from the module. The fixture and
  four tests this paragraph describes were retired in place at the foot of the
  file, with the reason recorded there.

Nothing here contacts api.anthropic.com. The header rejection is reproduced
against `http.client` directly, with no socket.

Run: python3 -m pytest tests/test_a_good_key_the_wizard_called_invalid.py
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def wk():
    return _load("scripts/wizard-verify-key.py", "wk_under_test")


@pytest.fixture(scope="module")
def wh():
    return _load("scripts/workspace-health.py", "wh_under_test")


# ============================================================
# The key the wizard called invalid without asking anyone
# ============================================================

@pytest.mark.parametrize("value,accepted", [
    ("good-key", True),
    (" padded-key ", True),
    ("with\nnewline", False),
    ("with\rreturn", False),
])
def test_the_header_rejection_this_is_about(value, accepted):
    """The mechanism, with no socket: `putrequest` only buffers."""
    conn = http.client.HTTPConnection("example.invalid")
    conn.putrequest("POST", "/")
    if accepted:
        conn.putheader("x-api-key", value)
    else:
        with pytest.raises(ValueError):
            conn.putheader("x-api-key", value)


@pytest.mark.parametrize("raw", [
    "sk-test-value\n",
    "\nsk-test-value",
    "  sk-test-value  ",
    "sk-test-value\r\n",
    "\tsk-test-value\t",
])
def test_surrounding_whitespace_is_stripped_before_any_request(wk, monkeypatch,
                                                                capsys, raw):
    """The ordinary case: `WIZARD_VERIFY_KEY=$(cat keyfile)` and a `.env` line
    both carry a trailing newline. Nothing goes to the network here: the
    verifier is a stand-in that records what it was handed."""
    seen = []
    monkeypatch.setattr(wk, "verify_anthropic",
                        lambda key: (seen.append(key), ("ok", "Key validated."))[1])
    monkeypatch.setenv("WIZARD_VERIFY_KEY", raw)

    rc = wk.main(["--provider", "anthropic"])

    assert rc == 0
    assert seen == ["sk-test-value"]
    capsys.readouterr()


@pytest.mark.parametrize("raw", ["sk-bad\nvalue", "sk-bad\rvalue", "sk-bad\x0bvalue"])
def test_a_control_character_inside_the_key_is_refused_not_misdiagnosed(
        wk, monkeypatch, capsys, raw):
    """Exit 1 means "the API said 401". Nothing was sent, so nothing said that.
    4 is the code this docstring reserves for a wrong invocation."""
    called = []
    monkeypatch.setattr(wk, "verify_anthropic",
                        lambda key: called.append(key) or ("ok", "x"))
    monkeypatch.setenv("WIZARD_VERIFY_KEY", raw)

    rc = wk.main(["--provider", "anthropic"])

    assert rc == 4
    assert called == [], "nothing may be sent for a key that cannot be a header"


def test_a_null_byte_in_the_key_is_refused_too(wk, monkeypatch, capsys):
    """Through `--key`, because the OS itself refuses a null byte in an
    environment variable, so this shape can only arrive on argv."""
    called = []
    monkeypatch.setattr(wk, "verify_anthropic",
                        lambda key: called.append(key) or ("ok", "x"))

    rc = wk.main(["--provider", "anthropic", "--key", "sk-bad\x00value"])

    assert rc == 4
    assert called == []
    capsys.readouterr()


@pytest.mark.parametrize("raw", ["sk-bad\nvalue", "sk-secret\rvalue"])
def test_the_refusal_never_echoes_the_key(wk, monkeypatch, capsys, raw):
    """The ValueError message contains the header value verbatim. That is a
    credential in stderr and in the wizard transcript."""
    monkeypatch.setattr(wk, "verify_anthropic", lambda key: ("ok", "x"))
    monkeypatch.setenv("WIZARD_VERIFY_KEY", raw)

    wk.main(["--provider", "anthropic"])

    captured = capsys.readouterr()
    body = captured.out + captured.err
    assert "sk-bad" not in body and "sk-secret" not in body
    assert "control character" in body


def test_an_all_whitespace_key_is_a_usage_error(wk, monkeypatch):
    """It used to become an empty header value and a pointless request."""
    monkeypatch.setenv("WIZARD_VERIFY_KEY", "   \n  ")

    with pytest.raises(SystemExit) as exc:
        wk.main(["--provider", "anthropic"])

    assert exc.value.code == 4


def test_the_verifier_itself_never_lets_a_value_error_escape(wk, monkeypatch):
    """Belt and braces behind the `main` guard, and it must not report
    `invalid`: exit 1 tells the wizard to send the operator back."""
    def _boom(*a, **k):
        raise ValueError("Invalid header value b'sk-secret\\n'")

    monkeypatch.setattr(wk.urllib.request, "urlopen", _boom)

    status, msg = wk.verify_anthropic("sk-secret")

    assert status == "unknown"
    assert "sk-secret" not in msg


def test_a_clean_key_still_reaches_the_verifier(wk, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(wk, "verify_anthropic",
                        lambda key: (seen.append(key), ("ok", "ok"))[1])
    monkeypatch.setenv("WIZARD_VERIFY_KEY", "sk-clean-value")

    assert wk.main(["--provider", "anthropic"]) == 0
    assert seen == ["sk-clean-value"]
    capsys.readouterr()


@pytest.mark.parametrize("status,code", [
    ("ok", 0), ("invalid", 1), ("rate_limited", 2), ("unknown", 3),
])
def test_the_documented_exit_codes_are_unchanged(wk, monkeypatch, capsys,
                                                  status, code):
    monkeypatch.setattr(wk, "verify_anthropic", lambda key: (status, "m"))
    monkeypatch.setenv("WIZARD_VERIFY_KEY", "sk-clean")

    assert wk.main(["--provider", "anthropic"]) == code
    capsys.readouterr()


def test_the_docstring_names_the_new_refusal(wk):
    assert "control character" in wk.__doc__


# ============================================================
# The BUILD.json that killed the whole health run
# ============================================================

@pytest.fixture
def corporate(wh, tmp_path, monkeypatch):
    """A stand-in corporate clone beside a stand-in workspace."""
    workspace = tmp_path / "engine"
    workspace.mkdir()
    (tmp_path / "heading-os-corporate").mkdir()
    monkeypatch.setattr(wh, "WORKSPACE", workspace)
    return tmp_path / "heading-os-corporate"


@pytest.mark.parametrize("body", ["[1, 2, 3]", '"a string"', "42", "null", "true"])
def test_a_non_object_build_json_is_a_finding_not_a_traceback(wh, corporate,
                                                               body, capsys):
    """`main` runs the checks in an unguarded loop, so this took the whole run
    down: no remaining sections, no summary, a traceback instead of a verdict."""
    (corporate / "BUILD.json").write_text(body, encoding="utf-8")

    issues = wh.check_build_sync()

    assert issues == 1
    assert "expected an object" in capsys.readouterr().out


def test_invalid_utf8_is_caught_too(wh, corporate, capsys):
    """`read_text` raises UnicodeDecodeError, a ValueError and not an OSError,
    so it escaped a handler that named OSError and JSONDecodeError."""
    (corporate / "BUILD.json").write_bytes(b'{"build": "\xff\xfe"}')

    issues = wh.check_build_sync()

    assert issues == 1
    assert "parse failed" in capsys.readouterr().out


def test_a_well_formed_build_json_still_passes(wh, corporate, capsys):
    (corporate / "BUILD.json").write_text(
        json.dumps({"build": 7, "timestamp": "2026-08-26T00:00:00Z"}),
        encoding="utf-8")

    assert wh.check_build_sync() == 0


def test_a_missing_build_json_is_still_only_information(wh, corporate):
    assert wh.check_build_sync() == 0


# ============================================================
# The pipeline section that counted one thing and named another
# ============================================================

@pytest.fixture
def pipeline(wh, tmp_path, monkeypatch):
    context = tmp_path / "context"
    context.mkdir()
    monkeypatch.setattr(wh, "CONTEXT_DIR", context)
    return context / "pipeline.md"


def test_placeholder_cells_are_counted(wh, pipeline, capsys):
    """`placeholder_count` was assigned and never read, so a table of nothing
    but placeholders passed the section green."""
    pipeline.write_text(
        "| Company | Stage |\n|---|---|\n| [PLACEHOLDER] | [PLACEHOLDER] |\n",
        encoding="utf-8")

    wh.check_pipeline_health()

    assert "2 table cell(s) hold only a [placeholder]" in capsys.readouterr().out


def test_empty_cells_are_counted(wh, pipeline, capsys):
    """Nothing anywhere examined them, though the comment promised it."""
    pipeline.write_text("| Company | Stage |\n|---|---|\n| Acme |  |\n",
                        encoding="utf-8")

    wh.check_pipeline_health()

    assert "1 empty table cell(s)" in capsys.readouterr().out


def test_a_complete_table_reports_neither(wh, pipeline, capsys):
    """The guard must not fire on a healthy pipeline."""
    pipeline.write_text("| Company | Stage |\n|---|---|\n| Acme | Won |\n",
                        encoding="utf-8")

    wh.check_pipeline_health()

    out = capsys.readouterr().out
    assert "placeholder]" not in out
    assert "empty table cell" not in out


def test_the_tbd_line_says_what_it_counted(wh, pipeline, capsys):
    """Two mentions of TBD in one prose sentence used to report as "2 TBD
    fields" over a file with zero table rows."""
    pipeline.write_text("Prose about TBD and more TBD here.\n", encoding="utf-8")

    wh.check_pipeline_health()

    out = capsys.readouterr().out
    assert "occurrence(s) anywhere" in out
    assert "TBD fields found" not in out


# ============================================================
# The clone that failed for having no private overlay
# ============================================================

def test_a_clone_with_no_overlay_is_not_a_docs_failure(wh, tmp_path,
                                                        monkeypatch, capsys):
    """Six ACTIONs and exit 1 on every public clone, where the sibling check
    calls the identical state legitimate."""
    monkeypatch.setattr(wh, "get_templates_dir", lambda: tmp_path / "absent")

    issues = wh.check_docs_sync()

    out = capsys.readouterr().out
    assert issues == 0
    assert "no data overlay" in out
    assert "ACTION" not in out


def test_a_file_missing_from_a_real_templates_tree_is_still_a_failure(
        wh, tmp_path, monkeypatch, capsys):
    """The narrowing must not become a way to stop noticing anything."""
    templates = tmp_path / "templates"
    templates.mkdir()
    monkeypatch.setattr(wh, "get_templates_dir", lambda: templates)

    issues = wh.check_docs_sync()

    assert issues > 0
    assert "missing from templates/" in capsys.readouterr().out


def test_the_two_siblings_agree_about_a_missing_overlay(wh, tmp_path,
                                                         monkeypatch, capsys):
    """The defect was the disagreement, so pin them together."""
    monkeypatch.setattr(wh, "get_templates_dir", lambda: tmp_path / "absent")

    assert wh.check_docs_sync() == 0
    assert wh.check_doc_versions() == 0



# ============================================================
# The remediation that was wrong whenever the section existed
# ============================================================
#
# A fixture and four tests stood here. They patched `wh.CLAUDE_MD` at a
# `tmp_path` file and pinned the difference between "no Reference Resources
# section" and "a section whose paths are not in backticks", because `checked
# == 0` could not tell those apart and the operator was told to add a section
# that was already there.
#
# All four passed for months over a heading that exists in no file in either
# repo. On 2026-08-30 `check_reference_validation` was pointed at
# `<data-root>/reference/workspace-overview.md`, the index that actually holds
# the paths, and `wh.CLAUDE_MD` was deleted, so the fixture had nothing left to
# patch.
#
# The distinction they protected survives, in a form that now matters: the new
# check counts path-shaped tokens it SKIPPED and prints that count beside the
# examined count, so "nothing to check" and "checked and clean" still read
# differently. See
# `tests/test_a_reference_check_that_verified_nothing_for_months.py`.
#
# ============================================================
