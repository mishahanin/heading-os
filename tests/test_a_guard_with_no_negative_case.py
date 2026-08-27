"""Five refusals that had never been made to happen.

A guard with only positive tests proves that the good input is accepted. It
proves nothing at all about the refusal, which is the entire reason the guard
exists: delete the branch and every test still passes. These five were found by
measuring coverage over the files that claim to test them, not by reading.

1. `census_schema.validate` is control #4 of the `/census` generated-code
   carve-out. Fifteen of its refusal statements were executed by no test:
   measured with `--cov-branch` over both files that import it, 83% with lines
   83, 103, 105, 110, 127, 131, 133, 150, 152, 155, 161, 169, 171, 195, 197
   missing.
2. `sandbox.run_sandboxed` refuses an `out_dir` that is air-gapped, because the
   writable mount would otherwise be the one path out of the box. No test drove
   it; `grep -rn "output directory" tests/` returned four hits and none was this.
3. `content_denylist._harvest_config` harvests Telegram-ID-shaped integers from
   config files whose NAME carries `fireside` or `roster`, and its comment
   states the scope claim: "An id sitting in some other data-config is not a
   denylist token." Both halves were untested; the `telegram-id` category is
   covered only through the separate roster harvest.
4. `proxy_transport` reports the FIRST failure alongside the last, a branch its
   own comment says was added after an hour of misdiagnosis. Nothing executed
   it, so the fix could regress in silence.
5. `osint-advanced-sync.validate_url` was tested only on its non-HTTP shortcut.
   Every refusal it can return - BLOCKED, ERROR, and the 405/501 GET retry the
   docstring exists to justify - was unreached.

Found by the third defect-class fan-out over `tests/`, 2026-08-27, lens
`a-guard-with-no-negative-case`. No production behaviour changes here: these are
the tests the five controls should have shipped with.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import census_schema as cs  # noqa: E402
from scripts.utils import sandbox  # noqa: E402
from scripts.utils.content_denylist import build_denylist  # noqa: E402


def _load(relpath: str, name: str):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / relpath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# 1. The census return schema
# ============================================================

_OK_SOURCES = ["notes/one.md"]


def _answer(**kw):
    base = {"kind": "count", "value": 1, "sources": list(_OK_SOURCES)}
    base.update(kw)
    return base


def test_a_well_formed_return_is_accepted():
    """The floor. Every refusal below is only meaningful against this."""
    assert cs.validate(_answer(), free_text_allowed=False) is None


@pytest.mark.parametrize("answer, fragment", [
    # shape of the envelope
    (["not", "a", "dict"], "must be a JSON object"),
    ("count", "must be a JSON object"),
    ({"kind": "tally", "sources": _OK_SOURCES}, "unknown kind"),
    ({"kind": None, "sources": _OK_SOURCES}, "unknown kind"),
    # the allowlist, not a blocklist
    ({"kind": "count", "value": 1, "sources": _OK_SOURCES, "note": "x" * 40},
     "that its shape does not define"),
    # sources
    ({"kind": "count", "value": 1}, "needs a 'sources' list"),
    ({"kind": "count", "value": 1, "sources": "notes/one.md"}, "needs a 'sources' list"),
    ({"kind": "count", "value": 1, "sources": []}, "'sources' is empty"),
    ({"kind": "count", "value": 1, "sources": [7]}, "entries must be strings"),
    ({"kind": "count", "value": 1, "sources": ["   "]}, "names nothing"),
    ({"kind": "count", "value": 1, "sources": ["a\nb.md"]}, "line break or NUL"),
    ({"kind": "count", "value": 1, "sources": ["/etc/shadow"]}, "is absolute"),
    ({"kind": "count", "value": 1, "sources": ["C:\\\\secrets"]}, "is absolute"),
    ({"kind": "count", "value": 1, "sources": ["../../../etc/shadow"]}, "escapes the corpus"),
    ({"kind": "count", "value": 1, "sources": ["x" * 600]}, "longer than"),
    # count
    ({"kind": "count", "value": "three", "sources": _OK_SOURCES}, "needs an integer value"),
    ({"kind": "count", "value": True, "sources": _OK_SOURCES}, "needs an integer value"),
    ({"kind": "count", "value": -1, "sources": _OK_SOURCES}, "cannot be negative"),
    # paths
    ({"kind": "paths", "paths": "a.md", "sources": _OK_SOURCES}, "needs a list of paths"),
    ({"kind": "paths", "paths": [None], "sources": _OK_SOURCES}, "entries must be strings"),
    ({"kind": "paths", "paths": ["/abs.md"], "sources": _OK_SOURCES}, "is absolute"),
    # pairs
    ({"kind": "pairs", "pairs": {"a": "b"}, "sources": _OK_SOURCES}, "needs a list of pairs"),
    ({"kind": "pairs", "pairs": [["only"]], "sources": _OK_SOURCES}, "2-element list"),
    ({"kind": "pairs", "pairs": [["a.md", 7]], "sources": _OK_SOURCES}, "must be strings"),
    ({"kind": "pairs", "pairs": [["a.md", "../b.md"]], "sources": _OK_SOURCES},
     "escapes the corpus"),
])
def test_every_refusal_states_its_reason(answer, fragment):
    """The validator returns the REASON, never a boolean, so assert the reason.

    A test that only asserted "not None" would pass against a validator that
    refuses everything with one message, which is the failure this shape avoids.
    """
    reason = cs.validate(answer, free_text_allowed=False)
    assert reason is not None, f"accepted: {answer!r}"
    assert fragment in reason, f"reason {reason!r} does not name {fragment!r}"


def test_kind_text_is_refused_without_the_free_text_opt_in():
    reason = cs.validate(
        {"kind": "text", "text": "hi", "provenance": cs.UNTRUSTED, "sources": _OK_SOURCES},
        free_text_allowed=False)
    assert reason and "the structured return is the control" in reason


@pytest.mark.parametrize("answer, fragment", [
    ({"kind": "text", "text": 7, "provenance": cs.UNTRUSTED, "sources": _OK_SOURCES},
     "needs a string text field"),
    ({"kind": "text", "text": "x" * (cs.MAX_TEXT_LEN + 1), "provenance": cs.UNTRUSTED,
      "sources": _OK_SOURCES}, "longer than"),
    ({"kind": "text", "text": "hi", "provenance": "trusted", "sources": _OK_SOURCES},
     "does not get to vouch for text"),
])
def test_the_text_channel_refuses_its_own_bad_shapes(answer, fragment):
    reason = cs.validate(answer, free_text_allowed=True)
    assert reason is not None and fragment in reason, reason


def test_a_structured_kind_cannot_smuggle_a_text_field():
    """`text` is not in `count`'s allowlist, so the allowlist refuses it first.

    Both refusals exist and only one can fire. Asserting the specific message
    records WHICH, so a later reorder that silences the second is visible.
    """
    reason = cs.validate(
        {"kind": "count", "value": 1, "text": "prose", "sources": _OK_SOURCES},
        free_text_allowed=True)
    assert reason and "that its shape does not define" in reason


@pytest.mark.parametrize("field, answer", [
    ("sources", {"kind": "count", "value": 1,
                 "sources": [f"n{i}.md" for i in range(cs.MAX_ENTRIES + 1)]}),
    ("paths", {"kind": "paths", "sources": _OK_SOURCES,
               "paths": [f"n{i}.md" for i in range(cs.MAX_ENTRIES + 1)]}),
])
def test_a_list_longer_than_the_entry_cap_is_refused(field, answer):
    reason = cs.validate(answer, free_text_allowed=False)
    assert reason and f"'{field}' carries" in reason and "the cap is" in reason


def test_a_list_under_the_entry_cap_can_still_be_too_much_text():
    """The per-entry cap bounded nothing; the TOTAL is the second gate."""
    entries = [("d" * 400) + f"/{i}.md" for i in range(30)]
    reason = cs.validate({"kind": "paths", "paths": entries, "sources": _OK_SOURCES},
                         free_text_allowed=False)
    assert reason and "characters; the cap is" in reason


# ============================================================
# 2. The sandbox output directory
# ============================================================

@pytest.fixture
def corpus(tmp_path):
    d = tmp_path / "corpus"
    d.mkdir()
    (d / "note.md").write_text("x", encoding="utf-8")
    return d


def test_an_air_gapped_output_directory_is_refused_before_anything_runs(tmp_path, corpus):
    """The writable mount is the ONE way out of the box, so it is judged too.

    A refusal that reads "air-gapped" only for the corpus would let the private
    tree be named as the destination instead, which is the same disclosure
    through the other argument.
    """
    out = tmp_path / "threads" / "personal" / "out"
    out.mkdir(parents=True)
    result = sandbox.run_sandboxed(
        program=Path("/bin/true"), corpus_paths=[corpus], out_dir=out)
    assert result.refused is not None
    assert result.refused.startswith("output directory:"), result.refused
    assert "air-gapped" in result.refused
    assert result.exit_code is None, "the box ran despite the refusal"


def test_a_secure_output_directory_is_refused(tmp_path, corpus):
    out = tmp_path / "_secure" / "out"
    out.mkdir(parents=True)
    result = sandbox.run_sandboxed(
        program=Path("/bin/true"), corpus_paths=[corpus], out_dir=out)
    assert result.refused is not None and "output directory:" in result.refused


def test_an_ordinary_output_directory_is_not_refused_for_being_one(tmp_path, corpus):
    """The negative of the negative: the guard must not refuse every out_dir.

    `refused` may still be set for a missing bubblewrap on this host, which is a
    different sentence and is what this asserts against.
    """
    out = tmp_path / "out"
    out.mkdir()
    result = sandbox.run_sandboxed(
        program=Path("/bin/true"), corpus_paths=[corpus], out_dir=out)
    assert "output directory:" not in (result.refused or "")


# ============================================================
# 3. The Telegram-ID harvest, and the scope it claims
# ============================================================

def _config_overlay(tmp_path, filename: str, payload: dict) -> Path:
    data = tmp_path / ".heading-os-data"
    (data / "config").mkdir(parents=True, exist_ok=True)
    (data / "config" / filename).write_text(json.dumps(payload), encoding="utf-8")
    # A second, always-present source so the denylist is never empty by accident.
    (data / "admin").mkdir(parents=True, exist_ok=True)
    (data / "admin" / "executives.json").write_text(
        json.dumps({"executives": [{"slug": "quill-marchetti"}]}), encoding="utf-8")
    return data


@pytest.mark.parametrize("filename", ["fireside-roster.json", "tribe-roster.yaml",
                                      "fireside-schedule.json"])
def test_an_id_in_a_fireside_or_roster_config_is_a_denylist_token(tmp_path, filename):
    data = _config_overlay(tmp_path, filename, {"members": [{"id": 481920377}]})
    dl = build_denylist(data)
    assert not dl.degraded
    assert dl.tokens.get("481920377") == "telegram-id"


def test_an_id_in_any_other_config_is_not_a_denylist_token(tmp_path):
    """The scope claim the comment makes, asserted.

    "An id sitting in some other data-config is not a denylist token." Without
    this row the harvester could widen to every config file and nothing would
    notice, and a nine-digit build number would start matching engine prose.
    """
    data = _config_overlay(tmp_path, "billing-settings.json", {"account": 481920377})
    dl = build_denylist(data)
    assert not dl.degraded and dl.tokens
    assert "481920377" not in dl.tokens


def test_an_email_is_harvested_from_every_config_file(tmp_path):
    """The other half of the same block, and it is deliberately UNSCOPED."""
    data = _config_overlay(
        tmp_path, "billing-settings.json", {"contact": "quill@example.invalid"})
    dl = build_denylist(data)
    assert dl.tokens.get("quill@example.invalid") == "email"


# ============================================================
# 4. The proxy transport's first-failure report
# ============================================================

def test_a_retry_sequence_that_changes_cause_reports_both(monkeypatch):
    """The branch its own comment says cost an hour of misdiagnosis.

    The proxy parks an auth in cooldown after the real refusal, so attempt 1
    carries the answer and attempts 2-4 carry only its consequence. Raising the
    LAST alone reported the consequence as the cause.
    """
    from scripts.utils import proxy_transport as pt

    calls = {"n": 0}
    messages = ["usage limit for this billing cycle", "auth_unavailable: no auth available"]

    class _Boom(Exception):
        pass

    def _attempt(*_a, **_k):
        i = calls["n"]
        calls["n"] += 1
        raise pt._TransientServerError(messages[0] if i == 0 else messages[1])

    monkeypatch.setattr(pt.time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError) as exc:
        pt._retry_server_errors(_attempt, 100, 30)
    text = str(exc.value)
    assert "first failure" in text and messages[0] in text
    assert "last failure" in text and messages[1] in text


def test_a_retry_sequence_with_one_cause_reports_it_once(monkeypatch):
    """The negative case: an unchanged cause must not be printed twice."""
    from scripts.utils import proxy_transport as pt

    def _attempt(*_a, **_k):
        raise pt._TransientServerError("upstream 503")

    monkeypatch.setattr(pt.time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError) as exc:
        pt._retry_server_errors(_attempt, 100, 30)
    text = str(exc.value)
    assert "first failure" not in text
    assert text.count("upstream 503") == 1


# ============================================================
# 5. The OSINT registry URL prober
# ============================================================

@pytest.fixture(scope="module")
def osint():
    return _load("scripts/osint-advanced-sync.py", "osint_advanced_sync_under_test")


def _probe_stub(behaviour):
    def _probe(url, verb):
        return behaviour(verb)
    return _probe


def test_a_head_refusal_retries_with_get_and_reports_working(osint, monkeypatch):
    """The retry the docstring exists to justify, executed for the first time.

    A server that refuses HEAD with 405 was reported BLOCKED: a healthy tool
    counted as broken, in the registry that decides which tools /osint reaches
    for.
    """
    def _behaviour(verb):
        if verb == "HEAD":
            raise HTTPError("u", 405, "Method Not Allowed", {}, None)
        return "WORKING", "HTTP 200 via GET (text/html)"

    monkeypatch.setattr(osint, "_probe", _probe_stub(_behaviour))
    assert osint.validate_url("https://example.invalid/x")[0] == "WORKING"


def test_a_get_retry_that_also_fails_reports_blocked_via_get(osint, monkeypatch):
    def _behaviour(verb):
        raise HTTPError("u", 405 if verb == "HEAD" else 404, "nope", {}, None)

    monkeypatch.setattr(osint, "_probe", _probe_stub(_behaviour))
    status, detail = osint.validate_url("https://example.invalid/x")
    assert status == "BLOCKED" and "via GET" in detail


def test_a_plain_403_is_blocked_without_a_second_probe(osint, monkeypatch):
    """Only 405 and 501 earn the retry. A 403 is an answer, not a method quirk."""
    seen = []

    def _probe(url, verb):
        seen.append(verb)
        raise HTTPError("u", 403, "Forbidden", {}, None)

    monkeypatch.setattr(osint, "_probe", _probe)
    status, detail = osint.validate_url("https://example.invalid/x")
    assert status == "BLOCKED" and "403" in detail
    assert seen == ["HEAD"], f"a second probe was issued: {seen}"


def test_a_connection_failure_is_an_error_not_a_block(osint, monkeypatch):
    """BLOCKED and ERROR are different verdicts: one is the site, one is us."""
    def _probe(url, verb):
        raise URLError("Name or service not known")

    monkeypatch.setattr(osint, "_probe", _probe)
    status, detail = osint.validate_url("https://example.invalid/x")
    assert status == "ERROR" and "Name or service" in detail


def test_a_github_repo_url_still_short_circuits_to_cli(osint):
    """The one branch that WAS tested, kept so the others cannot displace it."""
    assert osint.validate_url("https://github.com/someone/tool")[0] == "CLI"
    assert osint.validate_url("https://github.com/search?q=x")[0] != "CLI"
