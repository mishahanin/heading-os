import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "scripts" / "leak-guard.py"

_spec = importlib.util.spec_from_file_location("leak_guard", GUARD)
leak_guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(leak_guard)


def _run(args, **kw):
    return subprocess.run(
        [sys.executable, str(GUARD), *args],
        capture_output=True, text=True, cwd=ROOT, **kw
    )


def test_check_paths_flags_hardcoded_data_path(tmp_path):
    bad = tmp_path / "bad_script.py"
    bad.write_text('p = root / "crm/contacts" / name\n', encoding="utf-8")
    r = _run(["check-paths", "--files", str(bad)])
    assert r.returncode == 1
    assert "crm/contacts" in r.stdout


def test_check_paths_allows_helper_usage(tmp_path):
    good = tmp_path / "good_script.py"
    good.write_text("p = get_crm_contacts_dir() / name\n", encoding="utf-8")
    r = _run(["check-paths", "--files", str(good)])
    assert r.returncode == 0


def _live_literal_lines(path: Path) -> list[int]:
    """Lines of `path` that `check_paths` would actually flag.

    The gate skips comment lines and `leak-guard: ok` lines BEFORE matching, so
    a bare `_LITERAL_RE.search()` over the whole file answers a different
    question than the gate asks. Measured 2026-09-01: `scripts/utils/workspace.py`
    matches the regex twice and both hits are inside comments.
    """
    hits = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("#") or "leak-guard: ok" in line:
            continue
        if leak_guard._LITERAL_RE.search(line):
            hits.append(n)
    return hits


def test_check_paths_skips_the_seam_file():
    """A SEAM_ALLOWLIST member is skipped even though it holds the literals.

    Until 2026-09-01 this pointed at `scripts/utils/workspace.py` and said the
    seam "legitimately contains the literal inside the helper definition". That
    stopped being true: the seam builds its paths segment by segment
    (`get_personal_root() / "crm" / "contacts"`), so the only two lines in it
    that match the lint's anchored regex are comments, which the gate drops
    before matching. The assertion was green over an empty premise, and MEASURED
    that day BOTH allowlist entries could be deleted with 130 tests across this
    file and five neighbours still passing, while the CI command
    (`git ls-files | leak-guard check-paths`) failed on the second of them.

    `scripts/leak-guard.py` is the allowlist member that still carries real
    matching literals on live lines: its own `DATA_PATH_TOKENS` list is five
    quoted strings each STARTING with a token. So it is the one that measures
    the allowlist.
    """
    assert not _live_literal_lines(ROOT / "scripts" / "utils" / "workspace.py"), (
        "the seam now has a live matching literal; it is a legitimate second "
        "witness, so widen this test rather than leaving the note stale"
    )
    assert _live_literal_lines(GUARD), (
        "the allowlisted file no longer contains a literal this lint would "
        "flag, so skipping it proves nothing; point this test at a member "
        "that does"
    )
    r = _run(["check-paths", "--files", str(GUARD)])
    assert r.returncode == 0, (
        f"an allowlisted seam file was flagged: {r.stdout}{r.stderr}")


def test_the_allowlist_is_what_skips_it_and_not_some_other_clause():
    """The negative half: take the entry away and the same file is refused.

    Without this, the test above is satisfied by any of the other four skips
    (suffix, tests/, scripts/archive/, non-engine routing) and the allowlist
    could be emptied unnoticed - which is exactly the state it was in.
    """
    without = leak_guard.SEAM_ALLOWLIST - {"scripts/leak-guard.py"}
    original = leak_guard.SEAM_ALLOWLIST
    leak_guard.SEAM_ALLOWLIST = without
    try:
        assert leak_guard.check_paths([GUARD]) == 1, (
            "removing the entry did not change the verdict, so the entry is "
            "not what produced the pass above"
        )
    finally:
        leak_guard.SEAM_ALLOWLIST = original


def test_check_paths_ignores_url_substring(tmp_path):
    # Anchored regex (M4): a URL merely CONTAINING outputs/ must not flag.
    ok = tmp_path / "url_script.py"
    ok.write_text('u = "https://example.com/outputs/x"\n', encoding="utf-8")
    r = _run(["check-paths", "--files", str(ok)])
    assert r.returncode == 0


def test_check_paths_respects_inline_suppression(tmp_path):
    # A legitimate relative-path usage annotated with the suppression marker
    # must not be flagged.
    ok = tmp_path / "relkeys.py"
    ok.write_text('PATHS = ["crm/contacts/"]  # leak-guard: ok (relative prefix)\n', encoding="utf-8")
    r = _run(["check-paths", "--files", str(ok)])
    assert r.returncode == 0


def test_check_paths_skips_test_files():
    # Test files legitimately embed data-path literals as fixtures (this very
    # file does). The lint must skip anything under tests/.
    r = _run(["check-paths", "--files", "tests/test_routing_map.py", "tests/test_leak_guard.py"])
    assert r.returncode == 0


def test_check_paths_skips_archived_scripts():
    # Archived scripts under scripts/archive/ are inert dead code retained for
    # history; they route to 'engine' but must not be linted (Plan 2 Task 6).
    r = _run(["check-paths", "--files", "scripts/archive/2026-04-24-export-sync.py"])
    assert r.returncode == 0


def test_check_staged_blocks_private_via_autodetect_no_marker(tmp_path):
    """Regression for the 2026-06-22 leak, on a topology this test builds itself.

    With NO env marker the guard must still block a private-routing file, because
    the clone is the split-topology engine (`get_data_root() != workspace root`).
    The hand-set marker being the sole trigger is exactly why the guard sat inert
    while specs leaked.

    The overlay is pinned through `HEADING_OS_DATA` rather than inherited from
    the machine. Until 2026-08-23 this read whatever topology the host happened
    to have, so on a bare public clone — where the data root falls back to the
    workspace root and autodetect is correctly inert — it went red and blamed
    the guard for the absence of an overlay.
    """
    overlay = tmp_path / "data-overlay"
    overlay.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "HEADING_OS_ENGINE_REPO"}
    env["HEADING_OS_DATA"] = str(overlay)
    r = _run(["check-staged", "--files", "crm/contacts/x.md"], env=env)
    assert r.returncode == 1, (
        f"the guard did not fire on a split topology: {r.stdout}{r.stderr}"
    )
    assert "crm/contacts/x.md" in r.stdout


def test_check_staged_is_inert_when_the_overlay_is_the_workspace(tmp_path):
    """The other half, through the same entry point: one tree, no marker, no block.

    Pins that the fix above is a real topology switch and not a permanent block.
    The in-process sibling below monkeypatches the seam; this one drives the CLI
    the pre-commit hook actually runs.
    """
    env = {k: v for k, v in os.environ.items() if k != "HEADING_OS_ENGINE_REPO"}
    env["HEADING_OS_DATA"] = str(ROOT)
    r = _run(["check-staged", "--files", "crm/contacts/x.md"], env=env)
    assert r.returncode == 0, (
        f"the guard blocked a single-tree clone: {r.stdout}{r.stderr}"
    )


def test_check_staged_blocks_private_file_in_engine():
    r = _run(["check-staged", "--files", "crm/contacts/x.md"],
             env={**os.environ, "HEADING_OS_ENGINE_REPO": "1"})
    assert r.returncode == 1
    assert "crm/contacts/x.md" in r.stdout


def test_check_staged_allows_engine_file_in_engine():
    r = _run(["check-staged", "--files", "scripts/foo.py"],
             env={**os.environ, "HEADING_OS_ENGINE_REPO": "1"})
    assert r.returncode == 0


def test_the_marker_alone_arms_the_guard_with_autodetect_inert():
    """`HEADING_OS_ENGINE_REPO=1` is the explicit override, and it is measured here.

    The two cases above set the marker but do not PIN the topology, so on the
    operator's split-topology machine autodetect arms the guard whatever the
    marker says. MEASURED 2026-09-01: deleting the whole
    `os.environ.get("HEADING_OS_ENGINE_REPO") == "1"` branch left 130 tests
    across this file and five neighbours green. The override was unwitnessed on
    every machine that has an overlay, which is every operator machine.

    Pinning `HEADING_OS_DATA` at the workspace root makes autodetect inert (one
    tree), so the marker is the only thing left that can arm it.
    """
    env = {**os.environ, "HEADING_OS_DATA": str(ROOT), "HEADING_OS_ENGINE_REPO": "1"}
    r = _run(["check-staged", "--files", "crm/contacts/x.md"], env=env)
    assert r.returncode == 1, (
        f"the marker did not arm the guard on a single-tree clone: "
        f"{r.stdout}{r.stderr}"
    )
    assert "crm/contacts/x.md" in r.stdout


def test_check_staged_blocks_a_corporate_file_too():
    """`corporate` content is not public either, and had no witness.

    MEASURED 2026-09-01: narrowing the block set from
    `{"private", "corporate"}` to `{"private"}` left 130 tests across this file
    and five neighbours green. The engine repository is PUBLIC; corporate
    content is shared DOWN to executives and not beyond, so a corporate-routed
    file staged here is a leak of a different colour, not a lesser one.

    The path is derived from the map rather than written down, so a
    reclassification retires this test loudly instead of leaving it green over
    a destination nothing routes to any more.
    """
    from scripts.utils.workspace import load_routing_map

    corporate = sorted(k for k, v in load_routing_map()["rules"].items()
                       if v == "corporate")
    assert corporate, "no path routes 'corporate' any more; retire this test"
    probe = corporate[0].rstrip("/") + "/probe-not-on-disk.md"
    r = _run(["check-staged", "--files", probe],
             env={**os.environ, "HEADING_OS_ENGINE_REPO": "1"})
    assert r.returncode == 1, (
        f"a corporate-routed path was allowed into the public engine repo: "
        f"{r.stdout}{r.stderr}"
    )
    assert probe in r.stdout


def test_check_paths_refuses_when_the_routing_map_cannot_classify(monkeypatch, tmp_path):
    """A degraded routing map must not turn this lint into a silent no-op.

    `load_routing_map()` fails CLOSED: one bad byte in
    `config/routing-map.yaml` classifies every path 'private'. `check_paths`
    skips anything that is not 'engine', so the composition inspected nothing
    and returned 0 - the same answer a clean tree gives.

    MEASURED 2026-09-01 on one engine file holding `P = "crm/contacts/x.md"`:
    healthy map -> 1, degraded map -> 0. At commit time the sibling
    `leak-guard-staged` hook masks it (a degraded map makes it refuse every
    staged path); nothing masks it in CI, where
    `.github/workflows/ci.yml` runs `check-paths` over `git ls-files` alone.

    Both halves run against the REAL loader over a scratch map, so this measures
    the composition rather than a stub of it.
    """
    from scripts.utils import workspace

    (tmp_path / "config").mkdir()
    (tmp_path / "scripts").mkdir()
    probe = tmp_path / "scripts" / "probe.py"
    probe.write_text('P = "crm/contacts/x.md"\n', encoding="utf-8")
    map_path = tmp_path / "config" / "routing-map.yaml"

    monkeypatch.setattr(workspace, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(leak_guard, "get_workspace_root", lambda: tmp_path)
    workspace._load_routing_map_cached.cache_clear()
    try:
        # Control: a healthy map, and the gate does its job.
        map_path.write_text("default: engine\nrules: {}\n", encoding="utf-8")
        assert leak_guard.check_paths([probe]) == 1, (
            "the control never reached the violation, so the case below "
            "measures nothing"
        )

        # One invalid byte. The loader fails closed to 'private' for every path.
        map_path.write_bytes(b"default: \xffengine\nrules: {}\n")
        workspace._load_routing_map_cached.cache_clear()
        assert workspace.get_routing_destination("scripts/probe.py") == "private", (
            "the scratch map is not actually degraded, so this test is not "
            "asking the question it says it asks"
        )
        assert leak_guard.check_paths([probe]) == 1, (
            "the gate reported clean over a tree it could not classify, which "
            "is byte-for-byte what a genuinely clean tree looks like"
        )
    finally:
        workspace._load_routing_map_cached.cache_clear()


def test_in_engine_repo_inert_on_single_repo(monkeypatch, tmp_path):
    # Pre-cutover single repo (data_root == workspace_root): the guard must be inert
    # so legitimately-tracked data files are not flagged. Marker absent.
    monkeypatch.delenv("HEADING_OS_ENGINE_REPO", raising=False)
    same = tmp_path / "single-repo"
    monkeypatch.setattr(leak_guard, "get_data_root", lambda: same)
    monkeypatch.setattr(leak_guard, "get_workspace_root", lambda: same)
    assert leak_guard._in_engine_repo() is False
    assert leak_guard.check_staged(["crm/contacts/x.md"]) == 0


def test_in_engine_repo_active_when_split(monkeypatch, tmp_path):
    # Split topology (data in a sibling): auto-active even without the marker.
    monkeypatch.delenv("HEADING_OS_ENGINE_REPO", raising=False)
    monkeypatch.setattr(leak_guard, "get_data_root", lambda: tmp_path / "data")
    monkeypatch.setattr(leak_guard, "get_workspace_root", lambda: tmp_path / "engine")
    assert leak_guard._in_engine_repo() is True


def test_in_engine_repo_fails_closed_on_seam_error(monkeypatch):
    # If the data-root seam cannot resolve, assume engine and enforce (fail-closed).
    monkeypatch.delenv("HEADING_OS_ENGINE_REPO", raising=False)

    def _boom():
        raise RuntimeError("seam unreadable")

    monkeypatch.setattr(leak_guard, "get_data_root", _boom)
    assert leak_guard._in_engine_repo() is True
