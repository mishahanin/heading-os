#!/usr/bin/env python3
"""Nine tools that reported something other than what they did.

Shard `scripts-04-p3` of the 2026-08-23 engine audit, everything outside the
CRM migration (which has its own file). Three recurring shapes:

**A machine-readable stream with prose written into it.** `--json --baseline`
on `context-floor-audit.py` printed its verdict lines to stdout after the JSON
document, and `crm-health.py --json --update` did the same with its status
lines — in a file whose own comment, twenty lines earlier, explains that stdout
must stay clean because `crm_next.py` parses it.

**A match that was looser than the question.** `context7.py --version 1`
selected `v15.2.0`, because `"v1" in "v15.2.0"` is true and the substring
clause ran before any real `v1.x` entry could be reached: the operator asked
for major version 1 and silently got v15 docs. `crm-health.py`'s demote guard
was a substring test in front of an anchored replacement, so frontmatter
carrying `notes: status: pending` printed `[demoted]` over a byte-identical
rewrite. `parse_transcript` accepted any readable `.md` in the council
directory and rendered it as a pending verdict, inflating the count that feeds
the Phase-3b calibration gate.

**A contract stated and not kept.** `council-record-verdict.py` documents exit
3 for a missing transcript and returned 0, so a wrapper could not detect the
typo'd id the docstring describes. `council-models.py --show` decided whether a
model was a fallback by asking whether the config FILE exists, which is a
different question from whether it names that provider — a partial config
showed a baseline as a deliberate pin. `council-models-notify.py` carries a
comment saying an empty recipient means no send is ever attempted, and called
`notify("", line)` anyway.

And two that simply crash: `int(Retry-After)` on the RFC-permitted HTTP-date
form, and `_have()` shelling out to `bash` inside a Windows-safe script.

The one with the widest blast radius is `crm-health.py`'s radar-table
rewrite. Its removal regex required the old table to be followed by a rule or
by a `## ` heading not starting with C, so a table at end of file, or one
before `## CRM Pipeline`, was never removed and a second copy was appended on
every `--update`. Its insertion fallback wrote at byte 0, pushing the
`> Last verified:` marker off line 1 — which `context-freshness.py` reads and
nothing else. Two scripts in this workspace silently corrupted each other's
invariant.

Fixed 2026-08-24.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PY = str(ROOT / ".venv" / "bin" / "python")


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    mod = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec. `@dataclass` resolves a string annotation by
    # looking its own module up in `sys.modules`, so a module that is not there
    # yet raises `AttributeError: 'NoneType' object has no attribute '__dict__'`
    # inside dataclasses itself — which reads as a bug in the file under test.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _code_only(src: str) -> str:
    """The source with `#` comments removed.

    Every fix below explains itself in a comment that quotes the thing it
    removed, so a raw scan for the removed string fails on the fix's own
    explanation. Only code is evidence of behaviour.
    """
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if "  # " in line:
            line = line.split("  # ", 1)[0]
        out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# context-floor-audit.py — the JSON stream
# ---------------------------------------------------------------------------

def test_json_and_baseline_together_still_parse():
    """`--json --baseline` mixed prose into the document on the same stream."""
    proc = subprocess.run(
        [PY, str(ROOT / "scripts" / "context-floor-audit.py"), "--json", "--baseline"],
        cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert proc.stdout.strip(), "nothing on stdout at all"
    json.loads(proc.stdout)  # raises if a verdict line landed here


def test_the_baseline_verdict_is_still_reported():
    """Anchor: moving it to stderr must not silence it."""
    proc = subprocess.run(
        [PY, str(ROOT / "scripts" / "context-floor-audit.py"), "--json", "--baseline"],
        cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert "Floor" in proc.stderr or "baseline" in proc.stderr.lower(), (
        f"the verdict vanished instead of moving. stderr={proc.stderr!r}"
    )


# ---------------------------------------------------------------------------
# context7.py — Retry-After, and version selection
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def c7():
    return _load("c7_mod", "context7.py")


class _Resp:
    def __init__(self, status_code, headers=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def json(self):
        return {}


def test_an_http_date_retry_after_is_not_a_traceback(c7, capsys):
    """RFC 7231 permits a date here; `int()` on it was uncaught."""
    resp = _Resp(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    with pytest.raises(SystemExit) as exc:
        c7.handle_response(resp)
    assert exc.value.code == 1, "the clean rate-limit exit is the point"
    assert "Rate limited" in capsys.readouterr().err


def test_a_numeric_retry_after_still_reads_as_seconds(c7, capsys):
    """Anchor: the common case must keep its useful message."""
    with pytest.raises(SystemExit):
        c7.handle_response(_Resp(429, {"Retry-After": "30"}))
    assert "30s" in capsys.readouterr().err


def test_an_absent_retry_after_still_defaults(c7, capsys):
    with pytest.raises(SystemExit):
        c7.handle_response(_Resp(429, {}))
    assert "5s" in capsys.readouterr().err


VERSION_SRC = _code_only(
    (ROOT / "scripts" / "context7.py").read_text(encoding="utf-8"))


def test_the_comment_stripper_keeps_the_code():
    """Guard the premise: a stripper that ate everything passes every scan."""
    assert "def handle_response" in VERSION_SRC
    assert "# `or ver in v` clause" not in VERSION_SRC


def _select(ver: str, available: list[str]) -> str | None:
    """The selection loop as it stands in the file, run in isolation.

    Extracting it keeps the test on the LOGIC rather than on the surrounding
    argparse and network code; the anchor test below fails if the loop in the
    file stops matching this shape.
    """
    for v in available:
        if v == ver:
            return v
        if v.startswith(ver) and not v[len(ver):len(ver) + 1].isdigit():
            return v
    return None


def test_the_selection_loop_in_the_file_matches_this_one():
    """Guard the premise: a copy that drifts tests nothing."""
    assert "if v.startswith(ver) and not v[len(ver):len(ver) + 1].isdigit():" in VERSION_SRC
    assert "or ver in v" not in VERSION_SRC, (
        "the substring clause is back; it made --version 1 select v15.2.0"
    )


def test_major_version_one_does_not_select_fifteen():
    assert _select("v1", ["v15.2.0", "v1.4.0"]) == "v1.4.0", (
        "asking for major version 1 returned v15 docs, silently, because "
        '"v1" is a substring of "v15.2.0" and it came first in the list'
    )


def test_major_version_two_does_not_select_twelve():
    assert _select("v2", ["v12.0", "v2.1"]) == "v2.1"


def test_an_exact_version_still_matches():
    assert _select("v15.2.0", ["v15.2.0"]) == "v15.2.0"


def test_a_prefix_at_a_segment_boundary_still_matches():
    """`v1` must still find `v1.4.0`; only a digit continuation is refused."""
    assert _select("v1", ["v1.4.0"]) == "v1.4.0"
    assert _select("v1", ["v1"]) == "v1"


def test_no_match_is_still_no_match():
    assert _select("v9", ["v1.0", "v15.2.0"]) is None


# ---------------------------------------------------------------------------
# context-freshness.py — the stamp cannot leave the workspace
# ---------------------------------------------------------------------------

def test_stamp_refuses_a_path_outside_the_workspace(tmp_path):
    victim = tmp_path / "not-ours.md"
    victim.write_text("# untouched\n", encoding="utf-8")
    proc = subprocess.run(
        [PY, str(ROOT / "scripts" / "context-freshness.py"), "stamp", str(victim)],
        cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 1
    assert "outside the workspace" in proc.stdout + proc.stderr
    assert victim.read_text(encoding="utf-8") == "# untouched\n", (
        "a marker was written into a file outside the workspace"
    )


def test_stamp_refuses_a_non_markdown_file(tmp_path):
    """The marker is a markdown blockquote; on anything else it is corruption.

    The target is a scratch file INSIDE the workspace, never a real one. The
    first version of this test pointed at `pyproject.toml`, and when the
    mutation sweep disabled the guard the stamp landed on it for real: the test
    failed as designed and left the repo's build config with
    `> Last verified: ...` on line 1, which broke every later `ruff` run until
    it was restored by hand. A test that can damage the tree when it fails is a
    worse instrument than the defect it measures.

    The scratch must live INSIDE the workspace, because the guard under test is
    the one that refuses a path outside it, so `tmp_path` cannot be the target.
    It must not live under `tests/`, which is what the second version did:
    `scripts/dev/check-lfs-fixtures.py` walks that tree, and a parallel worker
    running this test made the walk list a file and then delete it mid-scan.
    `.tmp/` is gitignored and no guard walks it, and the per-worker suffix keeps
    two workers off each other's directory.
    """
    scratch_dir = ROOT / ".tmp" / f"freshness-probe-{tmp_path.name}"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    target = scratch_dir / "not-markdown.toml"
    target.write_text("key = 1\n", encoding="utf-8")
    try:
        proc = subprocess.run(
            [PY, str(ROOT / "scripts" / "context-freshness.py"), "stamp",
             str(target.relative_to(ROOT))],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        assert proc.returncode == 1
        assert "not a .md file" in proc.stdout + proc.stderr
        assert target.read_text(encoding="utf-8") == "key = 1\n"
    finally:
        # rmtree, not rmdir: when the guard under test regresses the stamp lands
        # on the target and rmdir raises on the non-empty directory, replacing
        # the assertion that names the regression with a cleanup error.
        shutil.rmtree(scratch_dir, ignore_errors=True)


def test_a_traversal_path_is_refused():
    proc = subprocess.run(
        [PY, str(ROOT / "scripts" / "context-freshness.py"), "stamp",
         "../../../etc/hosts"],
        cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 1
    assert "outside the workspace" in proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# council-aggregate.py — a stray file is not a transcript
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def council():
    return _load("council_mod", "council-aggregate.py")


def test_a_scratch_note_is_not_a_transcript(council, tmp_path):
    note = tmp_path / "scratch.md"
    note.write_text("# random notes\n\nnothing here\n", encoding="utf-8")
    assert council.parse_transcript(note) is None, (
        "any readable .md in the council directory became a pending verdict "
        "and inflated the count feeding the Phase-3b calibration gate"
    )


def test_a_readme_is_not_a_transcript(council, tmp_path):
    note = tmp_path / "README.md"
    note.write_text("# Council transcripts\n\nOne file per session.\n", encoding="utf-8")
    assert council.parse_transcript(note) is None


def test_a_real_transcript_still_parses(council, tmp_path):
    """Anchor: a guard that rejects everything is not a guard."""
    real = tmp_path / "2026-05-22_council_151429_topic.md"
    real.write_text(
        "---\nmode: debate\ntimestamp: 2026-05-22\n---\n\n"
        "# Always-on assistant\n\n"
        "## Question\n\nWhat should we do?\n",
        encoding="utf-8")
    parsed = council.parse_transcript(real)
    assert parsed is not None and parsed.mode == "debate"


def test_a_transcript_with_sections_but_no_frontmatter_parses(council, tmp_path):
    """Either signal is enough; requiring both would drop real transcripts."""
    real = tmp_path / "2026-05-22_council_151429_topic.md"
    real.write_text("# A topic\n\n## Question\n\nWhat should we do?\n",
                    encoding="utf-8")
    assert council.parse_transcript(real) is not None


# ---------------------------------------------------------------------------
# council-models.py — a fallback is per provider
# ---------------------------------------------------------------------------

def test_a_partial_config_marks_only_the_unpinned_providers(monkeypatch, tmp_path):
    from scripts.utils import council_models as cm
    cfg = tmp_path / "council-models.json"
    cfg.write_text(json.dumps({"grok": "grok-4.6"}), encoding="utf-8")
    monkeypatch.setattr(cm, "config_path", lambda: cfg)
    assert cm.is_fallback("grok") is False
    assert cm.is_fallback("gemini") is True, (
        "the file exists, so --show suppressed the (fallback) marker and the "
        "operator read a baseline as a deliberate pin"
    )
    assert cm.is_fallback("kimi") is True


def test_a_missing_config_marks_every_provider(monkeypatch, tmp_path):
    from scripts.utils import council_models as cm
    monkeypatch.setattr(cm, "config_path", lambda: tmp_path / "absent.json")
    assert all(cm.is_fallback(p) for p in cm.PROVIDERS)


def test_an_empty_pin_counts_as_a_fallback(monkeypatch, tmp_path):
    from scripts.utils import council_models as cm
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"grok": "   "}), encoding="utf-8")
    monkeypatch.setattr(cm, "config_path", lambda: cfg)
    assert cm.is_fallback("grok") is True
    assert cm.get_model("grok") == cm.FALLBACKS["grok"]


def test_an_unknown_provider_raises(monkeypatch):
    from scripts.utils import council_models as cm
    with pytest.raises(ValueError):
        cm.is_fallback("openai")


def test_show_asks_per_provider_not_per_file():
    src = (ROOT / "scripts" / "council-models.py").read_text(encoding="utf-8")
    assert "is_fallback(provider)" in src
    assert "not config_path().exists()" not in src, (
        "whether the FILE exists is a different question from whether it names "
        "this provider"
    )


# ---------------------------------------------------------------------------
# council-models-notify.py — no target means no attempt
# ---------------------------------------------------------------------------

def test_an_unconfigured_target_never_reaches_the_transport():
    src = (ROOT / "scripts" / "council-models-notify.py").read_text(encoding="utf-8")
    guard = src.split("or DEFAULT_RECIPIENT", 1)[1].split("telegram_notify.notify", 1)[0]
    assert "if not recipient:" in guard and "return 0" in guard, (
        "the comment beside DEFAULT_RECIPIENT promises no send is attempted "
        "with an empty target; the code called notify(\"\", line) and left the "
        "decision to a module this file does not own"
    )


# ---------------------------------------------------------------------------
# council-record-verdict.py — the documented exit code
# ---------------------------------------------------------------------------

def test_a_missing_transcript_exits_three(tmp_path, monkeypatch):
    verdict = _load("verdict_mod", "council-record-verdict.py")
    monkeypatch.setattr(verdict, "COUNCIL_DIR", tmp_path)
    monkeypatch.setattr(verdict, "VERDICTS_PATH", tmp_path / "verdicts.jsonl")
    rc = verdict.main(["--id", "no-such-transcript", "--choice", "mix"])
    assert rc == 3, (
        "the docstring contracts exit 3 for exactly this, and a wrapper using "
        "it to catch a typo'd id got 0"
    )
    assert (tmp_path / "verdicts.jsonl").exists(), (
        "the docstring also says the verdict is STILL written"
    )


def test_a_present_transcript_exits_zero(tmp_path, monkeypatch):
    verdict = _load("verdict_mod2", "council-record-verdict.py")
    monkeypatch.setattr(verdict, "COUNCIL_DIR", tmp_path)
    monkeypatch.setattr(verdict, "VERDICTS_PATH", tmp_path / "verdicts.jsonl")
    (tmp_path / "real-id.md").write_text("# t\n", encoding="utf-8")
    assert verdict.main(["--id", "real-id", "--choice", "mix"]) == 0


# ---------------------------------------------------------------------------
# create-data-repo.py — portable, and one working directory
# ---------------------------------------------------------------------------

def test_the_tool_check_does_not_shell_out_to_bash():
    src = (ROOT / "scripts" / "create-data-repo.py").read_text(encoding="utf-8")
    assert 'run(["bash", "-lc", f"command -v' not in src, (
        "on a Windows host with no Git-bash the PRECONDITION CHECK itself died "
        "on FileNotFoundError instead of printing 'git is not installed'"
    )
    assert "shutil.which(tool)" in src


def test_the_tool_check_still_answers_correctly():
    cdr = _load("cdr_mod", "create-data-repo.py")
    assert cdr._have("git") is True
    assert cdr._have("definitely-not-a-real-binary-xyz") is False


def test_a_relative_target_is_resolved_to_one_absolute_path():
    src = (ROOT / "scripts" / "create-data-repo.py").read_text(encoding="utf-8")
    assert 'target = Path(args.path).expanduser().resolve()' in src, (
        "the scaffold subprocess runs with cwd=workspace_root while the git "
        "commands run in the caller's cwd, so a relative --path built the tree "
        "in one directory and the repo in another"
    )


# ---------------------------------------------------------------------------
# crm-backfill-exchange.py — dates compared as dates
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def backfill():
    return _load("backfill_mod", "crm-backfill-exchange.py")


def test_a_quoted_stored_date_is_read_as_a_date(backfill):
    """A leading quote sorts BELOW every digit, so every run proposed a bump."""
    assert backfill._stored_date(' "2099-01-01"') == "2099-01-01"


def test_a_datetime_stored_value_is_read_as_its_date(backfill):
    assert backfill._stored_date(" 2026-04-01T09:00:00") == "2026-04-01"


def test_a_bare_date_is_unchanged(backfill):
    assert backfill._stored_date(" 2026-04-01") == "2026-04-01"


def test_an_unreadable_stored_date_reads_as_unset(backfill, capsys):
    assert backfill._stored_date(" last week") == ""
    assert "unreadable last_touch" in capsys.readouterr().err, (
        "silently trusting an unparseable value is how the wrong list looked "
        "plausible"
    )


def test_a_newer_stored_date_now_suppresses_the_bump(backfill):
    """The actual defect: quoted 2099 compared BELOW today and got bumped."""
    assert backfill._stored_date(' "2099-01-01"') > "2026-08-24"


# ---------------------------------------------------------------------------
# crm-health.py — the radar table
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def health():
    return _load("health_mod", "crm-health.py")


def _people(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "people.md"
    p.write_text(body, encoding="utf-8")
    return p


CONTACT = [{
    "name": "James Bond", "company": "Universal Exports", "type": "partner",
    "last_touch": "2026-08-01", "health": "green", "file": "james-bond.md",
}]


def test_a_radar_table_at_end_of_file_is_replaced_not_duplicated(health, tmp_path,
                                                                 monkeypatch):
    body = ("> Last verified: 2026-08-01\n\n# People\n\n"
            "## Contact Radar\n\nold table\n")
    path = _people(tmp_path, body)
    monkeypatch.setattr(health, "PEOPLE_FILE", path)
    health.update_people_md(CONTACT)
    health.update_people_md(CONTACT)
    text = path.read_text(encoding="utf-8")
    assert text.count("## Contact Radar") == 1, (
        "the removal lookahead needed a following rule or a non-C heading, so "
        "a table at EOF was never removed and one more stacked on every run"
    )


def test_a_radar_table_before_a_c_heading_is_replaced(health, tmp_path, monkeypatch):
    body = ("> Last verified: 2026-08-01\n\n# People\n\n"
            "## Contact Radar\n\nold table\n\n## CRM Pipeline\n\nrows\n")
    path = _people(tmp_path, body)
    monkeypatch.setattr(health, "PEOPLE_FILE", path)
    health.update_people_md(CONTACT)
    health.update_people_md(CONTACT)
    text = path.read_text(encoding="utf-8")
    assert text.count("## Contact Radar") == 1
    assert "## CRM Pipeline" in text, "the following section was eaten"


def test_the_last_verified_marker_stays_on_line_one(health, tmp_path, monkeypatch):
    """No `---` anywhere: the old else-branch wrote the table at byte 0."""
    body = "> Last verified: 2026-08-01\n\n# People\n\nsome prose\n"
    path = _people(tmp_path, body)
    monkeypatch.setattr(health, "PEOPLE_FILE", path)
    health.update_people_md(CONTACT)
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("> Last verified:"), (
        f"line 1 is now {first!r}; context-freshness.py reads ONLY line 1, so "
        "the file reports as unstamped and the next stamp adds a second marker"
    )


def test_frontmatter_is_not_displaced_either(health, tmp_path, monkeypatch):
    body = "---\ntitle: People\n---\n\n# People\n\nprose\n"
    path = _people(tmp_path, body)
    monkeypatch.setattr(health, "PEOPLE_FILE", path)
    health.update_people_md(CONTACT)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\ntitle: People\n---\n")
    assert "## Contact Radar" in text


def test_the_table_is_actually_written(health, tmp_path, monkeypatch):
    """Anchor: a removal that also removed the new table would pass the counts."""
    path = _people(tmp_path, "> Last verified: 2026-08-01\n\n# People\n")
    monkeypatch.setattr(health, "PEOPLE_FILE", path)
    assert health.update_people_md(CONTACT) is True
    assert "James Bond" in path.read_text(encoding="utf-8")


def test_the_update_status_line_stays_off_stdout():
    src = (ROOT / "scripts" / "crm-health.py").read_text(encoding="utf-8")
    block = src.split("if args.update:", 1)[1].split("if args.demote_candidates:", 1)[0]
    assert block.count("file=sys.stderr") == 2, (
        "`--json --update` printed these lines after the JSON on stdout; the "
        "comment twenty lines up already knew stdout must stay parseable"
    )


def test_the_demote_guard_is_anchored_like_its_replacement():
    src = (ROOT / "scripts" / "crm-health.py").read_text(encoding="utf-8")
    assert '_re.search(r"^status:", frontmatter, _re.MULTILINE)' in src, (
        "a substring guard in front of an anchored re.sub printed [demoted] "
        "over a byte-identical rewrite"
    )
    assert 'if "status:" in frontmatter:' not in src


def test_the_no_frontmatter_skip_does_not_assume_a_slug():
    src = (ROOT / "scripts" / "crm-health.py").read_text(encoding="utf-8")
    assert 'label = c.get("slug", c["file"])' in src, (
        "a hard c['slug'] index crashed mid-demote, after some contacts had "
        "already been rewritten and with no rollback"
    )
