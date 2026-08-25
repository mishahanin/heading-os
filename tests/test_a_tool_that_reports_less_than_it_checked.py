#!/usr/bin/env python3
"""Fourteen small tools, one shape: the report and the run disagreed.

Found by the 2026-08-23 engine audit, shard `scripts-04-p2`. None of these is a
crash on the happy path. Every one of them produces a plausible answer that is
not the answer, which is the failure mode `.claude/rules/scope-claims.md` names.

* **`chronicle.py`** advanced its high-water marker past a session whose
  summarization had FAILED, because only the failure branch declined to advance
  while both skip branches did — and sessions are walked newest-first. The
  older failed session then sat below the cutoff forever, chronicled never,
  reported never, exit 0, nightly timer green.
* **`chronicle.py` personal-recall** wrapped its embedding call in
  `except Exception`, silently degrading semantic scoring to lexical and
  swallowing the `strict=True` zip's ValueError with it. `EmbeddingError` was
  imported in that block and never referenced, which is what was meant.
* **`classification-health.py`** registered `--unclassified`, read it nowhere,
  and had no third bucket: anything not exactly "corporate" fell into
  `ceo_only`. An operator running the flag got the ordinary summary.
* **`clear-dep-marker.py`** resolved the marker against `Path.cwd()`, so a run
  from anywhere but the repo root said "Nothing to clear" and exited 0 with the
  marker intact.
* **`composite-logo.py`** passed the logo as its own transparency mask with
  nothing checking it had one, so any JPEG logo died on a traceback.
* **`clip.py`** treated `grabclipboard()`'s documented LIST return (files on
  the clipboard) as an image and called `.save` on it.
* **`compaction-probe.py`** caught bare `Exception` around `CP.handoff_dir`, so
  a programming error read as "handoff archive unresolved" and poisoned every
  handoff assertion.
* **`checkpoint-paths.py`** dispatched on the first matching flag and returned,
  so `--auto on --compact-at 35` — the pairing its own comment says the
  operator types together — silently applied half the command.
* **`cold_sweep_core.py`** treated an unparseable `radar_freeze_until` as NOT
  frozen, turning a do-not-contact marker into an outreach card, silently.
* **`compression-candidates.py --output report.md`** wrote terminal colour
  escapes into the file.
* **`content-guard.py`** documented exit 2 for an internal error and had no
  path that produced it, and skipped unreadable files without a word — from a
  gate whose whole purpose is that nothing unscanned ships.
* **`check-version-sync.py --help`** named three of the four surfaces it checks.

Two findings from the same shard are NOT here, because the tree already carries
their fixes: the compaction probe's UTC-versus-local stamp comparison
(`_event_stamp_local`, with the 2026-08-23 measurement in its docstring) and
chronicle's `split("\\n\\n")[-2]` gist extraction. Both were fixed before this
shard's audit output was written.

Fixed 2026-08-24.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PY = str(ROOT / ".venv" / "bin" / "python")


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# chronicle.py — the marker must not step over a failure
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def chronicle():
    import scripts.chronicle as mod
    return mod


def test_the_marker_stops_at_the_oldest_failed_session(chronicle):
    """The reported sequence: an older session fails, a newer one skips."""
    capped = chronicle.capped_marker("2026-08-20", ["2026-08-18"])
    assert capped == "2026-08-18", (
        "the mark was raised to the newer skipped session, so `mday < cutoff` "
        "hid the failed one from every later run"
    )


def test_the_marker_stops_at_the_OLDEST_of_several_failures(chronicle):
    assert chronicle.capped_marker("2026-08-20",
                                   ["2026-08-19", "2026-08-15", "2026-08-17"]) \
        == "2026-08-15"


def test_a_clean_run_advances_the_marker_normally(chronicle):
    """Anchor: a cap that always refuses would freeze the chronicle."""
    assert chronicle.capped_marker("2026-08-20", []) == "2026-08-20"


def test_the_cap_never_moves_the_marker_forward(chronicle):
    """A failure NEWER than everything processed must not raise the mark."""
    assert chronicle.capped_marker("2026-08-10", ["2026-08-20"]) == "2026-08-10"


def test_the_failed_date_itself_stays_selectable(chronicle):
    """`select_sessions` filters `mday < cutoff`, so cutoff == the failed day
    keeps that day's sessions in scope. Equality is the whole point."""
    failed = "2026-08-18"
    cutoff = chronicle.capped_marker("2026-08-20", [failed])
    assert not (failed < cutoff), (
        f"cutoff {cutoff} excludes the failed session at {failed}"
    )


def test_cmd_build_records_the_failed_date(chronicle):
    """The cap is worthless if nothing collects the dates it caps against."""
    src = Path(chronicle.__file__).read_text(encoding="utf-8")
    assert "failed_dates.append(sdate)" in src
    assert "capped_marker(newest_processed, failed_dates)" in src


def test_the_embedding_fallback_is_narrow_and_audible(chronicle):
    """`except Exception` also ate the strict-zip ValueError and any regression
    inside the embeddings module, leaving only the word '(lexical)'."""
    src = Path(chronicle.__file__).read_text(encoding="utf-8")
    assert "except (EmbeddingError, OSError)" in src, (
        "the catch-all is back; EmbeddingError is the thing that was meant"
    )
    assert "semantic scoring unavailable" in src, (
        "a silent degrade to lexical scoring is the defect, not the fallback"
    )


# ---------------------------------------------------------------------------
# classification-health.py — the third bucket
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def clshealth():
    return _load("cls_health", "classification-health.py")


def test_unclassified_is_a_real_bucket(clshealth, monkeypatch, tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "a.py").write_text("x = 1\n")
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "b.md").write_text("x\n")

    monkeypatch.setattr(clshealth, "matched_routing_rule",
                        lambda f: "outputs/" if f.startswith("outputs/") else None)
    monkeypatch.setattr(clshealth, "get_classification",
                        lambda f: "ceo-only" if f.startswith("outputs/") else "corporate")

    results = clshealth.classify_files(tmp_path)
    assert results["unclassified"] == ["scripts/a.py"], (
        "a file with no rule was counted as CEO-only and looked deliberate"
    )


def test_the_unclassified_flag_is_actually_read(clshealth):
    src = Path(ROOT / "scripts" / "classification-health.py").read_text(encoding="utf-8")
    assert "args.unclassified" in src, (
        "the argument is registered and never read, so running it prints the "
        "ordinary summary and the operator concludes there are none"
    )
    assert "def print_unclassified" in src


def test_the_unclassified_report_does_not_call_the_default_a_defect(clshealth):
    """Taking the map default is the DESIGNED outcome for shareable code, and
    2112 paths do. Colouring that red makes every healthy run look broken."""
    src = Path(ROOT / "scripts" / "classification-health.py").read_text(encoding="utf-8")
    body = src.split("def print_unclassified", 1)[1].split("\ndef ", 1)[0]
    assert "{RED}" not in body, "the review list is presented as a defect list"
    assert "map default" in body


def test_json_output_carries_the_bucket(clshealth):
    src = Path(ROOT / "scripts" / "classification-health.py").read_text(encoding="utf-8")
    assert '"unclassified_count"' in src and '"unclassified_files"' in src


def test_matched_routing_rule_answers_the_question_it_is_for():
    from scripts.utils.workspace import matched_routing_rule
    assert matched_routing_rule("outputs/anything.md") is not None
    assert matched_routing_rule("scripts/browser.py") is None, (
        "scripts/ has no explicit rule; it takes the engine default, and the "
        "resolver has to be able to say so"
    )


def test_the_split_did_not_change_what_routing_resolves_to():
    """Anchor: matched_routing_rule was carved out of get_routing_destination."""
    from scripts.utils.workspace import get_routing_destination
    assert get_routing_destination("outputs/x.md") == "private"
    assert get_routing_destination("scripts/browser.py") == "engine"


# ---------------------------------------------------------------------------
# clear-dep-marker.py — anchored to the workspace, not the shell's cwd
# ---------------------------------------------------------------------------

def test_the_marker_is_cleared_from_any_directory(tmp_path):
    marker = ROOT / ".sync" / "dep-update-pending.json"
    assert not marker.exists(), "a real marker is pending; not clobbering it"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}\n")
    try:
        out = subprocess.run(
            [PY, str(ROOT / "scripts" / "clear-dep-marker.py")],
            cwd=tmp_path, capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        assert not marker.exists(), (
            f"run from {tmp_path} the marker survived: it was resolved against "
            f"the cwd, so the session-start banner keeps firing after a "
            f"successful install. stdout={out.stdout!r}"
        )
    finally:
        marker.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# composite-logo.py — a logo without an alpha channel
# ---------------------------------------------------------------------------

def test_a_logo_with_no_alpha_channel_composites(tmp_path):
    Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    base = tmp_path / "base.png"
    logo = tmp_path / "logo.jpg"
    out = tmp_path / "out.png"
    Image.new("RGB", (400, 300), "white").save(base)
    Image.new("RGB", (100, 50), "red").save(logo)   # JPEG: no alpha, ever

    proc = subprocess.run(
        [PY, str(ROOT / "scripts" / "composite-logo.py"), str(base), str(logo), str(out)],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, (
        f"a JPEG logo is an ordinary input for an untyped <logo_image>; it "
        f"raised 'bad transparency mask'. stderr={proc.stderr}"
    )
    assert out.exists()


def test_an_rgba_logo_still_composites(tmp_path):
    """Anchor: the convert must not break the case that already worked."""
    Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    base, logo, out = tmp_path / "b.png", tmp_path / "l.png", tmp_path / "o.png"
    Image.new("RGB", (400, 300), "white").save(base)
    Image.new("RGBA", (100, 50), (255, 0, 0, 128)).save(logo)
    proc = subprocess.run(
        [PY, str(ROOT / "scripts" / "composite-logo.py"), str(base), str(logo), str(out)],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert out.exists()


# ---------------------------------------------------------------------------
# clip.py — the clipboard can hold file paths
# ---------------------------------------------------------------------------

def test_files_on_the_clipboard_are_a_clean_error_not_a_traceback(tmp_path,
                                                                  monkeypatch,
                                                                  capsys):
    pytest.importorskip("PIL.ImageGrab", reason="Pillow not installed")
    clip = _load("clip_mod", "clip.py")
    monkeypatch.setattr(clip, "get_outputs_dir", lambda: tmp_path)
    monkeypatch.setattr(clip.ImageGrab, "grabclipboard",
                        lambda: ["/home/x/a.png", "/home/x/b.png"])
    rc = clip.main()
    assert rc == 1, "a list has no .save; this used to be an AttributeError"
    assert "file path" in capsys.readouterr().err


def test_an_image_on_the_clipboard_still_saves(tmp_path, monkeypatch, capsys):
    """Anchor: the isinstance guard must not reject the normal case."""
    Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    clip = _load("clip_mod2", "clip.py")
    monkeypatch.setattr(clip, "get_outputs_dir", lambda: tmp_path)
    monkeypatch.setattr(clip.ImageGrab, "grabclipboard",
                        lambda: Image.new("RGB", (4, 4), "red"))
    assert clip.main() == 0
    assert (tmp_path / "clipboard" / "clip.png").exists()


# ---------------------------------------------------------------------------
# compaction-probe.py — a bug must not read as a condition
# ---------------------------------------------------------------------------

def test_a_programming_error_in_handoff_dir_is_not_swallowed(monkeypatch, tmp_path):
    probe = _load("probe_mod", "compaction-probe.py")

    def bad_signature(project):
        raise TypeError("handoff_dir() missing 1 required positional argument")

    monkeypatch.setattr(probe.CP, "handoff_dir", bad_signature)
    with pytest.raises(TypeError):
        probe._archives(tmp_path)


def test_an_unresolvable_overlay_is_still_a_note(monkeypatch, tmp_path):
    """Anchor: narrowing must not turn the real condition into a crash."""
    probe = _load("probe_mod2", "compaction-probe.py")
    monkeypatch.setattr(probe.CP, "handoff_dir",
                        lambda project: (_ for _ in ()).throw(OSError("no overlay")))
    grouped, notes = probe._archives(tmp_path)
    assert grouped == {} and notes and "unresolved" in notes[0]


# ---------------------------------------------------------------------------
# checkpoint-paths.py — both halves of the command run
# ---------------------------------------------------------------------------

def test_two_action_flags_both_run(monkeypatch):
    cp = _load("cp_mod", "checkpoint-paths.py")
    ran = []
    monkeypatch.setattr(cp, "auto_switch", lambda v: ran.append(("auto", v)) or 0)
    monkeypatch.setattr(cp, "compact_at_switch",
                        lambda v: ran.append(("compact_at", v)) or 0)
    rc = cp.main(["--auto", "on", "--compact-at", "35"])
    assert rc == 0
    assert ran == [("auto", "on"), ("compact_at", "35")], (
        f"only {ran} ran; the threshold was silently never set, and argparse "
        "accepts the pairing without complaint"
    )


def test_a_refusal_does_not_cancel_the_other_action(monkeypatch):
    cp = _load("cp_mod2", "checkpoint-paths.py")
    ran = []
    monkeypatch.setattr(cp, "auto_switch", lambda v: ran.append("auto") or 0)
    monkeypatch.setattr(cp, "compact_at_switch", lambda v: ran.append("compact_at") or 2)
    rc = cp.main(["--auto", "on", "--compact-at", "999"])
    assert ran == ["auto", "compact_at"]
    assert rc == 2, "the refusal has to reach the exit code"


def test_a_single_action_still_works(monkeypatch):
    cp = _load("cp_mod3", "checkpoint-paths.py")
    monkeypatch.setattr(cp, "auto_switch", lambda v: 0)
    assert cp.main(["--auto", "status"]) == 0


# ---------------------------------------------------------------------------
# cold_sweep_core.py — a suppression flag fails closed
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sweep():
    import scripts.cold_sweep_core as mod
    return mod


def test_an_unparseable_freeze_date_keeps_the_contact_frozen(sweep, capsys):
    """`_frozen` now delegates to the single `crm.is_radar_frozen`.

    Shard `scripts-04-p4` showed this fix had reached one of THREE copies of
    the same parse: `cold_sweep_core._frozen` (fixed here first),
    `crm_next.rank_candidates`, and `crm.is_radar_frozen` — the last being the
    one whose docstring cheerfully noted that the other two matched it. All
    three failed open. They are one function now, and the message it prints is
    that function's.
    """
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    assert sweep._frozen("not-a-date", now) is True, (
        "a typo in radar_freeze_until turned a do-not-contact marker into an "
        "outreach card, with no log line anywhere"
    )
    assert "not an ISO date" in capsys.readouterr().err


def test_a_z_suffixed_freeze_date_is_understood(sweep):
    """The audit blamed the `Z` form; on this repo's floor it was never the bug.

    `requires-python = ">=3.11"` and `fromisoformat` has parsed `Z` natively
    since 3.11, so a mutation deleting a `.replace("Z", "+00:00")` stayed green
    — correctly. The guard that matters is the fail-closed one above.
    """
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    assert sys.version_info >= (3, 11), "below 3.11 the Z form needs a replace"
    assert sweep._frozen("2099-01-01T00:00:00Z", now) is True


def test_an_expired_freeze_does_not_hold(sweep):
    """Anchor: fail-closed must not mean always-closed."""
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    assert sweep._frozen("2020-01-01T00:00:00Z", now) is False
    assert sweep._frozen("", now) is False
    assert sweep._frozen(None, now) is False


def test_a_future_freeze_holds(sweep):
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    later = (now + timedelta(days=1)).isoformat()
    assert sweep._frozen(later, now) is True


def test_the_module_docstring_names_the_path_the_cli_takes(sweep):
    src = Path(sweep.__file__).read_text(encoding="utf-8")
    head = src.split('"""', 2)[1]
    assert "/action-queue/deposit`` endpoint.\n" not in head, (
        "the CLI has appended in-process since 2026-06-27; the docstring "
        "taught an architecture that no longer exists"
    )
    assert "append_cards" in head


# ---------------------------------------------------------------------------
# compression-candidates.py — a file is not a terminal
# ---------------------------------------------------------------------------

def test_the_report_file_holds_no_colour_escapes(tmp_path):
    mod = _load("compress_mod", "compression-candidates.py")
    assert mod._strip_ansi("\x1b[1mbold\x1b[0m and \x1b[92mgreen\x1b[0m") == \
        "bold and green"


def test_the_output_path_goes_through_the_stripper():
    src = (ROOT / "scripts" / "compression-candidates.py").read_text(encoding="utf-8")
    assert "args.output.write_text(_strip_ansi(output)" in src, (
        "the docstring's own example, --output report.md, produced a Markdown "
        "file full of \\x1b[1m sequences"
    )


# ---------------------------------------------------------------------------
# content-guard.py — a gate says what it did not scan
# ---------------------------------------------------------------------------

def test_an_unreadable_file_is_reported_by_the_gate(tmp_path):
    bad = ROOT / "tests" / "_content_guard_probe.bin"
    bad.write_bytes(b"\xff\xfe\x00 not utf-8 \xff")
    try:
        proc = subprocess.run(
            [PY, str(ROOT / "scripts" / "content-guard.py"), "--files",
             bad.relative_to(ROOT).as_posix()],
            cwd=ROOT, capture_output=True, text=True, timeout=120)
        combined = proc.stdout + proc.stderr
        # The file may be filtered out before the read as a non-text path; what
        # must never happen is a clean verdict over a file that WAS read and
        # failed. Assert the reporting path exists and is wired.
        src = (ROOT / "scripts" / "content-guard.py").read_text(encoding="utf-8")
        assert "were NOT scanned" in src, (
            "a bare `continue` let an unreadable engine-routed file pass a gate "
            f"whose whole purpose is that nothing unscanned ships. Run said: "
            f"{combined!r}"
        )
        assert "unscanned.append" in src
    finally:
        bad.unlink(missing_ok=True)


def test_the_gate_refuses_rather_than_admitting_what_it_skipped():
    """Superseded 2026-08-25, in the same direction, one step further.

    This test used to require the CLEAN line to name its unscanned files -
    the first fix for "printing `clean` while files went unread is the
    coverage claim .claude/rules/scope-claims.md forbids". Naming them was an
    improvement over silence and still exited 0, and the exit code is the only
    thing CI reads: the gate went on shipping a surface it had not scanned.
    There is now no clean-with-skips line to inspect, because that state
    refuses. Asserting the old sentence would pin the weaker contract.
    """
    src = (ROOT / "scripts" / "content-guard.py").read_text(encoding="utf-8")
    assert "unreadable and NOT scanned" not in src
    assert "content-guard: REFUSED" in src
    assert "unscanned.append" in src, "it must still RECORD what it could not read"


def test_the_documented_exit_2_exists():
    src = (ROOT / "scripts" / "content-guard.py").read_text(encoding="utf-8")
    assert "SystemExit(2)" in src, (
        "the docstring contracts `2 internal error` and nothing produced it: a "
        "crash exited 1, indistinguishable from `leak found` to any CI step "
        "keying on the contract"
    )
    # The literal moved when exit 1 widened to cover an unscannable file; the
    # claim being pinned is that the docstring states a contract and that
    # `SystemExit(2)` exists to keep the third code distinguishable.
    assert "2 internal error." in src
    assert "Exit: 0 clean, 1 leak(s) found OR a file that could not be scanned" in src


def test_the_gate_still_exits_0_on_a_clean_file():
    """Anchor: the exit-2 wrapper must not swallow the normal codes."""
    proc = subprocess.run(
        [PY, str(ROOT / "scripts" / "content-guard.py"), "--files", "README.md"],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# check-version-sync.py — --help states the real scope
# ---------------------------------------------------------------------------

def test_help_names_every_surface_the_tool_checks():
    proc = subprocess.run(
        [PY, str(ROOT / "scripts" / "check-version-sync.py"), "--help"],
        cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert "ROADMAP" in proc.stdout, (
        "the guard checks four surfaces and --help named three; this repo "
        "treats under-reporting scope as a defect class"
    )
