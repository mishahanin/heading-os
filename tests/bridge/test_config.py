import os

import pytest
import yaml

from scripts.bridge_daemon.config import (
    ConfigState,
    list_snapshots,
    load_config,
    revert_config,
    revert_config_to,
    snapshot_config,
)


def test_load_corporate_only(workspace_root, monkeypatch):
    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    corp.parent.mkdir(parents=True)
    corp.write_text("version: 1\nrefresh:\n  default: 30\n  email: 300\n")
    cfg = load_config(workspace_root)
    assert cfg["refresh"]["default"] == 30
    assert cfg["refresh"]["email"] == 300
    assert cfg["version"] == 1


def test_user_overrides_corporate(workspace_root):
    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    corp.parent.mkdir(parents=True)
    corp.write_text("refresh:\n  email: 300\n  inflight: 60\n")
    user = workspace_root / ".daemon-state" / "config.yaml"
    user.write_text("refresh:\n  email: 60\n")  # user overrides email to 60s
    cfg = load_config(workspace_root)
    assert cfg["refresh"]["email"] == 60   # user wins
    assert cfg["refresh"]["inflight"] == 60  # corporate retained


def _snapshot_with_user(workspace_root, user_cfg):
    """Write the USER layer, then snapshot the merged result.

    Since 2026-08-24 a snapshot holds the two layers apart and a revert restores
    only `user`, so a test that reverts and expects a value back has to have put
    that value in the user layer. Handing it to `snapshot_config` as part of the
    merged dict is not enough any more, and that is the point of the change:
    the old revert could not tell an override from a corporate default, so it
    restored both and froze the corporate half forever.
    """
    path = workspace_root / ".daemon-state" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(user_cfg), encoding="utf-8")
    return snapshot_config(workspace_root, load_config(workspace_root))


# Phase 1.154 - snapshot + revert tests.

def test_snapshot_writes_yaml(workspace_root):
    cfg = load_config(workspace_root)
    out = snapshot_config(workspace_root, cfg)
    assert out.exists()
    assert out.suffix == ".yaml"
    assert out.parent.name == "config-history"
    # Verify it can be round-tripped through YAML.
    import yaml as _y
    reloaded = _y.safe_load(out.read_text())
    assert reloaded["schema"] == 2
    assert reloaded["merged"]["refresh"]["email"] == cfg["refresh"]["email"]
    assert reloaded["corporate"] == {} and reloaded["user"] == {}


def test_snapshot_trims_to_keep_3(workspace_root):
    # No sleep between writes. Snapshot names are '{seq:09d}_{stamp}.yaml' with a
    # monotonic sequence prefix, so the lexicographic sort these assertions rely on
    # is correct by construction (see snapshot_config). The eight sleep(1.05) call
    # sites this file carried until 2026-08-20 — thirteen executions, two of them
    # inside loops — were guarding a wall-clock-only filename that no longer
    # exists. Re-measured 2026-08-20 on this file alone: 15.13s before, 0.53s
    # after, 29 passed either way.
    for i in range(5):
        snapshot_config(workspace_root, {"iteration": i})
    snaps = list_snapshots(workspace_root)
    assert len(snaps) == 3, snaps


def test_list_snapshots_newest_first(workspace_root):
    snapshot_config(workspace_root, {"order": "first"})
    snapshot_config(workspace_root, {"order": "second"})
    snaps = list_snapshots(workspace_root)
    assert len(snaps) == 2
    assert snaps[0].name > snaps[1].name  # newest first by ts-prefix sort


def test_revert_config_restores_prior(workspace_root):
    _snapshot_with_user(workspace_root, {"refresh": {"email": 100}})
    _snapshot_with_user(workspace_root, {"refresh": {"email": 999}})
    restored = revert_config(workspace_root)
    user_cfg = workspace_root / ".daemon-state" / "config.yaml"
    assert user_cfg.exists()
    # The snapshot is the layered document; the user layer is one part of
    # it, so the two files are no longer byte-identical.
    assert yaml.safe_load(user_cfg.read_text()) == \
        yaml.safe_load(restored.read_text())["user"]
    cfg = load_config(workspace_root)
    assert cfg["refresh"]["email"] == 100


def test_revert_config_requires_two_snapshots(workspace_root):
    snapshot_config(workspace_root, {"only": 1})
    with pytest.raises(RuntimeError, match="at least 2 config snapshots"):
        revert_config(workspace_root)


def test_revert_config_zero_snapshots(workspace_root):
    with pytest.raises(RuntimeError, match="at least 2 config snapshots"):
        revert_config(workspace_root)


def test_revert_config_to_specific_snapshot(workspace_root):
    _snapshot_with_user(workspace_root, {"refresh": {"email": 100}})
    _snapshot_with_user(workspace_root, {"refresh": {"email": 200}})
    _snapshot_with_user(workspace_root, {"refresh": {"email": 300}})
    snaps = list_snapshots(workspace_root)
    # Pick the oldest snapshot explicitly (newest-first sort -> index 2)
    oldest_name = snaps[2].name
    restored = revert_config_to(workspace_root, oldest_name)
    assert restored.name == oldest_name
    cfg = load_config(workspace_root)
    assert cfg["refresh"]["email"] == 100


def test_revert_config_to_unknown_snapshot(workspace_root):
    snapshot_config(workspace_root, {"a": 1})
    with pytest.raises(RuntimeError, match="not found"):
        revert_config_to(workspace_root, "does-not-exist.yaml")


def test_revert_config_to_writes_user_override(workspace_root):
    _snapshot_with_user(workspace_root, {"refresh": {"email": 100}})
    snaps = list_snapshots(workspace_root)
    revert_config_to(workspace_root, snaps[0].name)
    user_cfg = workspace_root / ".daemon-state" / "config.yaml"
    assert user_cfg.exists()
    # Round-trip: load_config should see the restored value.
    cfg = load_config(workspace_root)
    assert cfg["refresh"]["email"] == 100


# Regression: rapid snapshots within the same wall-clock second must not
# collide, and their lexicographic name-sort (which the revert logic treats
# as chronological) must stay correct even when the wall clock does not
# advance monotonically. Before the monotonic-sequence-prefix fix, three
# writes inside one second shared the same %Y%m%dT%H%M%SZ filename and
# overwrote each other (1 file instead of 3); and on WSL the clock could
# step backward across writes, leaving the newest file sorting before an
# older one. Both broke keep-3 / revert-to-prior assertions.

def test_rapid_snapshots_do_not_collide(workspace_root):
    # Tight loop, no sleep -> all three writes land in the same second.
    for i in range(3):
        snapshot_config(workspace_root, {"refresh": {"email": 100 + i}})
    snaps = list_snapshots(workspace_root)
    assert len(snaps) == 3, [p.name for p in snaps]
    # All filenames distinct.
    names = [p.name for p in snaps]
    assert len(set(names)) == 3, names
    # Newest-first ordering must remain chronological (write order i=0,1,2).
    contents = [yaml.safe_load(p.read_text())["merged"]["refresh"]["email"]
                for p in snaps]
    assert contents == [102, 101, 100], contents


def test_rapid_snapshots_revert_to_each(workspace_root):
    # Three rapid snapshots in the same second, then revert to each by name.
    for i in range(3):
        _snapshot_with_user(workspace_root, {"refresh": {"email": 100 + i}})
    snaps = list_snapshots(workspace_root)
    assert len(snaps) == 3
    for snap in snaps:
        expected = yaml.safe_load(snap.read_text())["user"]["refresh"]["email"]
        restored = revert_config_to(workspace_root, snap.name)
        assert restored.name == snap.name
        assert load_config(workspace_root)["refresh"]["email"] == expected


# Phase 1.165: path-traversal hardening.

@pytest.mark.parametrize("name", [
    "../../etc/passwd",
    "/etc/passwd",
    "sub/file.yaml",
    "back\\slash.yaml",
    "..",
    ".",
    ".hidden.yaml",
    "",
])
def test_revert_config_to_rejects_unsafe_names(workspace_root, name):
    snapshot_config(workspace_root, {"a": 1})  # at least one snapshot exists
    with pytest.raises(RuntimeError):
        revert_config_to(workspace_root, name)


def test_the_unsafe_names_are_refused_by_the_guard_not_by_absence(workspace_root):
    """The case above is a straw man, and this is the case it was missing.

    Every name it lists also fails the `target.is_file()` check further down,
    because none of them names a file inside `config-history`, and that check
    raises `RuntimeError` too. So `pytest.raises(RuntimeError)` cannot tell a
    traversal REFUSAL from an ordinary "snapshot not found". Deleting the two
    prefix guards in `revert_config_to` (the separator/dot-name check and the
    leading-dot check, config.py lines 371-379) was measured on 2026-08-31:

        owner tests/bridge/test_config.py: 29 passed in 1.02s
        tests/bridge                     : 1312 passed, 1 skipped in 45.61s
        VERDICT: SURVIVED

    The scope of that measurement is the owning file plus every test in
    `tests/bridge`, which is what was run. Nothing in either made those nine
    lines refuse anything.

    The distinction is not cosmetic. `is_file()` is a question about the
    filesystem, so it answers differently the moment a matching file exists:
    a `.hidden.yaml` dropped into the history directory (by an editor
    swapfile, a partial rsync, a `.#name` lock) would be RESTORED into the
    live user config by a guardless revert, and `sub/file.yaml` would reach
    outside the directory the moment `sub/` existed. This asserts the guard's
    own message, which only the guard can produce, and creates the file first
    so the fallback branch is genuinely unreachable.
    """
    history = workspace_root / ".daemon-state" / "config-history"
    history.mkdir(parents=True, exist_ok=True)
    # Make each name resolve to a real file, so "not found" cannot be the
    # reason for the refusal.
    (history / ".hidden.yaml").write_text("key: smuggled\n", encoding="utf-8")
    (history / "sub").mkdir()
    (history / "sub" / "file.yaml").write_text("key: smuggled\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="starts with '.'"):
        revert_config_to(workspace_root, ".hidden.yaml")
    with pytest.raises(RuntimeError, match="path separators"):
        revert_config_to(workspace_root, "sub/file.yaml")
    with pytest.raises(RuntimeError, match="path separators"):
        revert_config_to(workspace_root, "back\\slash.yaml")
    for dot in ("..", "."):
        with pytest.raises(RuntimeError, match="path separators"):
            revert_config_to(workspace_root, dot)
    with pytest.raises(RuntimeError, match="snapshot name is required"):
        revert_config_to(workspace_root, "")

    # And the user layer was never written by any of the above.
    assert not (workspace_root / ".daemon-state" / "config.yaml").exists()


def test_an_absolute_path_cannot_be_read_out_of_the_filesystem(workspace_root):
    """`/etc/passwd` is the one unsafe name that IS a file on this host.

    `history_dir / "/etc/passwd"` is `/etc/passwd` under pathlib's join
    semantics, so `is_file()` says yes and only a guard stands between the
    revert and writing the host's password file into the daemon's config
    layer. Asserting the message pins WHICH guard stopped it; the two prefix
    checks and the `relative_to` containment check are separate lines and
    either could be the one deleted next.
    """
    snapshot_config(workspace_root, {"a": 1})
    with pytest.raises(RuntimeError, match="path separators|escapes history"):
        revert_config_to(workspace_root, "/etc/passwd")
    assert not (workspace_root / ".daemon-state" / "config.yaml").exists()


# A broken config layer is SKIPPED, never fatal (config.py `_load_layer`).
# That is a deliberate trade recorded in the function's docstring: an override
# saved mid-edit used to stop the daemon booting at all, which also killed the
# reconcile tick that would have picked up the corrected file. Nothing tested
# it. Two mutations, measured 2026-08-31:
#
#   narrowing the except clause to `(OSError,)`, so a YAMLError propagates:
#     owner tests/bridge/test_config.py: 29 passed in 1.21s
#     tests/bridge                     : 1312 passed, 1 skipped in 51.56s
#     VERDICT: SURVIVED
#
#   deleting the `isinstance(parsed, dict)` guard:
#     owner tests/bridge/test_config.py: 29 passed in 1.14s
#     tests/bridge                     : 1312 passed, 1 skipped in 50.62s
#     VERDICT: SURVIVED
#
# Both were run against the owning file and all of `tests/bridge`, which is
# the scope of the "SURVIVED" above. The first reinstates the exact boot outage
# the docstring describes; the second turns a YAML list into an
# `AttributeError` inside `_deep_merge`, from the same call site. Every test in
# this file writes syntactically perfect YAML, which is why neither was
# visible: the corpus had no broken member at all.

BROKEN_LAYERS = {
    "unclosed-bracket": "refresh: [1, 2\n",
    "tab-indent": "refresh:\n\temail: 60\n",
    "duplicate-block-mapping-key": "a: 1\n b: 2\n  c: 3\n",
    "half-written": "refresh:\n  email:\n    - \n  -\n",
    "a-yaml-list": "- one\n- two\n",
    "a-bare-scalar": "just a string\n",
    "a-number": "42\n",
    "empty": "",
}


@pytest.mark.parametrize("label,text", sorted(BROKEN_LAYERS.items()))
def test_a_broken_user_layer_does_not_stop_the_daemon(workspace_root, label, text):
    """The documented promise: skipped, never fatal, layers below retained."""
    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    corp.parent.mkdir(parents=True)
    corp.write_text("version: 7\nrefresh:\n  email: 300\n", encoding="utf-8")
    (workspace_root / ".daemon-state" / "config.yaml").write_text(
        text, encoding="utf-8")

    cfg = load_config(workspace_root)          # must not raise

    assert cfg["version"] == 7, f"the corporate layer was lost on {label!r}"
    assert cfg["refresh"]["email"] == 300
    assert cfg["refresh"]["inflight"] == 60, "DEFAULTS were lost too"


@pytest.mark.parametrize("label,text", sorted(BROKEN_LAYERS.items()))
def test_a_broken_corporate_layer_does_not_stop_the_daemon(workspace_root, label, text):
    """Same for the layer above, where a bad `/push-updates` lands it."""
    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    corp.parent.mkdir(parents=True)
    corp.write_text(text, encoding="utf-8")
    (workspace_root / ".daemon-state" / "config.yaml").write_text(
        "refresh:\n  email: 45\n", encoding="utf-8")

    cfg = load_config(workspace_root)          # must not raise

    assert cfg["refresh"]["email"] == 45, f"the user layer was lost on {label!r}"
    assert cfg["version"] == 0, "should have fallen back to DEFAULTS"


@pytest.mark.parametrize("label,text", sorted(BROKEN_LAYERS.items()))
def test_a_broken_layer_says_so_in_the_log(workspace_root, label, text, caplog):
    """Failing open silently is a different defect from failing open loudly.

    An operator whose override stopped taking effect has nothing else to go
    on: `load_config` returns a perfectly valid dict either way. `empty` is
    the one member with nothing to report, since an empty file is a legal
    empty mapping rather than a broken one.
    """
    import logging

    (workspace_root / ".daemon-state" / "config.yaml").write_text(
        text, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="scripts.bridge_daemon.config"):
        load_config(workspace_root)
    if label == "empty":
        return
    assert caplog.records, f"a {label!r} layer was skipped in silence"
    assert any("user" in r.getMessage() for r in caplog.records), (
        f"the warning does not name the layer that was dropped: "
        f"{[r.getMessage() for r in caplog.records]}")


def test_a_broken_layer_still_reloads_after_it_is_corrected(workspace_root):
    """The reason failing open was chosen, and the half nothing measured.

    The docstring's argument is that a fatal parse error 'killed the
    reconcile tick that would have picked up the corrected file'. That is a
    claim about `ConfigState`, not about `load_config`, and no test followed
    it that far: booting on a broken layer, then fixing the file, then
    reconciling.
    """
    user = workspace_root / ".daemon-state" / "config.yaml"
    user.write_text("refresh: [1, 2\n", encoding="utf-8")

    cs = ConfigState(workspace_root)            # must not raise
    assert cs.config["refresh"]["email"] == 300  # DEFAULTS

    user.write_text("refresh:\n  email: 42\n", encoding="utf-8")
    _bump_mtime(user)
    assert cs.reconcile() is True
    assert cs.config["refresh"]["email"] == 42


def test_a_broken_layer_is_not_snapshotted_as_content(workspace_root):
    """A snapshot of an unparseable layer must record `{}`, not raise.

    `snapshot_config` runs at boot and calls `_load_layer` on both layers,
    so the fail-open branch is reachable from there too. If it raised, the
    daemon would die at boot for the same reason, one function along.
    """
    user = workspace_root / ".daemon-state" / "config.yaml"
    user.write_text("refresh: [1, 2\n", encoding="utf-8")
    out = snapshot_config(workspace_root, load_config(workspace_root))
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert doc["user"] == {}
    assert doc["schema"] == 2


def test_revert_config_to_rejects_none(workspace_root):
    snapshot_config(workspace_root, {"a": 1})
    with pytest.raises(RuntimeError):
        revert_config_to(workspace_root, None)


# Phase B - ConfigState reconciliation tests.


def _bump_mtime(path, seconds=2.0):
    """Move a file's mtime forward without sleeping.

    reconcile() compares stat() mtimes, so these tests need the MTIME to move,
    not the wall clock to advance. The sleep(1.05) they used until 2026-08-20
    bought that at ~1s a call and was still only as coarse as the filesystem
    (the old comment cited 1s granularity on FAT/NTFS). An explicit utime is
    free and deterministic on every filesystem, so deleting the sleep here does
    not trade a slow test for a flaky one.
    """
    t = path.stat().st_mtime + seconds
    os.utime(path, (t, t))


def test_config_state_loads_at_init(workspace_root):
    """ConfigState picks up the same merged config as load_config()."""
    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    corp.parent.mkdir(parents=True)
    corp.write_text("version: 2\nrefresh:\n  email: 200\n")
    cs = ConfigState(workspace_root)
    assert cs.config["version"] == 2
    assert cs.config["refresh"]["email"] == 200
    assert cs.reload_count == 0
    assert cs.last_reload_at is None


def test_reconcile_returns_false_when_nothing_changed(workspace_root):
    """No mtime change -> reconcile() is a noop returning False."""
    cs = ConfigState(workspace_root)
    assert cs.reconcile() is False
    assert cs.reload_count == 0


def test_reconcile_returns_true_on_corporate_mtime_change(workspace_root):
    """Touching corporate/daemon/config.yaml mtime triggers a reload."""
    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    corp.parent.mkdir(parents=True)
    corp.write_text("version: 1\nrefresh:\n  email: 100\n")
    cs = ConfigState(workspace_root)
    assert cs.config["refresh"]["email"] == 100

    # Simulate /push-updates landing a new corporate config.
    corp.write_text("version: 2\nrefresh:\n  email: 250\n")
    _bump_mtime(corp)

    assert cs.reconcile() is True
    assert cs.config["version"] == 2
    assert cs.config["refresh"]["email"] == 250
    assert cs.reload_count == 1
    assert cs.last_reload_at is not None


def test_reconcile_returns_true_on_user_override_change(workspace_root):
    """Touching .daemon-state/config.yaml (per-user override) also triggers a reload."""
    user = workspace_root / ".daemon-state" / "config.yaml"
    user.write_text("refresh:\n  email: 60\n")
    cs = ConfigState(workspace_root)
    assert cs.config["refresh"]["email"] == 60

    user.write_text("refresh:\n  email: 45\n")
    _bump_mtime(user)
    assert cs.reconcile() is True
    assert cs.config["refresh"]["email"] == 45


def test_reconcile_counts_each_reload(workspace_root):
    """reload_count increments on each successful reconcile."""
    user = workspace_root / ".daemon-state" / "config.yaml"
    user.write_text("refresh:\n  email: 60\n")
    cs = ConfigState(workspace_root)
    for i in range(3):
        user.write_text(f"refresh:\n  email: {100 + i}\n")
        _bump_mtime(user)
        assert cs.reconcile() is True
    assert cs.reload_count == 3


def test_reconcile_handles_corporate_appearing_after_boot(workspace_root):
    """Daemon booted without a corporate config; one lands later -> reload."""
    cs = ConfigState(workspace_root)
    # No corporate config exists at boot -> defaults only.
    assert cs.config["version"] == 0

    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    corp.parent.mkdir(parents=True)
    corp.write_text("version: 9\n")
    assert cs.reconcile() is True
    assert cs.config["version"] == 9


def test_reconcile_handles_corporate_disappearing(workspace_root):
    """Corporate config existed at boot, was deleted (someone reverted upstream) ->
    reload returns True and config falls back to defaults + user overrides."""
    corp = workspace_root / "corporate" / "daemon" / "config.yaml"
    corp.parent.mkdir(parents=True)
    corp.write_text("version: 5\n")
    cs = ConfigState(workspace_root)
    assert cs.config["version"] == 5

    corp.unlink()
    assert cs.reconcile() is True
    assert cs.config["version"] == 0  # back to DEFAULTS
