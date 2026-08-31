"""Shard `scripts-11-p3`: four publish and provision paths that reported more
than they did.

The headline: `provision-exec.py` builds `.claude/settings.local.json` for a new
executive -- the permission list and every hook, including the PreToolUse
dispatcher whose own docstring promises "the same guard set" as the CEO's
workspace. Two steps later `init_git` writes a `.gitignore` containing
`.claude/settings.local.json`, and `main` closes by telling the exec to obtain
the workspace with `git clone`. The file therefore never left the admin machine.
Every provisioned executive started with no permissions and no guards at all,
and the provisioner printed `[ok] settings.local.json generated` on the way past.

`scripts/setup-platform.sh` already solves this on the exec's own machine: it
copies `.claude/settings.local.<platform>.json` onto the active name. That
template name is not gitignored, so the provisioner now writes it too, and the
closing instructions name the step.

The report's own framing of that finding -- `python3` is not portable to
Windows -- is REFUTED here and pinned: the engine's own
`.claude/settings.local.windows.json` uses `python3` nineteen times and is the
shipped, working configuration, because these hooks run under the bash-flavoured
shell Claude Code spawns there. Emitting `python` on Windows would have diverged
from the file the generator is modelled on and fixed nothing observable.

The rest of the shard:

  - The per-exec CRM seed warned on failure and then marked the step done. The
    step is the idempotent script's only chance to fix an empty CRM repo, and a
    completed step is skipped forever after. The seed also configured no git
    identity in its temp clone, which is the failure most likely to fire, since
    `git commit` refuses outright without one. And its "nothing to commit"
    escape hatch was applied to `git add` and `git push` as well as to commit.
  - The registry step said it "Pushed registry update to corporate repo". The
    registry lives under the data root and classifies `private` precisely so it
    never syncs to an exec; the sentence described a leak.
  - `publish-corporate.py --verify` threw away `new_files` -- the files
    classified corporate that are ABSENT from the corporate repo -- and printed
    VERIFY OK. /push-updates runs that gate immediately before the corporate
    commit. It also counted files it had skipped without comparing among the
    files it called matching, and claimed a two-sided check while enumerating
    one side.
  - `bump_build`'s `files_changed` defaulted to 0 and `main` never passed it, so
    every BUILD.json the CLI ever wrote carried a hard number no method computed.
  - Exit code 6 is documented as "copy failed (filesystem error)" and was also
    returned for the untracked-files hygiene refusal, where nothing was copied
    and nothing failed.
  - `publish-service.py` checks every `include` entry from its manifest with
    `_contained`, then joins `downstream_repo` from the same manifest onto
    `workspace.parent` unchecked. An absolute or `../` value redirects the whole
    publish into a directory `copy_includes` rmtree's and overwrites.
  - `pull-service-state.py` read `_SVC["state_dirs"]` by subscript, so a config
    missing the key raised KeyError past `main`'s ValueError-only handler. Its
    config load also ran at import, where a malformed file could raise
    JSONDecodeError or AttributeError with no handler in scope at all. That load
    is `service_config()`, resolved on call, since 2026-08-31; `_SVC` and
    `_SVC_ERROR` are gone, and the tests below drive the function instead.

Fixed 2026-08-25.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import shlex
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# provision-exec: the guard set that never shipped
# ===========================================================================

@pytest.fixture
def px():
    return _load("px_11p3", "scripts/provision-exec.py")


class _Args:
    platform = "linux"
    name = "Jane Roe"
    email = "jane.roe@example.com"
    github_user = "janeroe"


def _settings(px, tmp_path, target_platform="linux"):
    args = _Args()
    args.platform = target_platform
    px.create_settings_local_json({"completed_steps": []}, args, tmp_path)
    return tmp_path / ".claude"


def test_a_tracked_platform_template_is_written_beside_the_active_file(px, tmp_path):
    """The whole finding: the active name is gitignored, so the clone got nothing."""
    claude = _settings(px, tmp_path)
    assert (claude / "settings.local.linux.json").exists(), (
        "only the gitignored active file was written; the exec's clone carries "
        "no permissions and no hooks"
    )


def test_the_template_matches_the_active_file(px, tmp_path):
    claude = _settings(px, tmp_path)
    assert (claude / "settings.local.linux.json").read_text(encoding="utf-8") == \
        (claude / "settings.local.json").read_text(encoding="utf-8")


def test_windows_gets_the_windows_template(px, tmp_path):
    claude = _settings(px, tmp_path, "windows")
    assert (claude / "settings.local.windows.json").exists()


def test_darwin_gets_the_linux_template(px, tmp_path):
    """setup-platform.sh routes Darwin to the linux template; match it exactly,
    or the exec's clone has no template the installer will look for."""
    claude = _settings(px, tmp_path, "darwin")
    assert (claude / "settings.local.linux.json").exists()
    assert not (claude / "settings.local.darwin.json").exists()


def test_the_template_names_are_the_ones_setup_platform_installs(px):
    """Two files, one pair of literals, and only this test holds them in step."""
    sh = (ROOT / "scripts" / "setup-platform.sh").read_text(encoding="utf-8")
    src = inspect.getsource(px.create_settings_local_json)
    for name in ("settings.local.linux.json", "settings.local.windows.json"):
        assert name in sh, f"setup-platform.sh no longer installs {name}"
        assert name in src, f"the provisioner no longer writes {name}"


def test_the_template_name_is_not_gitignored(px):
    """`.gitignore` must keep excluding the ACTIVE file and never the template."""
    gitignore_src = inspect.getsource(px.init_git)
    assert ".claude/settings.local.json\\n" in gitignore_src
    for name in ("settings.local.linux.json", "settings.local.windows.json"):
        assert name not in gitignore_src, (
            f"{name} is now ignored too; the template would stop shipping and "
            f"the exec would be back to no guards"
        )


def test_the_closing_instructions_name_the_installer(px):
    """A template nobody is told to install delivers nothing."""
    main_src = inspect.getsource(px.main)
    assert "setup-platform.sh" in main_src


# ---------------------------------------------------------------------------
# the hook command itself
# ---------------------------------------------------------------------------

def _run_hook(command: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run the emitted hook command body with this interpreter.

    `shlex.split` recovers the `-c` payload; the emitted `python3` is replaced
    with `sys.executable` so the test does not depend on what is on PATH.
    """
    parts = shlex.split(command)
    return subprocess.run([sys.executable, *parts[1:]], cwd=str(cwd),
                          capture_output=True, text=True)


def test_a_missing_hook_script_is_not_silent(px, tmp_path):
    """`p and runpy.run_path(...)` returned None and exited 0, so an exec whose
    .claude/hooks never arrived ran with every guard absent, silently."""
    result = _run_hook(px._dispatch_command(), tmp_path)
    assert result.returncode == 0, "a missing hook must not block the tool call"
    assert "_dispatch.py" in result.stderr
    assert "did not run" in result.stderr


def test_a_present_hook_script_still_runs(px, tmp_path):
    """Anchor: the warning branch must not shadow the running branch."""
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "_dispatch.py").write_text(
        "print('dispatched')\n", encoding="utf-8")
    result = _run_hook(px._dispatch_command(), tmp_path)
    assert "dispatched" in result.stdout
    assert result.stderr == ""


def test_the_hook_resolves_from_a_subdirectory(px, tmp_path):
    """Anchor: the self-resolving walk up the parents is the point of the form."""
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "_dispatch.py").write_text("print('dispatched')\n", encoding="utf-8")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert "dispatched" in _run_hook(px._dispatch_command(), deep).stdout


def test_the_interpreter_stays_python3_on_every_platform(px, tmp_path):
    """REFUTED report claim, pinned so it is not "fixed" later.

    The engine's own Windows settings use `python3` throughout; these hooks run
    under the bash-flavoured shell Claude Code spawns, not cmd.exe.
    """
    win = (ROOT / ".claude" / "settings.local.windows.json").read_text(encoding="utf-8")
    assert "python3" in win, (
        "the engine's Windows settings stopped using python3; if that changed "
        "for a real reason, the provisioner must change with it"
    )
    claude = _settings(px, tmp_path, "windows")
    blob = (claude / "settings.local.windows.json").read_text(encoding="utf-8")
    assert "python3 -c" in blob


# ===========================================================================
# provision-exec: the CRM seed that warned and called itself done
# ===========================================================================

def _fake_subprocess(fail_on=None, fail_text=""):
    """A `subprocess` shim. `fail_on` is a set of argv[1] tokens that fail."""
    fail_on = fail_on or set()
    calls: list[list[str]] = []

    def run(cmd, *_a, **_k):
        calls.append(list(cmd))
        token = cmd[1] if len(cmd) > 1 else ""
        rc = 1 if token in fail_on else 0
        if rc == 0 and cmd[:3] == ["gh", "repo", "clone"]:
            # The real clone creates the directory the seed then writes into.
            Path(cmd[4]).mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(
            args=cmd, returncode=rc, stdout="", stderr=fail_text if rc else "")

    shim = types.SimpleNamespace(run=run, CalledProcessError=subprocess.CalledProcessError)
    return shim, calls


def _seed(px, tmp_path, monkeypatch, **kwargs):
    shim, calls = _fake_subprocess(**kwargs)
    monkeypatch.setattr(px, "subprocess", shim)
    state = {"completed_steps": []}
    ok = px.create_crm_repo(state, _Args(), tmp_path, "jane-roe")
    return ok, state, calls


def test_a_failed_seed_leaves_the_step_open(px, tmp_path, monkeypatch, capsys):
    """The finding: marked done means the idempotent re-run skips the only step
    that could have fixed an empty CRM repo."""
    ok, state, _ = _seed(px, tmp_path, monkeypatch, fail_on={"push"})
    assert ok is True, "provisioning should carry on"
    assert "create_crm_repo" not in state["completed_steps"]
    out = capsys.readouterr().out
    assert "NOT seeded" in out


def test_a_failed_seed_does_not_print_the_success_line(px, tmp_path, monkeypatch,
                                                       capsys):
    _seed(px, tmp_path, monkeypatch, fail_on={"commit"})
    assert "repo seeded for" not in capsys.readouterr().out


def test_the_seed_clone_gets_a_git_identity(px, tmp_path, monkeypatch):
    """`git commit` refuses outright with no identity, and the temp clone
    inherits none from `init_git`, which configures the workspace repo only."""
    _ok, _state, calls = _seed(px, tmp_path, monkeypatch)
    configured = [c for c in calls if c[:2] == ["git", "config"]]
    assert ["git", "config", "user.name", "Jane Roe"] in configured
    assert ["git", "config", "user.email", "jane.roe@example.com"] in configured


def test_the_identity_is_set_before_the_commit(px, tmp_path, monkeypatch):
    """Order is the whole point; after the commit it changes nothing."""
    _ok, _state, calls = _seed(px, tmp_path, monkeypatch)
    tokens = [c[1] for c in calls if c[0] == "git"]
    assert tokens.index("config") < tokens.index("commit")


def test_a_clean_seed_marks_the_step_done(px, tmp_path, monkeypatch, capsys):
    """Anchor: the happy path must still be idempotent."""
    _ok, state, _ = _seed(px, tmp_path, monkeypatch)
    assert "create_crm_repo" in state["completed_steps"]
    assert "repo seeded for" in capsys.readouterr().out


def test_nothing_to_commit_is_still_a_clean_seed(px, tmp_path, monkeypatch):
    """Anchor: a re-seed of an already-seeded repo is a success, not a failure."""
    _ok, state, _ = _seed(px, tmp_path, monkeypatch, fail_on={"commit"},
                          fail_text="nothing to commit, working tree clean")
    assert "create_crm_repo" in state["completed_steps"]


def test_nothing_to_commit_does_not_excuse_a_failed_push(px, tmp_path, monkeypatch):
    """The escape hatch was applied to all three commands, so any failure whose
    text carried the phrase was read as success."""
    _ok, state, _ = _seed(px, tmp_path, monkeypatch, fail_on={"push"},
                          fail_text="rejected: nothing to commit here either")
    assert "create_crm_repo" not in state["completed_steps"]


# ===========================================================================
# provision-exec: the registry that said the wrong repo
# ===========================================================================

def test_the_registry_step_does_not_claim_the_corporate_repo(px):
    """The registry classifies `private`; a push to corporate would be a leak,
    and the success line said that is what had happened."""
    src = inspect.getsource(px.register_in_exec_registry)
    printed = [ln for ln in src.splitlines() if "Pushed registry update" in ln]
    assert printed, "the success line moved; re-anchor this test"
    assert not any("corporate" in ln for ln in printed), printed


# ===========================================================================
# publish-corporate
# ===========================================================================

@pytest.fixture
def pc(tmp_path, monkeypatch):
    mod = _load("pc_11p3", "scripts/publish-corporate.py")
    monkeypatch.setattr(mod, "CORPORATE_ROOT", tmp_path / "corp")
    monkeypatch.setattr(mod, "source_root", lambda p=tmp_path / "src": p)
    (tmp_path / "corp").mkdir()
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(mod, "operator_slug", lambda: "misha-hanin")
    return mod


def _build(pc) -> dict:
    return json.loads((pc.CORPORATE_ROOT / "BUILD.json").read_text(encoding="utf-8"))


def test_an_uncounted_bump_writes_no_files_changed(pc):
    """The finding: `main` never passed it, so every CLI bump stamped a hard 0."""
    assert pc.bump_build(summary="s") == 0
    assert "files_changed" not in _build(pc)


def test_a_previous_count_does_not_survive_into_the_next_build(pc):
    """`dict(cur)` carries every key forward, so omitting the update would leave
    the LAST build's count under the new build number."""
    (pc.CORPORATE_ROOT / "BUILD.json").write_text(
        json.dumps({"version": "1.0.0", "build": 4, "files_changed": 37}),
        encoding="utf-8")
    pc.bump_build()
    b = _build(pc)
    assert b["build"] == 5
    assert "files_changed" not in b


def test_a_counted_bump_records_the_count(pc):
    pc.bump_build(files_changed=12)
    assert _build(pc)["files_changed"] == 12


def test_a_zero_count_is_still_recordable(pc):
    """0 supplied is a measurement; 0 defaulted was a fabrication."""
    pc.bump_build(files_changed=0)
    assert _build(pc)["files_changed"] == 0


def test_the_cli_can_supply_the_count(pc, monkeypatch):
    monkeypatch.setattr(pc, "verify_admin_identity", lambda: None)
    monkeypatch.setattr(pc, "verify_corporate_repo", lambda: None)
    assert pc.main(["--bump-build", "--files-changed", "7"]) == 0
    assert _build(pc)["files_changed"] == 7


def test_an_unrelated_key_still_survives_a_bump(pc):
    """Anchor: the pop must remove one key, not rebuild the payload."""
    (pc.CORPORATE_ROOT / "BUILD.json").write_text(
        json.dumps({"build": 1, "history": [{"event": "x"}]}), encoding="utf-8")
    pc.bump_build()
    assert _build(pc)["history"] == [{"event": "x"}]


# ---------------------------------------------------------------------------
# --verify
# ---------------------------------------------------------------------------

def _corp_tree(pc, monkeypatch, files: dict[str, tuple[str | None, str | None]]):
    """files: rel -> (source text or None, corporate text or None)."""
    for rel, (src_text, dst_text) in files.items():
        if src_text is not None:
            p = pc.source_root() / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(src_text, encoding="utf-8")
        if dst_text is not None:
            p = pc.CORPORATE_ROOT / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(dst_text, encoding="utf-8")
    monkeypatch.setattr(pc, "list_tracked_files", lambda: list(files))
    monkeypatch.setattr(pc, "get_routing_destination", lambda _p: "corporate")


def test_a_file_absent_from_corporate_fails_the_verify(pc, monkeypatch, capsys):
    """The finding: /push-updates runs this gate right before the corporate
    commit, and it printed VERIFY OK over a file that had never been published."""
    _corp_tree(pc, monkeypatch, {"a.md": ("x", "x"), "b.md": ("y", None)})
    assert pc.mode_verify() == 7
    err = capsys.readouterr().err
    assert "b.md" in err
    assert "absent from corporate" in err


def test_the_ok_line_counts_only_what_was_compared(pc, monkeypatch, capsys):
    """A missing-in-source file is `continue`d without any comparison, and was
    then counted among the files the same run called matching."""
    _corp_tree(pc, monkeypatch, {"a.md": ("x", "x"), "gone.md": (None, "old")})
    assert pc.mode_verify() == 0
    out = capsys.readouterr().out
    assert "VERIFY OK: 1 corporate-classified file(s)" in out


def test_the_ok_line_names_what_it_did_not_look_at(pc, monkeypatch, capsys):
    """Only the source index is enumerated, so a corporate-only file is unseen."""
    _corp_tree(pc, monkeypatch, {"a.md": ("x", "x")})
    pc.mode_verify()
    assert "ONLY in the corporate repo" in capsys.readouterr().out


def test_a_differing_file_still_fails(pc, monkeypatch, capsys):
    """Anchor: the original failure mode must survive the rewrite."""
    _corp_tree(pc, monkeypatch, {"a.md": ("new", "old")})
    assert pc.mode_verify() == 7
    assert "differs" in capsys.readouterr().err


def test_an_identical_tree_still_passes(pc, monkeypatch):
    """Anchor: verify must not become impossible to pass."""
    _corp_tree(pc, monkeypatch, {"a.md": ("x", "x"), "b.md": ("y", "y")})
    assert pc.mode_verify() == 0


def test_an_orphan_is_still_only_a_warning(pc, monkeypatch, capsys):
    """Anchor: orphans are surfaced for manual cleanup, never a hard failure."""
    _corp_tree(pc, monkeypatch, {"gone.md": (None, "old")})
    assert pc.mode_verify() == 0
    assert "VERIFY WARNING" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# the shared exit code
# ---------------------------------------------------------------------------

def test_the_hygiene_refusal_has_its_own_exit_code(pc, monkeypatch, capsys):
    """Nothing was copied and nothing failed, so "copy failed (filesystem
    error)" described the wrong event every time this fired."""
    monkeypatch.setattr(pc, "corporate_gitattributes_ok", lambda: True)
    monkeypatch.setattr(pc, "list_tracked_files", list)
    monkeypatch.setattr(pc, "list_untracked_corporate_files", lambda: ["new.md"])
    assert pc.mode_copy() == 9
    assert "new.md" in capsys.readouterr().err


def test_every_exit_code_the_module_returns_is_documented(pc):
    """The docstring is the contract; a code missing from it is a lie by
    omission, which is how 6 came to mean two unrelated things."""
    src = (ROOT / "scripts" / "publish-corporate.py").read_text(encoding="utf-8")
    doc = src.split('"""')[1]
    documented = {int(ln.split()[0]) for ln in doc.splitlines()
                  if ln.strip() and ln.strip()[0].isdigit()}
    for code in (0, 2, 3, 4, 6, 7, 8, 9):
        assert code in documented, f"exit code {code} is not documented"


# ===========================================================================
# publish-service: the one manifest value nobody checked
# ===========================================================================

@pytest.fixture
def ps():
    return _load("ps_11p3", "scripts/publish-service.py")


def test_an_absolute_downstream_repo_is_refused(ps, tmp_path):
    """`Path('/a/b').parent / '/etc/x'` is `/etc/x`, and copy_includes rmtree's
    the destination before writing it."""
    with pytest.raises(ValueError, match="downstream_repo"):
        ps.downstream_dest(tmp_path / "ws", "/etc/cron.d")


def test_a_traversing_downstream_repo_is_refused(ps, tmp_path):
    with pytest.raises(ValueError, match="downstream_repo"):
        ps.downstream_dest(tmp_path / "ws", "../../elsewhere")


def test_a_nested_downstream_repo_is_refused(ps, tmp_path):
    """A sibling DIRECTORY NAME, not a path: anything with a separator is out."""
    with pytest.raises(ValueError, match="downstream_repo"):
        ps.downstream_dest(tmp_path / "ws", "sub/dir")


@pytest.mark.parametrize("value", ["", ".", ".."])
def test_a_degenerate_downstream_repo_is_refused(ps, tmp_path, value):
    with pytest.raises(ValueError, match="downstream_repo"):
        ps.downstream_dest(tmp_path / "ws", value)


def test_a_plain_sibling_name_is_accepted(ps, tmp_path):
    """Anchor: the ordinary configuration must keep working."""
    ws = tmp_path / "ws"
    assert ps.downstream_dest(ws, "service-host-mirror") == \
        (tmp_path / "service-host-mirror").resolve()


def test_the_destination_is_resolved_not_merely_joined(ps, tmp_path):
    """`_contained` is the second wall AND it resolves. A linked parent must
    yield the real directory, or the containment reasoning is about a path that
    is not the one being written to."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    assert ps.downstream_dest(link / "ws", "mirror") == real / "mirror"


def test_main_refuses_a_path_valued_downstream_repo(ps, tmp_path, monkeypatch,
                                                    capsys):
    """The wall has to be ON the call path, not merely available beside it."""
    monkeypatch.setattr(ps, "get_workspace_root", lambda: tmp_path / "ws")
    monkeypatch.setattr(ps, "load_manifest", lambda _w: (["scripts"], [], "/etc/cron.d"))
    monkeypatch.setattr(sys, "argv", ["publish-service.py"])
    assert ps.main() == 1
    assert "must be a plain" in capsys.readouterr().out


def test_main_still_accepts_a_plain_sibling(ps, tmp_path, monkeypatch, capsys):
    """Anchor: the guard must not refuse the ordinary configuration. It stops at
    the missing-clone check, which is one step past the wall."""
    monkeypatch.setattr(ps, "get_workspace_root", lambda: tmp_path / "ws")
    monkeypatch.setattr(ps, "load_manifest", lambda _w: (["scripts"], [], "mirror"))
    monkeypatch.setattr(sys, "argv", ["publish-service.py"])
    assert ps.main() == 1
    out = capsys.readouterr().out
    assert "must be a plain" not in out
    assert "clone not found" in out


def test_the_include_check_is_untouched(ps, tmp_path):
    """Anchor: `_contained` was already right; this shard adds a sibling wall."""
    with pytest.raises(ValueError, match="escapes"):
        ps._contained(tmp_path, "../outside")


def test_an_escaping_include_is_refused_through_copy_includes(ps, tmp_path):
    """The wall has to be on the copy path too: `copy_includes` rmtree's the
    destination before writing it, so an escape is a delete primitive."""
    workspace = tmp_path / "ws"
    (workspace / "scripts").mkdir(parents=True)
    dest = tmp_path / "mirror"
    dest.mkdir()
    with pytest.raises(ValueError, match="escapes"):
        ps.copy_includes(workspace, dest, ["../../outside"], [])


def test_an_ordinary_include_still_copies(ps, tmp_path):
    """Anchor: the wall must not refuse a normal manifest entry."""
    workspace = tmp_path / "ws"
    (workspace / "scripts").mkdir(parents=True)
    (workspace / "scripts" / "a.py").write_text("x", encoding="utf-8")
    dest = tmp_path / "mirror"
    dest.mkdir()
    ps.copy_includes(workspace, dest, ["scripts"], [])
    assert (dest / "scripts" / "a.py").read_text(encoding="utf-8") == "x"


# ===========================================================================
# pull-service-state: a config defect that outran its own handler
# ===========================================================================

@pytest.fixture
def pss():
    return _load("pss_11p3", "scripts/pull-service-state.py")


def test_a_config_without_state_dirs_is_named_not_a_traceback(pss, monkeypatch):
    """KeyError is not a ValueError, so `main`'s handler could not catch it."""
    monkeypatch.setattr(pss, "service_config",
                        lambda: ({"vm_engine_root": "/srv"}, None))
    with pytest.raises(ValueError, match="state_dirs"):
        pss.state_dirs()


def test_the_missing_key_reaches_the_operator_as_one_line(pss, monkeypatch, capsys):
    """End to end through `main`'s handler, which is what the fix is for."""
    monkeypatch.setattr(pss, "service_config", lambda: ({}, None))
    monkeypatch.setattr(pss, "load_env", lambda: None)
    monkeypatch.setattr(pss, "get_data_root", lambda: Path("/nonexistent"))
    assert pss.main() == 1
    assert "service-host.json" in capsys.readouterr().out


def test_a_non_list_state_dirs_is_named(pss, monkeypatch):
    monkeypatch.setattr(pss, "service_config",
                        lambda: ({"state_dirs": {"a": "b"}}, None))
    with pytest.raises(ValueError, match="must be a list"):
        pss.state_dirs()


def test_unparseable_json_is_carried_not_raised_at_import(pss, tmp_path,
                                                          monkeypatch):
    """The load ran at IMPORT, where no handler exists at all.

    It is a call-time load since 2026-08-31 (`service_config`), which removes
    the no-handler-in-scope window but not the reason for carrying the error as
    a value: `main` catches ValueError only, and `state_dirs` is where it is
    raised.
    """
    bad = tmp_path / "service-host.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(pss, "resolve_config_with_example", lambda *a, **k: bad)
    cfg, err = pss.service_config()
    assert cfg == {}
    assert "not valid JSON" in err


def test_a_json_list_config_is_carried_not_raised(pss, tmp_path, monkeypatch):
    """A top-level list raised AttributeError from the module-level `.get`."""
    bad = tmp_path / "service-host.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    monkeypatch.setattr(pss, "resolve_config_with_example", lambda *a, **k: bad)
    cfg, err = pss.service_config()
    assert cfg == {}
    assert "JSON object" in err


def test_an_unreadable_config_is_carried_not_raised(pss, tmp_path, monkeypatch):
    missing = tmp_path / "does-not-exist.json"
    monkeypatch.setattr(pss, "resolve_config_with_example", lambda *a, **k: missing)
    cfg, err = pss.service_config()
    assert cfg == {}
    assert "could not be read" in err


def test_a_carried_error_surfaces_at_the_first_use(pss, monkeypatch):
    """The error must not sit silently until something else fails oddly."""
    monkeypatch.setattr(pss, "service_config",
                        lambda: ({}, "is not valid JSON (x): boom"))
    with pytest.raises(ValueError, match="not valid JSON"):
        pss.state_dirs()


def test_the_config_object_is_still_a_plain_dict(pss):
    """Anchor: tests/test_a_publish_path_with_no_wall.py reads
    `service_config()[0].get(...)`, so the first element of the returned pair
    must stay a plain dict and the second must stay the error slot.

    The name it anchors changed on 2026-08-31 -- `_SVC` / `_SVC_ERROR` were a
    module-level pair that froze the data root at import, and became this one
    call-time function -- but the cross-file assumption is the same one, and
    the sibling file still reads the config through this seam.
    """
    cfg, error = pss.service_config()
    assert isinstance(cfg, dict)
    assert error is None or isinstance(error, str)


def test_a_good_config_still_resolves(pss, monkeypatch):
    """Anchor: the guards must not refuse the working configuration."""
    monkeypatch.setattr(pss, "service_config",
                        lambda: ({"state_dirs": [["m", "data", "state/x"]],
                                  "vm_data_root": "/srv/data"}, None))
    monkeypatch.delenv("SERVICE_VM_DATA_ROOT", raising=False)
    assert pss.state_dirs() == [("m", "/srv/data/state/x")]
