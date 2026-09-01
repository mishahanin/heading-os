"""Shard scripts-15-p1: six tools that reported a state they never reached.

The shard's spine is one defect wearing six coats. A tool ran a narrow method
and then printed a sentence -- or an exit code, which is a sentence a machine
reads -- describing a much wider state.

- `cliproxyapi_update.py` replaced the running binary, failed to find the health
  probe, and exited 2, the code its own docstring defines as "NOTHING was
  swapped".
- `wizard-verify-key.py` exited 2 on a mistyped flag, and 2 means "rate-limited,
  the key is probably fine" to the setup wizard that reads it.
- `visual-discipline-check.py baseline check` printed "No findings above the
  baseline." and exited 0 when only one of its two engines had run, and its
  scan-count line omitted every file the baseline fully absorbed.
- `verify-skills-lock.py` had a guard whose comment described a footgun it did
  not actually close.
- `watchdog_core.load_cadence` swallowed a config failure without a word, and
  died outright on a malformed value -- the watchdog going silent.

Each test below pins one of those, at the seam where the wrong sentence is
produced rather than at the source text that produces it.

The key fixtures here are deliberately not key-shaped. A realistic-looking
credential in a tracked file is refused by the commit gate, and rightly: the
tests need a value to carry, not a value that looks live.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
import tarfile
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# cliproxyapi_update.py -- exit 2 must mean "nothing was swapped"
# ============================================================

@pytest.fixture
def cpx():
    return _load("scripts/updaters/cliproxyapi_update.py", "cpx_under_test")


def _staged_release(tmp_path: Path, cpx, monkeypatch, *, body=b"NEW-BINARY"):
    """A tarball + checksums pair the real verify path accepts.

    Everything remote is stubbed; the sha256 gate and the tar member selection
    run for real, so the test exercises the same swap the daemon does.
    """
    import hashlib

    src = tmp_path / "src"
    src.mkdir()
    (src / "cli-proxy-api").write_bytes(body)
    tarball = tmp_path / "cpx.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(src / "cli-proxy-api", arcname="cli-proxy-api")
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()

    cpx_dir = tmp_path / "cliproxyapi"
    cpx_dir.mkdir()
    binary = cpx_dir / "cli-proxy-api"
    binary.write_bytes(b"OLD-BINARY")
    binary.chmod(0o755)
    monkeypatch.setattr(cpx, "CPX_DIR", cpx_dir)
    monkeypatch.setattr(cpx, "BIN", binary)

    def _fake_download(url, dest):
        if url.endswith("checksums.txt"):
            dest.write_text(f"{digest}  cpx_linux_amd64.tar.gz\n")
        else:
            dest.write_bytes(tarball.read_bytes())

    monkeypatch.setattr(cpx, "_download", _fake_download)
    monkeypatch.setattr(cpx, "_current_version", lambda: "Version: 7.0.0")
    monkeypatch.setattr(cpx.update_sources, "latest_version", lambda spec: "7.9.9")
    monkeypatch.setattr(
        cpx.update_sources, "github_asset_url",
        lambda spec, arch: "https://example.invalid/r/cpx_linux_amd64.tar.gz")
    monkeypatch.setattr(cpx.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""))
    return binary


def test_a_missing_health_probe_refuses_before_the_swap(cpx, tmp_path, monkeypatch, capsys):
    """The pre-flight, and the reason exit 2 is now honest.

    Without it the probe's FileNotFoundError was raised AFTER the swap, caught
    by main's pre-swap handler, and reported as "update aborted before any
    swap" with exit 2 -- while the new, unverified binary was already live.
    """
    binary = _staged_release(tmp_path, cpx, monkeypatch)
    monkeypatch.setattr(cpx, "HEALTH_PROBE", tmp_path / "no" / "such" / "cliproxy")

    rc = cpx.main()

    assert rc == 2
    out = capsys.readouterr().out
    assert "refusing to swap" in out
    assert "before any swap" not in out
    # The claim exit 2 makes, verified rather than asserted.
    assert binary.read_bytes() == b"OLD-BINARY"


def test_a_probe_that_dies_after_the_swap_is_never_called_a_pre_swap_abort(
        cpx, tmp_path, monkeypatch, capsys):
    """The safety net behind the pre-flight.

    The probe can pass the pre-flight and be gone (or unreadable) by the time
    the health gate runs. That OSError must not travel to main's handler, which
    would print the pre-swap wording over a completed swap.
    """
    binary = _staged_release(tmp_path, cpx, monkeypatch)
    probe = tmp_path / "cliproxy"
    probe.write_text("#!/bin/sh\nexit 0\n")
    probe.chmod(0o755)
    monkeypatch.setattr(cpx, "HEALTH_PROBE", probe)

    def _vanish(*a, **k):
        raise FileNotFoundError(str(probe))

    monkeypatch.setattr(cpx, "_health_ok", _vanish)

    rc = cpx.main()

    out = capsys.readouterr().out
    assert rc == 1, "a completed swap may not report a pre-swap exit code"
    assert "before any swap" not in out
    assert "ALREADY replaced" in out
    assert binary.read_bytes() == b"NEW-BINARY", "the swap did happen; say so"


def _break_the_restore(cpx, monkeypatch):
    """Fail exactly the copy that puts the backup back, and no other.

    `shutil.copy2` is called three times on this path -- backup, stage-beside,
    restore -- so a call counter would break the swap instead. Keying on the
    source keeps the test aimed at the rollback.
    """
    real_copy = cpx.shutil.copy2

    def _copy(src, dst, *a, **k):
        if ".bak" in str(src):
            raise PermissionError("read-only filesystem")
        return real_copy(src, dst, *a, **k)

    monkeypatch.setattr(cpx.shutil, "copy2", _copy)


def test_a_failed_rollback_is_not_reported_as_nothing_swapped(
        cpx, tmp_path, monkeypatch, capsys):
    """`_restore`'s copy can raise too, and it landed on the same wrong branch:
    out of `_fetch_verify_and_swap`, into main's pre-swap handler, exit 2."""
    binary = _staged_release(tmp_path, cpx, monkeypatch)
    probe = tmp_path / "cliproxy"
    probe.write_text("#!/bin/sh\nexit 0\n")
    probe.chmod(0o755)
    monkeypatch.setattr(cpx, "HEALTH_PROBE", probe)
    monkeypatch.setattr(cpx, "_health_ok", lambda: False)
    monkeypatch.setattr(cpx, "_wait_healthy", lambda *a, **k: False)
    _break_the_restore(cpx, monkeypatch)

    rc = cpx.main()

    out = capsys.readouterr().out
    assert rc == 1
    assert "before any swap" not in out
    assert "ROLLBACK FAILED" in out
    assert "investigate now" in out
    assert binary.read_bytes() == b"NEW-BINARY", (
        "the rollback did not happen; the message must not imply it did")
    # The whole point of returning False. Falling through to the health poll
    # prints "rolled back to the previous binary", which is a claim about a copy
    # that raised -- the operator would go looking at the wrong binary.
    assert "rolled back to the previous binary" not in out


def test_a_failed_rollback_after_a_failed_swap_also_exits_one(
        cpx, tmp_path, monkeypatch, capsys):
    """The other `_restore` call site, in the swap-failure handler. An OSError
    raised there escaped the handler that was meant to contain it."""
    _staged_release(tmp_path, cpx, monkeypatch)
    probe = tmp_path / "cliproxy"
    probe.write_text("#!/bin/sh\nexit 0\n")
    probe.chmod(0o755)
    monkeypatch.setattr(cpx, "HEALTH_PROBE", probe)
    _break_the_restore(cpx, monkeypatch)

    def _systemctl(cmd, *a, **k):
        if cmd[:4] == ["systemctl", "--user", "start", cpx.SERVICE] and k.get("check"):
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(cpx.subprocess, "run", _systemctl)

    rc = cpx.main()

    out = capsys.readouterr().out
    assert rc == 1
    assert "before any swap" not in out
    assert "swap failed" in out
    assert "ROLLBACK FAILED" in out


def test_the_health_probe_path_is_one_constant():
    """Two spellings of the probe path would let the pre-flight check one file
    and the gate run another."""
    src = (ROOT / "scripts" / "updaters" / "cliproxyapi_update.py").read_text()
    assert src.count('".local"') == 1, "the probe path is built in more than one place"
    assert "HEALTH_PROBE" in src


# ============================================================
# wizard-verify-key.py -- a usage error is not a rate limit
# ============================================================

WIZARD = ROOT / "scripts" / "wizard-verify-key.py"


def _wizard(*argv, env=None):
    return subprocess.run([sys.executable, str(WIZARD), *argv],
                          capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


@pytest.mark.parametrize("argv", [
    ("--provider", "nope", "--key", "x"),      # value outside `choices`
    ("--provider", "anthropic"),               # no key anywhere
    ("--kee", "x"),                            # mistyped flag
    (),                                        # nothing at all
])
def test_a_usage_error_never_exits_the_rate_limited_code(argv):
    """2 is read by the setup wizard as "key likely valid, proceed with note".

    A command that never reached the network must not be able to produce it.
    """
    res = _wizard(*argv, env={"WIZARD_VERIFY_KEY": ""})
    assert res.returncode == 4, f"{argv} exited {res.returncode}, not 4"
    assert res.stdout == "", "a usage error must not print a status object"


def test_the_documented_bad_argument_code_is_reachable():
    """`4 = bad arguments` was documented and, before this, unreachable: every
    argument error went through argparse and exited 2."""
    doc = WIZARD.read_text().split('"""')[1]
    assert "4 = bad arguments" in doc
    assert _wizard("--provider", "anthropic", "--key").returncode == 4


def test_the_key_can_be_passed_without_appearing_in_argv(monkeypatch):
    """argv is world-readable via /proc/<pid>/cmdline and lands in shell
    history. The environment path exists so the wizard has an alternative."""
    mod = _load("scripts/wizard-verify-key.py", "wizard_under_test")
    seen = {}

    def _fake_verify(key):
        seen["key"] = key
        return "ok", "fine"

    mod.verify_anthropic = _fake_verify
    monkeypatch.setenv("WIZARD_VERIFY_KEY", "VALUE-FROM-THE-ENVIRONMENT")

    rc = mod.main(["--provider", "anthropic"])

    assert rc == 0
    assert seen["key"] == "VALUE-FROM-THE-ENVIRONMENT"


def test_an_explicit_key_still_wins_over_the_environment(monkeypatch):
    """Back-compat: `--key` keeps working, and is not shadowed."""
    mod = _load("scripts/wizard-verify-key.py", "wizard_under_test2")
    seen = {}

    def _fake_verify(key):
        seen["key"] = key
        return "ok", "fine"

    mod.verify_anthropic = _fake_verify
    monkeypatch.setenv("WIZARD_VERIFY_KEY", "VALUE-FROM-ENV")

    mod.main(["--provider", "anthropic", "--key", "VALUE-FROM-FLAG"])

    assert seen["key"] == "VALUE-FROM-FLAG"


def test_the_wizard_skill_no_longer_puts_the_key_in_argv():
    """The only caller. A fix in the script that the documentation contradicts
    is not a fix."""
    skill = (ROOT / ".claude" / "skills" / "setup-wizard" / "SKILL.md").read_text()
    assert "wizard-verify-key.py --provider anthropic --key" not in skill
    assert "WIZARD_VERIFY_KEY=" in skill
    assert "`4`" in skill, "the skill must know what exit 4 means"


# ============================================================
# visual-discipline-check.py -- a verdict names the engines that ran
# ============================================================

@pytest.fixture
def vdc():
    return _load("scripts/visual-discipline-check.py", "vdc_under_test")


def _degrade_deep(vdc, monkeypatch, note="deep engine unavailable: no Node"):
    """The exact shape `impeccable_engine.deep_findings` returns when the CLI
    will not resolve: no findings, plus a note nobody was reading."""
    monkeypatch.setattr(vdc.impeccable_engine, "deep_findings",
                        lambda root, profile_override=None: ([], note))


def test_a_baseline_check_refuses_when_only_one_engine_ran(vdc, tmp_path, monkeypatch, capsys):
    """`baseline record` already refuses in this exact condition. `check` -- the
    half that gates CI -- printed the green line and exited 0."""
    _degrade_deep(vdc, monkeypatch)
    (tmp_path / "a.html").write_text("<html><body>hi</body></html>")
    args = types.SimpleNamespace(path=str(tmp_path), action="check", strict=False,
                                 profile=None, include_internal=False, deep=True)

    rc = vdc._cmd_baseline(args)

    captured = capsys.readouterr()
    assert rc == 2, "a pass here asserts coverage that did not happen"
    assert "No findings above the baseline" not in captured.out
    assert "refusing" in captured.err


def test_a_baseline_check_still_passes_when_both_engines_ran(vdc, tmp_path, monkeypatch, capsys):
    """The refusal must not have become a wall: a healthy run still gates."""
    monkeypatch.setattr(vdc.impeccable_engine, "deep_findings",
                        lambda root, profile_override=None: ([], ""))
    monkeypatch.setattr(vdc.impeccable_engine, "load_baseline", dict)
    (tmp_path / "a.html").write_text("<html><body>hi</body></html>")
    args = types.SimpleNamespace(path=str(tmp_path), action="check", strict=False,
                                 profile=None, include_internal=False, deep=True)

    rc = vdc._cmd_baseline(args)

    assert rc == 0
    assert "No findings above the baseline" in capsys.readouterr().out


def test_the_degradation_note_reaches_the_caller(vdc, tmp_path, monkeypatch):
    """`_run_audit` discarded it (`deep_map, _ = ...`), which is why every
    caller could only assert deep coverage on faith."""
    _degrade_deep(vdc, monkeypatch, note="npx not found")
    (tmp_path / "a.html").write_text("<html><body>hi</body></html>")

    _, _, note = vdc._run_audit(tmp_path, strict=False, deep=True, profile=None,
                               use_baseline=False, include_internal=False)

    assert note == "npx not found"


def test_a_clean_deep_run_returns_no_note(vdc, tmp_path, monkeypatch):
    """The refusal above keys on this value, so an always-truthy note would
    turn the gate permanently red."""
    monkeypatch.setattr(vdc.impeccable_engine, "deep_findings",
                        lambda root, profile_override=None: ([], ""))
    (tmp_path / "a.html").write_text("<html><body>hi</body></html>")

    _, _, note = vdc._run_audit(tmp_path, strict=False, deep=True, profile=None,
                               use_baseline=False, include_internal=False)

    assert not note


def test_a_fully_baselined_file_is_still_counted_as_scanned(vdc, tmp_path, monkeypatch):
    """It contributed no surviving finding and no `_empty` marker, so it fell
    out of the results entirely -- indistinguishable in the output from a file
    that was never visited."""
    scanned = tmp_path / "a.html"
    scanned.write_text("<html><style>font-family: Inter, sans-serif;</style></html>")
    clean = tmp_path / "b.html"
    clean.write_text("<html><body>hi</body></html>")
    assert clean.exists()

    key = vdc.impeccable_engine.relative_path(scanned)
    monkeypatch.setattr(vdc.impeccable_engine, "load_baseline",
                        lambda: {key: {"forbidden_font": 99}})

    results, _, _ = vdc._run_audit(tmp_path, strict=False, deep=False, profile=None,
                                   use_baseline=True, include_internal=False)

    sources = {r["source"] for r in results}
    assert key in sources, "a file whose findings were all frozen still got read"
    assert len(results) == 2


def test_a_clean_file_is_still_counted(vdc, tmp_path):
    """The `_empty` marker this replaced existed for a reason; keep its effect."""
    (tmp_path / "b.html").write_text("<html><body>hi</body></html>")

    results, _, _ = vdc._run_audit(tmp_path, strict=False, deep=False, profile=None,
                                   use_baseline=False, include_internal=False)

    assert len(results) == 1
    assert results[0]["summary"]["total_findings"] == 0


def test_the_summary_line_names_the_engines_that_produced_the_numbers(vdc):
    """`engines = "regex + deep" if args.deep else "regex"` read off the FLAG,
    so a degraded run advertised an engine that contributed nothing."""
    src = (ROOT / "scripts" / "visual-discipline-check.py").read_text()
    assert 'engines = "regex + deep" if args.deep else "regex"' not in src
    assert "the deep engine did not run" in src


# ============================================================
# verify-skills-lock.py -- the guard that accepted the repository root
# ============================================================

@pytest.fixture
def vsl():
    return _load("scripts/verify-skills-lock.py", "vsl_under_test")


@pytest.mark.parametrize("skill_path", [
    "x/../SKILL.md",
    ".claude/skills/foo/../../../SKILL.md",
    "./SKILL.md",
])
def test_a_path_that_resolves_to_the_root_is_refused(vsl, tmp_path, skill_path):
    """`tree != root and root not in tree.parents` short-circuited to False when
    tree WAS the root, so `--relock` would pin a sha256 over the whole
    repository -- `.git` and `.venv` included -- and fail every check after."""
    assert vsl._vendored_dir(tmp_path, {"skillPath": skill_path}) is None


def test_a_real_vendored_tree_still_resolves(vsl, tmp_path):
    """The guard must reject the root without rejecting the skills."""
    tree = tmp_path / ".claude" / "skills" / "census"
    tree.mkdir(parents=True)

    got = vsl._vendored_dir(tmp_path, {"skillPath": ".claude/skills/census/SKILL.md"})

    assert got == tree.resolve()


@pytest.mark.parametrize("skill_path", ["SKILL.md", "../outside/SKILL.md", ""])
def test_the_pre_existing_refusals_still_hold(vsl, tmp_path, skill_path):
    assert vsl._vendored_dir(tmp_path, {"skillPath": skill_path}) is None


# ============================================================
# watchdog_core.load_cadence -- the watchdog must not be what goes silent
# ============================================================

@pytest.fixture
def wd():
    from scripts import watchdog_core
    return watchdog_core


@pytest.mark.parametrize("bad", [None, "fast", [], {"nested": 1}])
def test_a_malformed_cadence_value_does_not_kill_the_watchdog(wd, tmp_path, monkeypatch, bad):
    """`int(entry.get("expected", DEFAULT))` raised TypeError on a YAML null and
    ValueError on a word. It escaped `load_cadence`, escaped `check_once`, and
    reached the bridge daemon's per-tick handler, which logs and carries on --
    so one typo disabled the watchdog on every tick, quietly."""
    monkeypatch.setattr(wd, "load_expected", lambda root: ("sentinel",))
    fake = types.ModuleType("scripts.bridge_daemon.config")
    fake.load_config = lambda root: {
        "daemon": {"watchdog": {"cadence": {"sentinel": {"expected": bad}}}}}
    monkeypatch.setitem(sys.modules, "scripts.bridge_daemon.config", fake)

    cadence = wd.load_cadence(tmp_path)

    assert cadence == {"sentinel": (wd.DEFAULT_EXPECTED_S, wd.DEFAULT_GRACE_S)}


def test_a_malformed_cadence_value_is_reported(wd, tmp_path, monkeypatch, caplog):
    """Falling back silently is the other half of the same defect: the operator
    would see default cadence and no reason for it."""
    monkeypatch.setattr(wd, "load_expected", lambda root: ("sentinel",))
    fake = types.ModuleType("scripts.bridge_daemon.config")
    fake.load_config = lambda root: {
        "daemon": {"watchdog": {"cadence": {"sentinel": {"grace": "soon"}}}}}
    monkeypatch.setitem(sys.modules, "scripts.bridge_daemon.config", fake)

    with caplog.at_level(logging.WARNING):
        wd.load_cadence(tmp_path)

    assert any("cadence.sentinel.grace" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("cfg, malformed_at", [
    # `watchdog: off` -- one YAML typo turning a mapping into a scalar.
    ({"daemon": {"watchdog": "off"}}, "watchdog"),
    ({"daemon": "off"}, "daemon"),
    ({"daemon": {"watchdog": {"cadence": "default"}}}, "cadence"),
    ({"daemon": {"watchdog": ["off"]}}, "watchdog"),
])
def test_a_malformed_cadence_CONTAINER_does_not_kill_the_watchdog(
        wd, tmp_path, monkeypatch, caplog, cfg, malformed_at):
    """The other half of the same defect, and the half nothing measured.

    The four cases above are malformed CONTAINERS, not malformed values: a level
    of the `daemon.watchdog.cadence` chain that is not a mapping. `_seconds`
    guards the leaf; the chain walk guards these, and the production comment
    says so outright ("fixed for malformed VALUES and left open for malformed
    CONTAINERS"). Before that walk was guarded, `.get` on a str raised
    AttributeError out of `load_cadence`, out of `check_once`, and into the
    bridge daemon's per-tick handler, which logs and carries on -- so the 2-minute
    tick did nothing forever while the daemon reported itself healthy.

    MEASURED 2026-09-01: deleting BOTH container branches left this file green at
    34 passed. The parametrized value test above cannot reach them, because every
    one of its inputs sits at the leaf with the chain intact.
    """
    monkeypatch.setattr(wd, "load_expected", lambda root: ("sentinel",))
    fake = types.ModuleType("scripts.bridge_daemon.config")
    fake.load_config = lambda root: cfg
    monkeypatch.setitem(sys.modules, "scripts.bridge_daemon.config", fake)

    with caplog.at_level(logging.WARNING):
        cadence = wd.load_cadence(tmp_path)

    assert cadence == {"sentinel": (wd.DEFAULT_EXPECTED_S, wd.DEFAULT_GRACE_S)}
    messages = [r.getMessage() for r in caplog.records]
    assert any("malformed" in m or "not a mapping" in m for m in messages), (
        f"the watchdog fell back to defaults over a malformed {malformed_at!r} "
        f"level and said nothing: {messages}")


def test_a_well_formed_config_logs_no_malformed_warning(wd, tmp_path, monkeypatch, caplog):
    """Anti-over-reporting: the container guards must not fire on a good config,
    or the warning above stops meaning anything."""
    monkeypatch.setattr(wd, "load_expected", lambda root: ("sentinel",))
    fake = types.ModuleType("scripts.bridge_daemon.config")
    fake.load_config = lambda root: {
        "daemon": {"watchdog": {"cadence": {"sentinel": {"expected": 900, "grace": 300}}}}}
    monkeypatch.setitem(sys.modules, "scripts.bridge_daemon.config", fake)

    with caplog.at_level(logging.WARNING):
        assert wd.load_cadence(tmp_path) == {"sentinel": (900, 300)}

    assert [r.getMessage() for r in caplog.records] == []


def test_an_absent_cadence_section_is_not_a_malformed_one(wd, tmp_path, monkeypatch, caplog):
    """`cadence:` simply not configured is the ordinary case, not a defect.

    It reaches the same `node is None` path the malformed cases exit through, so
    without this the guard could report every default-cadence host as malformed.
    """
    monkeypatch.setattr(wd, "load_expected", lambda root: ("sentinel",))
    fake = types.ModuleType("scripts.bridge_daemon.config")
    fake.load_config = lambda root: {"daemon": {"watchdog": {}}}
    monkeypatch.setitem(sys.modules, "scripts.bridge_daemon.config", fake)

    with caplog.at_level(logging.WARNING):
        cadence = wd.load_cadence(tmp_path)

    assert cadence == {"sentinel": (wd.DEFAULT_EXPECTED_S, wd.DEFAULT_GRACE_S)}
    assert [r.getMessage() for r in caplog.records] == []


def test_a_good_cadence_value_is_honoured(wd, tmp_path, monkeypatch):
    """The tolerance must not have become an override."""
    monkeypatch.setattr(wd, "load_expected", lambda root: ("sentinel",))
    fake = types.ModuleType("scripts.bridge_daemon.config")
    fake.load_config = lambda root: {
        "daemon": {"watchdog": {"cadence": {"sentinel": {"expected": 900, "grace": 300}}}}}
    monkeypatch.setitem(sys.modules, "scripts.bridge_daemon.config", fake)

    assert wd.load_cadence(tmp_path) == {"sentinel": (900, 300)}


def test_a_failed_cadence_config_read_is_logged(wd, tmp_path, monkeypatch, caplog):
    """Its two siblings in the same file log here; this one bound nothing and
    said nothing, so a daemon on a long cadence was classified silent and
    alerted on, with no line anywhere naming the cause."""
    monkeypatch.setattr(wd, "load_expected", lambda root: ("sentinel",))
    fake = types.ModuleType("scripts.bridge_daemon.config")

    def _boom(root):
        raise RuntimeError("config layer exploded")

    fake.load_config = _boom
    monkeypatch.setitem(sys.modules, "scripts.bridge_daemon.config", fake)

    with caplog.at_level(logging.DEBUG):
        cadence = wd.load_cadence(tmp_path)

    assert cadence == {"sentinel": (wd.DEFAULT_EXPECTED_S, wd.DEFAULT_GRACE_S)}
    assert any("cadence config read failed" in r.getMessage() for r in caplog.records)
