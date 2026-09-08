"""Eight commit sites took a directory on trust and none asked where it was.

The silhouette, repeated across `scripts/`: a caller-supplied directory, a
`git add` and a `git commit` inside it, and nothing anywhere asking whether that
directory is in the engine work tree. `scripts/create-data-repo.py` is the one
that fired on 2026-09-08 (commit `ed7cee1` on `main`, six files, two unrelated
sessions' work); it is repaired in
`tests/test_a_probe_that_asked_whether_it_was_inside_any_repository.py`. This
file covers its siblings.

TWO OF THEM ARE WORSE THAN THE ONE THAT FIRED. `scripts/build_engine_repo.py`
and `scripts/build_data_repo.py` commit with `--no-verify`, which disables every
commit gate: the secret scanner, the leak guards, the real-entity scan. Handed an
engine-internal `--target`, either would have committed the live engine PAST ALL
OF THEM, and `build_data_repo` would have copied the PRIVATE and CORPORATE files
in first.

NO ELEVENTH COPY OF THE CHECK. `require_outside_engine_clone` already existed at
`scripts/utils/paths.py:290` with five callers, written for precisely this
question ("is this path inside the engine clone?") and carrying the reasoning for
why it asks about the PATH and not about the environment. Every site below calls
it. Writing a new helper beside it would have been the tenth copy the repair
exists to prevent.

WHAT IS NOT GUARDED, and why, is asserted at the bottom of this file rather than
left to silence: `scripts/push-all.py` commits the live engine ON PURPOSE, and
two sites resolve to a NESTED repository inside an exec workspace by design.

The refusals below are driven through each script's own predicate and, where the
entry point is reachable without network or `gh`, through `main()` itself.
"""
import importlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.utils.paths import DataRootError

ENGINE_ROOT = Path(__file__).resolve().parent.parent

# Every site repaired here, as (module path, the SOURCE of the argument the
# guard is passed). The second element is what makes this a per-SITE list rather
# than a per-file one: `scripts/provision-exec.py` carries two independent
# commit paths, and a wiring test that only asked "does this file mention the
# helper" was satisfied by either one. Found by mutation -- removing either of
# that file's two calls SURVIVED the first version of this file.
GUARDED_SITES = [
    ("scripts/build_engine_repo.py", "target"),
    ("scripts/build_data_repo.py", "target"),
    ("scripts/dev/publish-marketplace.py", "repo_dir"),
    ("scripts/provision-exec.py", "workspace_dir"),
    ("scripts/provision-exec.py", "Path(cwd)"),
    ("scripts/offboard-exec.py", "Path(cwd)"),
    ("scripts/emergency-revoke.py", "Path(cwd)"),
    ("scripts/utils/crm.py", "repo"),
    ("scripts/publish-service.py", "dest"),
]

# MEASURED 2026-09-08 from a grep of `scripts/` for `"add", "-A"` and
# `git commit`, plus the second provision-exec path. A floor, so a site dropping
# out of the list is a failure rather than a quietly shorter run.
SITE_FLOOR = 9


def _load(relative: str):
    """Load a script by path; several have hyphens and are not module names."""
    name = Path(relative).stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, ENGINE_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine_scratch(request):
    """A factory for a path under `.tmp/` that is removed before AND after.

    Before, not only after, and that is the lesson rather than the tidiness. A
    mutation run over these tests removes the guard on purpose, so the run under
    a surviving mutant CREATES the path these tests assert is absent; the next
    run then fails on the litter instead of on the code, and every verdict after
    it is about the wrong thing. `auto-memory/a-mutation-that-littered-faked-
    every-later-verdict.md` is the same failure in another harness.
    """
    import shutil

    made = []

    def make(name: str) -> Path:
        path = ENGINE_ROOT / ".tmp" / name
        shutil.rmtree(path, ignore_errors=True)
        made.append(path)
        return path

    yield make
    for path in made:
        shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Every site calls the shared helper, and it is the shared one
# ---------------------------------------------------------------------------


def test_every_repaired_site_calls_the_one_shared_helper():
    """Asked of the AST: the call must be there, by that name, in that file.

    The point is not that a check exists but that it is THE check. A site that
    grew its own `if root in target.parents` would pass a behavioural test and
    still be the copy that stops being fixed.
    """
    import ast

    assert len(GUARDED_SITES) >= SITE_FLOOR, GUARDED_SITES
    guarded = {}
    for relative, _ in GUARDED_SITES:
        if relative in guarded:
            continue
        tree = ast.parse((ENGINE_ROOT / relative).read_text(encoding="utf-8"))
        guarded[relative] = {
            ast.unparse(node.args[0])
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "require_outside_engine_clone" and node.args
        }
    missing = [(relative, argument) for relative, argument in GUARDED_SITES
               if argument not in guarded[relative]]
    assert not missing, missing


def test_the_helper_it_shares_is_the_one_that_already_existed():
    """No eleventh copy: the name resolves to `scripts/utils/paths.py`."""
    from scripts.utils import paths, workspace

    assert workspace.require_outside_engine_clone is paths.require_outside_engine_clone
    assert paths.require_outside_engine_clone.__module__ == "scripts.utils.paths"


# ---------------------------------------------------------------------------
# The refusal, per site
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("relative", ["scripts/build_engine_repo.py",
                                      "scripts/build_data_repo.py"])
def test_a_build_refuses_an_engine_internal_target(relative, monkeypatch,
                                                   capsys, unguard_main_clone,
                                                   engine_scratch):
    """The two `--no-verify` builds, through `main()`.

    `unguard_main_clone` because `require_main_clone(__file__)` is the first
    statement of each `main()` and exits from this worktree, which would make
    the test pass without the containment check existing at all. That guard is
    owned elsewhere; this file owns what happens after it.
    """
    module = _load(relative)
    unguard_main_clone(module)
    victim = engine_scratch(f"build-should-refuse-{Path(relative).stem}")
    monkeypatch.setattr(sys, "argv", [relative, "--target", str(victim)])

    assert module.main() == 1
    assert "REFUSING" in capsys.readouterr().out
    assert not victim.exists(), "it refused after creating the target"


def test_a_build_still_accepts_a_sibling_target(monkeypatch, capsys, tmp_path,
                                                unguard_main_clone):
    """The other direction, and it must get PAST the guard, not merely differ.

    `--dry-run`, so the assertion is that the run reaches the dry-run report:
    a full build copies the whole tracked tree and is not what is under test
    here. Reaching that line proves the containment check let a sibling through.
    """
    module = _load("scripts/build_data_repo.py")
    unguard_main_clone(module)
    monkeypatch.setattr(
        sys, "argv",
        ["build_data_repo.py", "--target", str(tmp_path / ".heading-os-data"),
         "--dry-run"])

    assert module.main() == 0
    out = capsys.readouterr().out
    assert "dry-run: nothing copied" in out, out
    assert "REFUSING" not in out, out


def test_the_marketplace_publish_refuses_the_engine_as_its_repo_dir(
        monkeypatch, capsys):
    """`--repo-dir .` resolves to the engine and passes the `.git` test.

    The refusal has to land before `sync_into_repo`, which `rmtree`s
    `.claude-plugin/` and `plugins/` in the target. A guard placed at the commit
    would refuse after the engine's own plugin tree had been deleted.
    """
    module = _load("scripts/dev/publish-marketplace.py")
    calls = []
    monkeypatch.setattr(module, "sync_into_repo",
                        lambda *a, **k: calls.append(a))

    assert module.main(["--repo-dir", "."]) == 2
    assert "REFUSING TO PUBLISH" in capsys.readouterr().err
    assert calls == [], "sync_into_repo ran before the refusal"


def test_provisioning_refuses_the_engine_as_an_exec_workspace(monkeypatch,
                                                              capsys):
    """`--workspace-dir .` would `git init`, remote, commit and PUSH the engine.

    Driven at the predicate rather than through `main()`: this script's `main()`
    exits 2 unconditionally as a deprecated entry point long before the check,
    so a `main()` test here would assert nothing about containment.
    """
    module = _load("scripts/provision-exec.py")
    with pytest.raises(DataRootError):
        module.require_outside_engine_clone(ENGINE_ROOT, "--workspace-dir")
    with pytest.raises(DataRootError):
        module.require_outside_engine_clone(
            ENGINE_ROOT / "scripts", "--workspace-dir")


@pytest.mark.parametrize("relative,function,expected", [
    ("scripts/offboard-exec.py", "update_exec_registry", None),
    ("scripts/emergency-revoke.py", "update_registry_status", None),
])
def test_a_registry_update_refuses_when_the_data_root_is_the_engine(
        relative, function, expected, monkeypatch, capsys, engine_scratch):
    """The `get_data_root()` fallback is the live exposure, not a typo.

    With no overlay configured, `get_data_root()` falls to `<engine>/examples`
    and, on a tree that carries the marker directories, to `<engine>` itself. A
    registry write then commits and PUSHES the engine, and in
    `emergency-revoke.py` it does so during an incident, unconditionally.

    The registry file is redirected into the engine so the WRITE succeeds and
    only the commit is refused, which is the state the messages promise.
    """
    module = _load(relative)
    registry = engine_scratch(f"registry-probe-{Path(relative).stem}") \
        / "config" / "exec-registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        '{"executives": [{"slug": "probe", "name": "Probe", "status": "active"}]}',
        encoding="utf-8")
    monkeypatch.setattr(module, "get_data_config_dir", lambda: registry.parent)

    ran = []
    monkeypatch.setattr(module, "run_cmd",
                        lambda *a, **k: ran.append(a) or pytest.fail(
                            "git ran against the engine"))

    if relative.endswith("offboard-exec.py"):
        assert module.update_exec_registry("probe") is expected
    else:
        assert module.update_registry_status("probe") is expected
    assert ran == []
    output = capsys.readouterr().out
    assert "inside the engine clone" in output, output


def test_the_contact_tools_refuse_at_their_shared_root(capsys):
    """One insertion covers four call sites in two scripts.

    `try_commit` is the only path either contact tool has to git, so the check
    lives there. The boolean contract is preserved: the callers read False as
    "stop and say INCOMPLETE", which is the right outcome for a refusal.
    """
    from scripts.utils.crm import try_commit

    called = []

    assert try_commit(lambda *a: called.append(a), ENGINE_ROOT / "crm",
                      [], "msg", "source") is False
    assert called == [], "the commit function ran against the engine"
    output = capsys.readouterr().out
    assert "REFUSED" in output, output
    assert "Nothing was staged or committed" in output, output


def test_the_contact_tools_refuse_when_the_data_root_IS_the_engine_root(capsys):
    """The guard asks about `repo`, not about a path derived from it.

    Found by mutation: `require_outside_engine_clone(repo.parent / "elsewhere",
    ...)` SURVIVED the first version of this file. Every case here passed a repo
    one level DOWN in the engine, so the mutant's derived path was still inside
    and still refused. The case that separates them is the one the data-root
    rule-2 fallback actually produces: `get_data_root()` returning the workspace
    root ITSELF, where `parent / "elsewhere"` is a sibling and passes.
    """
    from scripts.utils.crm import try_commit

    called = []
    assert try_commit(lambda *a: called.append(a), ENGINE_ROOT,
                      [], "msg", "target") is False
    assert called == [], "the commit function ran against the engine root"
    assert "REFUSED" in capsys.readouterr().out


def test_provisioning_refuses_an_engine_internal_workspace_dir_through_main(
        monkeypatch, capsys, unguard_main_clone):
    """`--workspace-dir <engine>`, driven through `main()` to the exit code.

    The preconditions ahead of the check are neutralised, not the check: the
    legacy-provisioner refusal (an env override the script itself documents),
    the GitHub-org resolution and the admin gate each have their own tests and
    each would otherwise end the run before `workspace_dir` is even computed.
    `load_provision_state` is the first thing that touches disk after the guard,
    so its not having been called is the assertion that nothing was created.
    """
    module = _load("scripts/provision-exec.py")
    unguard_main_clone(module)
    monkeypatch.setenv("HEADING_OS_ALLOW_LEGACY_PROVISION", "1")
    monkeypatch.setattr(module, "github_org", lambda: "example-org")
    monkeypatch.setattr(module, "validate_admin", lambda *a, **k: None)
    monkeypatch.setattr(module, "load_provision_state", lambda *a, **k: pytest.fail(
        "the run continued past the containment refusal"))
    monkeypatch.setattr(sys, "argv", [
        "provision-exec.py", "--name", "James Bond", "--title", "CSO",
        "--email", "james.bond@example.com", "--role", "cso",
        "--workspace-dir", str(ENGINE_ROOT)])

    with pytest.raises(SystemExit) as caught:
        module.main()
    assert caught.value.code == 2
    assert "inside the engine clone" in capsys.readouterr().err


def test_the_exec_registry_step_refuses_when_the_data_root_is_the_engine(
        monkeypatch, capsys, engine_scratch):
    """provision-exec's SECOND commit path, which the first wiring test missed.

    Same `get_data_root()` fallback as the offboard and emergency registry
    writes, and it ends in `git push` too.
    """
    module = _load("scripts/provision-exec.py")
    registry_dir = engine_scratch("provision-registry-probe") / "config"
    registry_dir.mkdir(parents=True, exist_ok=True)
    (registry_dir / "exec-registry.json").write_text(
        '{"version": "1.0", "executives": []}', encoding="utf-8")
    monkeypatch.setattr(module, "get_data_config_dir", lambda: registry_dir)
    monkeypatch.setattr(module, "run_cmd", lambda *a, **k: pytest.fail(
        "git ran against the engine"))
    monkeypatch.setattr(module, "mark_step_done", lambda *a, **k: pytest.fail(
        "the step was marked done over a refusal"))

    args = type("A", (), {"name": "James Bond", "title": "CSO",
                          "email": "james.bond@example.com", "role": "cso",
                          "platform": "linux", "github_user": None})()
    assert module.register_in_exec_registry({}, args, ENGINE_ROOT / ".tmp",
                                            "james-bond") is False
    assert "inside the engine clone" in capsys.readouterr().out


def test_the_contact_tools_still_commit_into_a_real_overlay(tmp_path, capsys):
    """The other direction: a sibling overlay reaches `commit_fn` unchanged."""
    from scripts.utils.crm import try_commit

    overlay = tmp_path / ".heading-os-data" / "crm"
    overlay.mkdir(parents=True)
    called = []

    assert try_commit(lambda *a: called.append(a), overlay,
                      [], "msg", "target") is True
    assert len(called) == 1, called
    assert "Committed to the target repo." in capsys.readouterr().out


def test_the_service_publish_refuses_a_manifest_pointing_at_the_engine(
        monkeypatch, capsys, unguard_main_clone):
    """`downstream_repo` is a plain NAME, and the engine's directory has one.

    `downstream_dest` refuses a path and permits any sibling name, so a
    manifest naming the engine's own directory resolves to this clone -- which
    `copy_includes` rmtree's and `publish` commits with `git add -A`.
    """
    module = _load("scripts/publish-service.py")
    unguard_main_clone(module)
    monkeypatch.setattr(
        module, "load_manifest",
        lambda workspace: ([], [], ENGINE_ROOT.name))
    monkeypatch.setattr(module, "copy_includes", lambda *a, **k: pytest.fail(
        "copy_includes ran against the engine"))
    monkeypatch.setattr(sys, "argv", ["publish-service.py"])

    assert module.main() == 1
    assert "inside the engine clone" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# What is deliberately NOT guarded
# ---------------------------------------------------------------------------


def test_push_all_is_deliberately_unguarded():
    """`/backup` commits the live engine on purpose, and it is HELM-gated.

    Asserted rather than left to silence, so a later sweep that "finishes the
    job" by adding the guard here has to delete this test and read why first.
    """
    import ast

    tree = ast.parse((ENGINE_ROOT / "scripts" / "push-all.py").read_text(
        encoding="utf-8"))
    calls = {node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "require_outside_engine_clone" not in calls


@pytest.mark.parametrize("resolver,relative", [
    ("get_crm_central_path", ".crm-central-repo"),
    ("get_corporate_repo_path", ".corporate-repo"),
])
def test_two_resolvers_deliberately_answer_inside_the_workspace(resolver,
                                                                relative):
    """`emergency-revoke.py:363` and `promote-knowledge.py` are left alone.

    Both commit into a repository these resolvers name, and on a NON-CEO
    workspace both resolvers deliberately return `<workspace_root>/<name>` --
    an independent nested git repository inside the exec's own clone, not the
    HEADING OS engine. `require_outside_engine_clone` cannot tell a nested
    repository from the work tree containing it, so guarding these two would
    refuse the exec branch outright and break the offboarding and knowledge
    paths on every exec workspace.

    Two things make that acceptable, and both are properties rather than hopes:
    each site stages an EXPLICIT PATHSPEC (`git add audit/security-events.jsonl`,
    `git add <one note>`), so neither can sweep up a bystander the way
    `git add -A` did on 2026-09-08; and each is gated on the target tree
    already existing, so neither creates one.

    This test pins the resolver behaviour that the decision rests on. If a
    resolver stops answering inside the workspace, the reason for the exemption
    is gone and this test says so.
    """
    from scripts.utils import workspace as ws

    source = (ENGINE_ROOT / "scripts" / "utils" / "workspace.py").read_text(
        encoding="utf-8")
    assert hasattr(ws, resolver), resolver
    assert f'root / "{relative}"' in source, (
        f"{resolver} no longer answers inside the workspace; the exemption "
        f"recorded in this test's docstring rests on that branch existing")
