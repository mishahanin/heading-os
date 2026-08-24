"""Shard 04-p1: two regexes with two line-ending policies, and a promise per file.

`crm-health.py` spells `\\r?\\n` into the OPENING frontmatter fence and left it
out of the closing one. On a CRLF contact the close line is `---\\r`, which
`[ \\t]*$` cannot match, so `frontmatter_end` returned -1 and
`--demote-candidates` reported "no frontmatter" about files whose frontmatter
was fine. `frontmatter_end` exists to guard the `status:` rewrite, so on those
files the guard was simply absent.

`_radar_insert_pos`, twelve lines below `frontmatter_end`'s docstring
explaining why a substring search is wrong, still used one:
`startswith("---\\n")` refuses `--- ` and `---\\r\\n`, and `find("\\n---", 3)`
matches any line merely beginning with `---`. Both fall through to "insert
after the first line", and on a file with frontmatter the first line IS the
opening fence -- so `--update` spliced the Contact Radar table into the YAML of
`people.md`.

`latest_verdicts` caught only `JSONDecodeError`. `null` on a line is valid JSON
that is not an object, so `.get` raised AttributeError -- after the new verdict
had already been appended, and on every run afterwards. The sibling
`council-models-notify.py` carries the same guard with a comment explaining it.

That sibling's own docstring promises a transient failure is "SWALLOWED (exit 0)
so the oneshot systemd unit is never left `failed`", and wrapped exactly one of
its four failure sites.

Finding 4 of the report is REFUTED here rather than fixed: see
`test_an_empty_last_touch_is_absent_not_unreadable`.

Tests: this file.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


crm = _load("crm-health", "crm_health_04p1")
cv = _load("council-record-verdict", "council_record_verdict_04p1")
cn = _load("council-models-notify", "council_models_notify_04p1")


# ==========================================================================
# 1 - the closing fence that only half read CRLF
# ==========================================================================

LF = "---\nstatus: active\nlast_touch: 2026-01-01\n---\n\nBody text.\n"
CRLF = LF.replace("\n", "\r\n")


def test_a_crlf_file_has_its_frontmatter_found():
    assert crm.frontmatter_end(CRLF) != -1, \
        "the open fence accepts CRLF and the close fence refused it"


def test_the_two_line_endings_find_the_same_fence():
    """Same document, same structure, same answer."""
    lf_before = LF[:crm.frontmatter_end(LF)]
    crlf_before = CRLF[:crm.frontmatter_end(CRLF)]
    assert lf_before.replace("\r", "") == crlf_before.replace("\r", "")


def test_a_crlf_fence_index_points_at_the_fence():
    idx = crm.frontmatter_end(CRLF)
    assert CRLF[idx:idx + 3] == "---"


def test_an_lf_file_is_unchanged():
    idx = crm.frontmatter_end(LF)
    assert LF[idx:idx + 3] == "---"
    assert "status: active" in LF[:idx]


def test_a_file_with_no_frontmatter_is_still_minus_one():
    assert crm.frontmatter_end("# Heading\n\n---\n\nA rule, not a fence.\n") == -1


def test_an_unclosed_frontmatter_is_still_minus_one():
    assert crm.frontmatter_end("---\nstatus: active\n") == -1


def test_a_dash_run_is_not_a_closing_fence():
    """`----` is a horizontal rule, not a fence."""
    text = "---\nstatus: active\n----\nstill frontmatter?\n---\n\nBody\n"
    idx = crm.frontmatter_end(text)
    assert text[idx:idx + 4] != "----", "a four-dash rule was read as the fence"


# ==========================================================================
# 2 - the radar table spliced into the YAML it was writing
# ==========================================================================

def _insert(content):
    pos = crm._radar_insert_pos(content)
    return content[:pos] + "## Contact Radar\n" + content[pos:]


@pytest.mark.parametrize("doc,label", [
    (LF, "lf"), (CRLF, "crlf"),
    ("--- \nstatus: active\n---\n\nBody\n", "trailing space on the opener"),
])
def test_the_radar_never_lands_inside_the_frontmatter(doc, label):
    out = _insert(doc)
    head, _, rest = out.replace("\r\n", "\n").partition("---\n")
    body_after_close = rest.split("\n---\n", 1)[-1]
    assert "## Contact Radar" in body_after_close, \
        f"the table was spliced into the YAML block ({label})"


def test_the_radar_never_displaces_line_one():
    """context-freshness.py reads ONLY line 1 for its stamp."""
    doc = "> Last verified: 2026-08-25\n\nBody\n"
    assert _insert(doc).splitlines()[0] == "> Last verified: 2026-08-25"


def test_a_file_without_frontmatter_gets_the_table_after_line_one():
    doc = "# People\n\nBody\n"
    lines = _insert(doc).splitlines()
    assert lines[0] == "# People"
    assert "## Contact Radar" in lines[1]


def test_a_single_line_file_appends_rather_than_prepends():
    assert _insert("# People").startswith("# People")


def test_a_dash_run_in_the_body_does_not_move_the_insert_point():
    doc = "# People\n\n----\n\nBody\n"
    assert _insert(doc).splitlines()[0] == "# People"


def test_the_insert_point_is_never_zero():
    for doc in (LF, CRLF, "# People\n", "# People", "---\nunclosed\n"):
        assert crm._radar_insert_pos(doc) != 0, f"would displace line 1 of {doc!r}"


# ==========================================================================
# 3 - the ledger line that killed every later run
# ==========================================================================

@pytest.mark.parametrize("bad", ["null", "[]", '"text"', "42", "true"])
def test_a_non_object_ledger_line_is_skipped(tmp_path, bad):
    path = tmp_path / "_verdicts.jsonl"
    path.write_text(
        json.dumps({"verdict_id": "t1", "choice": "mix"}) + "\n"
        + bad + "\n"
        + json.dumps({"verdict_id": "t2", "choice": "keep"}) + "\n",
        encoding="utf-8")
    out = cv.latest_verdicts(path)
    assert set(out) == {"t1", "t2"}, \
        f"a ledger line of {bad} took the whole tally down"


def test_a_malformed_line_is_still_skipped(tmp_path):
    path = tmp_path / "_verdicts.jsonl"
    path.write_text('{"verdict_id": "t1"}\n{not json\n', encoding="utf-8")
    assert set(cv.latest_verdicts(path)) == {"t1"}


def test_last_write_wins_is_unchanged(tmp_path):
    path = tmp_path / "_verdicts.jsonl"
    path.write_text(
        json.dumps({"verdict_id": "t1", "choice": "mix"}) + "\n"
        + json.dumps({"verdict_id": "t1", "choice": "keep"}) + "\n",
        encoding="utf-8")
    assert cv.latest_verdicts(path)["t1"]["choice"] == "keep"


def test_a_missing_ledger_is_empty(tmp_path):
    assert cv.latest_verdicts(tmp_path / "nope.jsonl") == {}


def test_a_record_without_a_verdict_id_is_ignored(tmp_path):
    path = tmp_path / "_verdicts.jsonl"
    path.write_text(json.dumps({"choice": "mix"}) + "\n", encoding="utf-8")
    assert cv.latest_verdicts(path) == {}


# ==========================================================================
# 4 - REFUTED: an empty last_touch is absent, not unreadable
# ==========================================================================

@pytest.mark.parametrize("value", ["", " null", " ~", ' ""'])
def test_an_empty_last_touch_is_absent_not_unreadable(value):
    """The report's Finding 4 rests on a premise this parser does not hold.

    It argued that `last_touch:` with no value YAML-parses to None, and
    `str(None)` is the truthy string "None", which would classify the contact
    "present and unreadable" and make `cmd_apply` skip it forever. The shared
    parser is not a YAML scalar parser: it returns strings, and every empty
    form below comes back as "". `str("")` is falsy, so the unreadable branch
    is never reached and the contact stays bumpable. No fix was applied.
    """
    from scripts.utils.markdown import parse_frontmatter_str
    fm, _ = parse_frontmatter_str(f"---\nlast_touch:{value}\nname: X\n---\nbody\n")
    raw = str(fm.get("last_touch", ""))
    assert raw.strip() == "", f"{value!r} parsed to {raw!r}, which would skip forever"


def test_a_real_last_touch_still_reads():
    from scripts.utils.markdown import parse_frontmatter_str
    fm, _ = parse_frontmatter_str("---\nlast_touch: 2026-01-15\n---\nbody\n")
    assert str(fm.get("last_touch", "")) == "2026-01-15"


# ==========================================================================
# 5 - the promise that covered one of four failure sites
# ==========================================================================

@pytest.fixture()
def notify_env(monkeypatch, tmp_path):
    monkeypatch.setattr(cn, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(cn, "load_env", lambda root: None)
    monkeypatch.setenv("COUNCIL_MODELS_TELEGRAM_TARGET", "12345")
    monkeypatch.setattr(cn, "_load_last_signature", list)
    monkeypatch.setattr(cn, "_save_signature", lambda sig: None)
    monkeypatch.setattr(sys, "argv", ["council-models-notify.py"])
    return monkeypatch


def _fake_freshness(monkeypatch, **attrs):
    """Install a stub freshness module.

    Patching `sys.modules` alone is not enough and looked like it was: `_run`
    does `from scripts.utils import council_freshness`, which reads the
    ATTRIBUTE off the already-imported package and never consults sys.modules.
    Four of the tests below passed against the REAL module, which happens to
    return no findings here and exits 0 for its own reasons -- they could not
    have failed. The package attribute is what the import statement reads.
    """
    import types

    import scripts.utils as utils_pkg
    attrs.setdefault("is_actionable", lambda finding: True)
    fake = types.SimpleNamespace(**attrs)
    # raising=False: the attribute only exists once something has imported
    # the submodule, and whether that has happened depends on test order.
    monkeypatch.setattr(utils_pkg, "council_freshness", fake, raising=False)
    monkeypatch.setitem(sys.modules, "scripts.utils.council_freshness", fake)
    return fake


def test_a_probe_failure_still_exits_zero(notify_env):
    def _boom():
        raise RuntimeError("xAI unreachable")

    _fake_freshness(notify_env, assess=_boom, nudge_line=lambda f: "x")
    assert cn.main() == 0


def test_a_nudge_line_failure_exits_zero(notify_env):
    def _boom(findings):
        raise ValueError("bad finding shape")

    _fake_freshness(notify_env, assess=lambda: [{"provider": "grok"}],
                    nudge_line=_boom)
    assert cn.main() == 0, "an unguarded nudge_line left the oneshot unit failed"


def test_a_partial_finding_exits_zero(notify_env):
    """_signature indexes f['status']; a finding without it must not fail."""
    _fake_freshness(notify_env, assess=lambda: [{"provider": "grok"}],
                    nudge_line=lambda f: "grok-4.6 available")
    assert cn.main() == 0, "a KeyError in _signature left the oneshot unit failed"


def test_a_raising_send_exits_zero(notify_env):
    _fake_freshness(notify_env,
                    assess=lambda: [{"provider": "grok", "status": "stale"}],
                    nudge_line=lambda f: "grok-4.6 available")

    def _boom(recipient, line):
        raise OSError("TLS handshake failed")

    notify_env.setattr(cn.telegram_notify, "notify", _boom)
    assert cn.main() == 0, "a raising send left the oneshot unit failed"


def test_a_clean_run_still_exits_zero_and_sends(notify_env):
    sent = {}
    _fake_freshness(notify_env,
                    assess=lambda: [{"provider": "grok", "status": "stale"}],
                    nudge_line=lambda f: "grok-4.6 available")
    notify_env.setattr(cn.telegram_notify, "notify",
                       lambda r, l: sent.update(to=r, line=l) or True)
    assert cn.main() == 0
    assert sent == {"to": "12345", "line": "grok-4.6 available"}


def test_an_unconfigured_target_attempts_no_send(notify_env):
    """The comment beside DEFAULT_RECIPIENT says no send is ever ATTEMPTED.

    Every earlier test here sets a target, so the mutation that deleted this
    guard could not fail any of them. Without the guard the script calls
    `notify("", line)` and leaves the decision to a module this file does not
    own -- which is the state that comment was written to end.
    """
    for var in ("COUNCIL_MODELS_TELEGRAM_TARGET", "OPS_RADAR_TELEGRAM_TARGET",
                "ODIN_CADENCE_TELEGRAM_TARGET"):
        notify_env.delenv(var, raising=False)
    calls = []
    _fake_freshness(notify_env,
                    assess=lambda: [{"provider": "grok", "status": "stale"}],
                    nudge_line=lambda f: "grok-4.6 available")
    notify_env.setattr(cn.telegram_notify, "notify",
                       lambda r, l: calls.append((r, l)) or True)
    assert cn.main() == 0
    assert calls == [], "a send was attempted with no target configured"


def test_a_bad_flag_is_still_a_cli_error(notify_env):
    """The boundary must not swallow argparse."""
    notify_env.setattr(sys, "argv", ["council-models-notify.py", "--nope"])
    with pytest.raises(SystemExit) as exc:
        cn.main()
    assert exc.value.code != 0
