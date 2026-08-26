#!/usr/bin/env python3
"""Shard scripts-09-p2: values that were fine and words that were not.

Most of this shard is one shape: the computation is right and the thing said
about it is wrong.

  - `odin-cadence` reports `age_days` -- which its own compute docstring spends
    a paragraph establishing is the wait of the OLDEST unreviewed episode, and
    explicitly condemns reading as the newest -- under the label "newest".
  - The same report calls the tag UNION "shared tags". With transitive A-B-C
    clustering, usually none of them are shared by every member. The union is
    pinned by an existing test, so it stays: the LABEL changed, and the honest
    intersection is carried beside it.
  - `odin-cadence`'s docstring promised "Exit 0 always" while its file I/O was
    unguarded, and `odin-cadence-notify` then read any crash as "up to date".
  - `mullvad-fastest` printed "Fastest: <host> @ None ms" when nothing had
    responded, because `results[0]` sorted on `inf`.

Two are worse than labels:

  - `odin-brain-health --update-index` raised TypeError on any brain holding one
    dated note and one undated one: `yaml.safe_load` gives `datetime.date` and a
    missing field gives the string "unknown", and Python 3 will not order them.
  - `mullvad-fastest` sent `ping -W 2` on macOS, where `-W` is MILLISECONDS. A
    2ms reply window means nothing responds and the ranking is silently garbage.

And `odin_pagerank` relativised data-root paths against the ENGINE root, so
every one fell through to an absolute path -- into JSON output, and into the
air-gap check that expects a relative one.

Run: .venv/bin/python -m pytest tests/test_a_label_that_names_the_opposite_metric.py -q
"""

import datetime
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


obh = _load("odin_brain_health_p9b", "scripts/odin-brain-health.py")
oc = _load("odin_cadence_p9b", "scripts/odin-cadence.py")
mv = _load("mullvad_fastest_p9b", "scripts/mullvad-fastest.py")

from scripts.odin_pagerank import FRONTMATTER_RE, build_graph  # noqa: E402


# ============================================================
# 1 - a date and a missing date can be sorted together
# ============================================================
def test_a_date_and_the_string_unknown_sort_without_raising():
    rows = [(datetime.date(2026, 8, 20), "dated"), ("unknown", "undated")]
    rows.sort(key=lambda x: obh._date_key(x[0]), reverse=True)
    assert [r[1] for r in rows] == ["dated", "undated"]


def test_a_datetime_and_a_date_sort_against_each_other():
    """A YAML timestamp with a time component becomes `datetime`, which cannot
    be ordered against `date` either -- the same crash with no field missing."""
    rows = [(datetime.date(2026, 8, 20), "d"),
            (datetime.datetime(2026, 8, 21, 9, 0), "dt")]  # noqa: DTZ001 - naive on purpose
    rows.sort(key=lambda x: obh._date_key(x[0]), reverse=True)
    assert [r[1] for r in rows] == ["dt", "d"]


def test_an_iso_string_sorts_with_real_dates():
    rows = [("2026-08-19", "s"), (datetime.date(2026, 8, 20), "d")]
    rows.sort(key=lambda x: obh._date_key(x[0]), reverse=True)
    assert [r[1] for r in rows] == ["d", "s"]


@pytest.mark.parametrize("bad", ["unknown", "", None, "n/a"])
def test_an_unparseable_date_sorts_to_the_bottom(bad):
    assert obh._date_key(bad) == ""
    assert obh._date_key(datetime.date(2026, 1, 1)) > obh._date_key(bad)


def test_isoformat_and_str_order_a_datetime_differently():
    """Not interchangeable: `str(datetime)` uses a SPACE separator (0x20) and
    isoformat uses 'T' (0x54), so against an ISO string on the same day they
    sort opposite ways. `str()` happens to equal isoformat for a bare date,
    which is what makes the difference easy to miss."""
    dt = datetime.datetime(2026, 8, 21, 9, 0)  # noqa: DTZ001 - naive is the case under test
    same_day = "2026-08-21T08:00:00"
    assert (str(dt) > same_day) != (dt.isoformat() > same_day)
    assert obh._date_key(dt) == dt.isoformat()


def test_generate_index_survives_a_brain_with_mixed_date_types(tmp_path):
    """The real sort path, not `_date_key` in isolation. Every test above calls
    the helper directly, so reverting the CALL SITE to `key=lambda x: x[0]`
    left them all green while `--update-index` went back to raising TypeError.
    """
    brain = tmp_path / "brain"
    for sub in ("sources", "principles", "positions", "episodes", "conflicts"):
        (brain / sub).mkdir(parents=True)
    (brain / "sources" / "dated.md").write_text(
        "---\ntitle: Dated\ningested: 2026-08-20\n---\nbody\n", encoding="utf-8")
    (brain / "sources" / "undated.md").write_text(
        "---\ntitle: Undated\n---\nbody\n", encoding="utf-8")
    (brain / "episodes" / "stamped.md").write_text(
        "---\ntitle: Stamped\ndate: 2026-08-21 09:00:00\n---\nbody\n",
        encoding="utf-8")
    files = {
        "sources": sorted((brain / "sources").glob("*.md")),
        "principles": [], "positions": [],
        "episodes": sorted((brain / "episodes").glob("*.md")),
        "conflicts": [],
    }
    out = obh.generate_index(files)
    assert "Dated" in out and "Undated" in out and "Stamped" in out
    assert out.index("Stamped") < out.index("Dated") < out.index("Undated"), out


# ============================================================
# 2 - ping's -W means different things on different systems
# ============================================================
@pytest.mark.parametrize("system,expect_ms", [
    ("Darwin", True),
    ("Linux", False),
])
def test_the_ping_timeout_unit_matches_the_platform(system, expect_ms, monkeypatch):
    seen = {}

    class _Res:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(mv.platform, "system", lambda: system)
    monkeypatch.setattr(mv.subprocess, "run",
                        lambda cmd, **k: seen.update(cmd=cmd) or _Res())
    mv.ping_host("1.1.1.1", count=3, timeout_s=2)
    cmd = seen["cmd"]
    value = cmd[cmd.index("-W") + 1]
    assert value == ("2000" if expect_ms else "2"), (system, cmd)


def test_windows_still_uses_its_own_flag(monkeypatch):
    seen = {}

    class _Res:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(mv.platform, "system", lambda: "Windows")
    monkeypatch.setattr(mv.subprocess, "run",
                        lambda cmd, **k: seen.update(cmd=cmd) or _Res())
    mv.ping_host("1.1.1.1", count=3, timeout_s=2)
    assert "-w" in seen["cmd"] and "2000" in seen["cmd"]


# ============================================================
# 3 - no responders means no fastest
# ============================================================
def test_nothing_reachable_does_not_name_a_winner():
    src = (ROOT / "scripts" / "mullvad-fastest.py").read_text(encoding="utf-8")
    body = src.split("reachable = sum(", 1)[1][:900]
    assert "if reachable == 0:" in body, body
    guard = body.index("if reachable == 0:")
    first_use = body.index("fastest = results[0]")
    assert guard < first_use, "results[0] is read before the guard"


def test_a_non_json_relay_list_raises_the_caught_type(monkeypatch):
    """A 200 carrying HTML escaped past HTTPError/URLError as a traceback. `main`
    catches RuntimeError, so that is the type this must raise -- asserted by
    CALLING it, because the function holds a second `raise RuntimeError` that a
    source grep finds either way."""
    class _Resp:
        def read(self):
            return b"<html>captive portal</html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mv.urllib.request, "urlopen", lambda *a, **k: _Resp())
    with pytest.raises(RuntimeError, match="not JSON"):
        mv.fetch_relays()


def test_a_json_relay_list_is_returned(monkeypatch):
    class _Resp:
        def read(self):
            return b'[{"hostname": "x", "type": "wireguard"}]'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mv.urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert mv.fetch_relays() == [{"hostname": "x", "type": "wireguard"}]


# ============================================================
# 4 - the cadence report says what it measured
# ============================================================
def _cluster_root(tmp_path, episodes):
    """The layout analyze_reflect_clusters walks, matching tests/test_odin_cadence."""
    brain = tmp_path / "knowledge" / "odin-brain"
    (brain / "episodes").mkdir(parents=True)
    (brain / ".last-collect").write_text("2026-08-24\n", encoding="utf-8")
    for i, tags in enumerate(episodes):
        (brain / "episodes" / f"e{i}.md").write_text(
            f'---\nid: "e{i}"\ntype: episode\ndate: 2026-05-21\n'
            f"entities: [{', '.join(tags)}]\nkeywords: [{', '.join(tags)}]\n"
            f"status: raw\n---\n\n# e{i}\n",
            encoding="utf-8")
    return tmp_path


def test_the_report_calls_the_oldest_wait_the_oldest(tmp_path):
    src = (ROOT / "scripts" / "odin-cadence.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "| newest {age}" not in code, code
    assert "oldest unreviewed" in code


def test_the_union_keeps_its_pinned_name_and_gains_an_honest_sibling(tmp_path):
    """`shared_tags` is the UNION and an existing test pins that, so the value
    stays. What was wrong is that nothing carried the real intersection and the
    report called the union 'shared'."""
    root = _cluster_root(tmp_path, [["acme", "bob", "mnda"],
                                    ["acme", "bob", "mnda", "demo"]])
    out = oc.analyze_reflect_clusters(root, datetime.date(2026, 8, 24))
    cluster = out["clusters"][0]
    assert set(cluster["shared_tags"]) == {"acme", "bob", "mnda", "demo"}
    assert set(cluster["common_tags"]) == {"acme", "bob", "mnda"}


def test_a_cluster_with_nothing_in_common_says_so(tmp_path):
    """Transitive membership: A-B share one tag, B-C share another, so the
    cluster's true intersection can be empty while the union is large."""
    root = _cluster_root(tmp_path, [["a", "x"], ["a", "b", "x"], ["b", "y"]])
    out = oc.analyze_reflect_clusters(root, datetime.date(2026, 8, 24))
    if out["clusters"]:
        cluster = out["clusters"][0]
        assert len(cluster["common_tags"]) < len(cluster["shared_tags"])


# ============================================================
# 5 - a days-only nudge is a sentence
# ============================================================
def _nudge(total=0, clusters=0, days=8):
    return {
        "nudge": True, "days_since": days, "unharvested_total": total,
        "by_source": {"thread": 0, "crm": 0, "viraid": 0},
        "reflect_clusters": clusters, "stale_clusters": 0,
    }


def test_a_days_only_nudge_does_not_end_in_run_nothing():
    """No new entries and no clusters left `tail` empty, and this line -- sent
    verbatim to Telegram -- rendered as "... - cadence due. Run ."."""
    line = oc.suggestion_line(_nudge())
    assert "Run ." not in line, line
    assert not line.rstrip().endswith("Run"), line
    assert line.endswith("."), line


def test_a_nudge_with_work_still_names_the_command():
    """The guard must not swallow the call to action when there IS one."""
    line = oc.suggestion_line(_nudge(total=3))
    assert "/odin collect" in line, line


# ============================================================
# 6 - a corrupt marker counts everything, not nothing
# ============================================================
def test_an_unparseable_marker_reads_as_no_marker(tmp_path):
    """Returned TRUTHY, the garbage string became the lexicographic `since`
    floor, so every entry compared False and all un-harvested counts read 0 --
    beside a reason saying "never collected"."""
    (tmp_path / oc.MARKER).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / oc.MARKER).write_text("not-a-date\n", encoding="utf-8")
    raw, days = oc.read_marker(tmp_path)
    assert raw is None and days is None


def test_a_valid_marker_still_parses(tmp_path):
    (tmp_path / oc.MARKER).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / oc.MARKER).write_text("2026-08-01\n", encoding="utf-8")
    raw, days = oc.read_marker(tmp_path)
    assert raw == "2026-08-01"
    assert isinstance(days, int) and days >= 0


def test_an_absent_marker_is_still_none(tmp_path):
    assert oc.read_marker(tmp_path / "nope") == (None, None)


# ============================================================
# 7 - the exit-code claim matches the code
# ============================================================
def test_the_docstring_no_longer_promises_exit_0_unconditionally():
    src = (ROOT / "scripts" / "odin-cadence.py").read_text(encoding="utf-8")
    doc = src.split('"""', 2)[1]
    assert "Exit 0 always." not in doc, doc
    assert "OSError" in doc


def test_the_notifier_distinguishes_a_crash_from_up_to_date():
    src = (ROOT / "scripts" / "odin-cadence-notify.py").read_text(encoding="utf-8")
    body = src.split("cmd = [sys.executable, str(cadence)", 1)[1][:1200]
    assert "if proc.returncode != 0:" in body, body
    assert body.index("returncode != 0") < body.index("up to date")


# ============================================================
# 8 - "never raises" holds
# ============================================================
def test_the_brain_snapshot_survives_a_file_vanishing(tmp_path, monkeypatch):
    """`is_file()` swallows OSError and returns False, so patching only `stat`
    made this test never reach the guard -- it passed with the guard removed.
    `is_file` is forced True so the vanish lands where it actually landed in
    production: between the rglob and the stat."""
    ocn = _load("odin_cadence_notify_p9b", "scripts/odin-cadence-notify.py")
    brain = tmp_path / "odin-brain"
    brain.mkdir()
    (brain / "a.md").write_text("x", encoding="utf-8")
    (brain / "b.md").write_text("y", encoding="utf-8")
    real_stat = Path.stat

    def flaky(self, *a, **k):
        if self.name == "a.md":
            raise FileNotFoundError(2, "gone")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(Path, "stat", flaky)
    snap = ocn._brain_snapshot(brain)
    assert list(snap) == ["b.md"], snap


def test_the_brain_snapshot_reads_every_readable_file(tmp_path):
    ocn = _load("odin_cadence_notify_p9b2", "scripts/odin-cadence-notify.py")
    brain = tmp_path / "odin-brain"
    brain.mkdir()
    (brain / "a.md").write_text("x", encoding="utf-8")
    assert list(ocn._brain_snapshot(brain)) == ["a.md"]


def test_the_propose_import_failure_is_reported_not_raised():
    src = (ROOT / "scripts" / "odin-cadence-notify.py").read_text(encoding="utf-8")
    body = src.split("def _run_headless_propose", 1)[1].split("\ndef ", 1)[0]
    assert "except ImportError" in body, body
    imp = body.index("from scripts.heading_cli import")
    assert body.rindex("try:", 0, imp) > 0, "the import is still unguarded"


# ============================================================
# 9 - a graph path stays relative to the tree it came from
# ============================================================
def test_a_brain_outside_the_engine_root_yields_relative_paths(tmp_path):
    """`relative_to(engine_root)` raised for EVERY note in a data-root brain, so
    the fallback put an absolute path into output and into the air-gap check."""
    brain = tmp_path / "data" / "knowledge" / "odin-brain"
    brain.mkdir(parents=True)
    (brain / "note.md").write_text(
        "---\nid: note\ntags: [x]\n---\nSee [[other]].\n", encoding="utf-8")
    engine = tmp_path / "engine"
    engine.mkdir()
    g = build_graph(brain, engine)
    # `rel` is what reaches the output: line ~360 emits `"path": meta["rel"]`.
    rels = [n["rel"] for n in g.nodes.values()]
    assert rels, "no nodes were built"
    for rel in rels:
        assert not rel.startswith("/"), rel
        assert str(tmp_path) not in rel, rel
    assert rels == ["note.md"], rels


# ============================================================
# 10 - frontmatter closing at EOF is still frontmatter
# ============================================================
def test_frontmatter_ending_at_eof_is_stripped():
    text = "---\ntags: [[x]]\n---"
    assert FRONTMATTER_RE.sub("", text, count=1) == ""


def test_frontmatter_with_a_trailing_newline_is_unaffected():
    text = "---\ntags: [[x]]\n---\nbody\n"
    assert FRONTMATTER_RE.sub("", text, count=1) == "body\n"


def test_a_note_with_no_frontmatter_is_untouched():
    text = "Just a body with [[a link]].\n"
    assert FRONTMATTER_RE.sub("", text, count=1) == text


def test_the_dead_pagerank_config_key_is_gone():
    from scripts.odin_pagerank import _DEFAULT_PAGERANK_CFG
    assert "seed_threshold" not in _DEFAULT_PAGERANK_CFG


# ============================================================
# 11 - an ignored flag says so
# ============================================================
def test_stage_with_keywords_warns_that_it_does_nothing():
    """Run it: the message string survives disabling the branch it sits in."""
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "odin-principles.py"),
         "--keywords", "acme,bob", "--stage", "Negotiation", "--json"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    assert "has no effect with --keywords" in proc.stderr, proc.stderr


def test_keywords_alone_warns_about_nothing():
    """The silent half of the pair above.

    Silence alone is not evidence: a script that crashed prints no warning
    either. So the run must also show it completed and produced its `--json`
    output, which is what proves the quiet came from the branch and not from a
    dead process.
    """
    import json
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "odin-principles.py"),
         "--keywords", "acme,bob", "--json"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)

    assert "has no effect" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert isinstance(json.loads(proc.stdout), list), proc.stdout[:500]


def test_a_workspace_without_the_brain_degrades_cleanly(tmp_path):
    """The console-first degrade for an exec workspace, which this machine can
    never reach on its own: the Odin brain IS present here, so the branch stays
    dark in every other test. Found while re-aiming a mutation that landed on
    it and survived.

    A caller parsing `--json` must still get a list, and the exit must stay 0 -
    an absent brain is a missing capability, not a failure.
    """
    import json
    import os
    import subprocess
    (tmp_path / "knowledge").mkdir()

    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "odin-principles.py"),
         "--keywords", "acme", "--json"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60,
        env=dict(os.environ, HEADING_OS_DATA=str(tmp_path)))

    assert proc.returncode == 0, proc.stderr[-1500:]
    assert json.loads(proc.stdout) == [], proc.stdout[:500]
    assert "Odin brain not present" in proc.stderr, (
        f"the degrade was silent, so a caller cannot tell an empty result from "
        f"an absent brain: {proc.stderr[-500:]}")
