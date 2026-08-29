"""The offboard that said "no contacts" about a repo it never opened.

`_find_exec_contacts` in scripts/offboard-exec.py returns
`(directory_or_None, looked_everywhere)`, and its docstring is explicit about
why the second value exists: "there are no contacts" and "I could not check"
are different answers, and only the first may be reported as a success. The
flag was implemented as a `reachable` latch set True on the first candidate
that cloned, and never lowered when a LATER candidate failed. That is "at least
one repo answered", which is not the question.

Measured 2026-08-29 with a stubbed `run_cmd`: candidate 1 cloned clean but held
no contacts subdirectory, candidate 2 failed to clone, and the function
returned `(None, True)`. `preserve_crm_contacts` took its success branch and
returned True, `offboard_verdict` said complete with no reasons, and the
durable audit log recorded "contacts preserved" about a location nothing ever
opened. `archive_workspace_repo` runs next and makes that unchecked repo
read-only, so the data loss happens before the archive step, not after it.

A second, smaller defect sat in `reassign_contacts`: the `owner:` rewrite lived
inside `if match:`, so a contact file with no frontmatter block was copied into
the CEO's CRM with a transfer note and no owner at all.

Nothing here touches a real repository. `run_cmd` is replaced in every test, so
no `gh` and no `git` process runs, and every path is under tmp_path.

Tests: this file.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "scripts" / "offboard-exec.py"

SLUG = "james-bond"


@pytest.fixture()
def ob():
    spec = importlib.util.spec_from_file_location("offboard_exec_contacts", str(SOURCE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wire(ob, tmp_path, monkeypatch):
    """Point the module at a tmp workspace and return its two candidate repo
    names, newest model first."""
    engine = tmp_path / "engine"
    engine.mkdir()
    monkeypatch.setattr(ob, "get_workspace_root", lambda: engine)
    monkeypatch.setattr(ob, "get_outputs_dir", lambda: tmp_path / "data" / "outputs")
    monkeypatch.setattr(ob, "get_crm_contacts_dir", lambda: tmp_path / "data" / "crm" / "contacts")
    return [name for name, _ in ob._contacts_candidates(SLUG)]


def _cloner(ob, monkeypatch, *, clones_ok, contacts_in=None):
    """Stub `run_cmd`. `clones_ok` is the set of repo names whose clone
    succeeds; `contacts_in` maps a repo name to the subpath to populate."""
    contacts_in = contacts_in or {}
    attempted: list[str] = []

    def run_cmd(cmd, cwd=None, check=True):
        if cmd[:3] == ["gh", "repo", "clone"]:
            repo = cmd[3].split("/")[-1]
            attempted.append(repo)
            if repo not in clones_ok:
                raise subprocess.CalledProcessError(1, cmd, stderr="network unreachable")
            local = Path(cmd[4])
            local.mkdir(parents=True, exist_ok=True)
            if repo in contacts_in:
                target = local / contacts_in[repo]
                target.mkdir(parents=True, exist_ok=True)
                (target / "moneypenny.md").write_text(
                    "---\nname: Moneypenny\nowner: james-bond\n---\n\nnotes\n",
                    encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ob, "run_cmd", run_cmd)
    return attempted


def test_an_unreached_second_candidate_is_not_looked_everywhere(ob, tmp_path, monkeypatch):
    """The measured defect: first candidate reachable but empty, second
    unreachable."""
    first, second = _wire(ob, tmp_path, monkeypatch)
    attempted = _cloner(ob, monkeypatch, clones_ok={first})

    src, looked_everywhere = ob._find_exec_contacts(SLUG)
    assert src is None
    assert looked_everywhere is False, (
        f"a candidate that was never reached was reported as checked; "
        f"clone attempts were {attempted!r}")
    assert attempted == [first, second]


def test_that_unreached_candidate_stops_the_offboard_claiming_success(ob, tmp_path, monkeypatch):
    """The consequence the finding is about: the verdict, and so the durable
    audit line, must not say the contacts were preserved."""
    first, _second = _wire(ob, tmp_path, monkeypatch)
    _cloner(ob, monkeypatch, clones_ok={first})

    preserved = ob.preserve_crm_contacts(SLUG)
    assert preserved is False

    complete, reasons = ob.offboard_verdict(True, preserved, [], [], True)
    assert complete is False
    assert any("contacts were not preserved" in r for r in reasons)


def test_every_candidate_reached_and_genuinely_empty_is_a_success(ob, tmp_path, monkeypatch):
    """The other direction. "There are no contacts" is a legitimate answer and
    must still be reportable as one, or every offboard of a contactless exec
    goes permanently red."""
    first, second = _wire(ob, tmp_path, monkeypatch)
    _cloner(ob, monkeypatch, clones_ok={first, second})

    src, looked_everywhere = ob._find_exec_contacts(SLUG)
    assert src is None
    assert looked_everywhere is True
    assert ob.preserve_crm_contacts(SLUG) is True


def test_no_candidate_reached_at_all_is_not_looked_everywhere(ob, tmp_path, monkeypatch):
    first, _second = _wire(ob, tmp_path, monkeypatch)
    _cloner(ob, monkeypatch, clones_ok=set())

    assert ob._find_exec_contacts(SLUG) == (None, False)
    assert first  # the candidate list is non-empty, so the sweep above proves something


def test_contacts_found_in_the_first_candidate_short_circuit(ob, tmp_path, monkeypatch):
    """Finding what it came for still ends the search; the fix must not force a
    pointless clone of the retired repo."""
    first, second = _wire(ob, tmp_path, monkeypatch)
    attempted = _cloner(ob, monkeypatch, clones_ok={first},
                        contacts_in={first: "crm/contacts"})

    src, looked_everywhere = ob._find_exec_contacts(SLUG)
    assert src is not None and src.is_dir()
    assert looked_everywhere is True
    assert attempted == [first], f"the second candidate should not be cloned; got {attempted!r}"
    assert second not in attempted


def test_preserved_contacts_are_actually_copied(ob, tmp_path, monkeypatch):
    """A guard on the success path, so "no contacts anywhere" cannot become the
    only tested outcome."""
    first, _second = _wire(ob, tmp_path, monkeypatch)
    _cloner(ob, monkeypatch, clones_ok={first}, contacts_in={first: "crm/contacts"})

    assert ob.preserve_crm_contacts(SLUG) is True
    dst = tmp_path / "data" / "outputs" / "operations" / "offboarding" / f"{SLUG}-crm-final"
    copied = sorted(p.name for p in dst.glob("*.md"))
    assert copied == ["moneypenny.md"]


def test_a_contact_without_frontmatter_is_still_given_an_owner(ob, tmp_path, monkeypatch):
    """Reassigning is supposed to set the owner. It only did so for files that
    already carried a frontmatter block."""
    first, _second = _wire(ob, tmp_path, monkeypatch)

    def run_cmd(cmd, cwd=None, check=True):
        if cmd[:3] == ["gh", "repo", "clone"]:
            repo = cmd[3].split("/")[-1]
            if repo != first:
                raise subprocess.CalledProcessError(1, cmd, stderr="not found")
            contacts = Path(cmd[4]) / "crm" / "contacts"
            contacts.mkdir(parents=True, exist_ok=True)
            (contacts / "bare.md").write_text("# Q Branch\n\nno frontmatter here\n",
                                              encoding="utf-8")
            (contacts / "owned.md").write_text(
                "---\nname: Moneypenny\nowner: james-bond\n---\n\nnotes\n",
                encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ob, "run_cmd", run_cmd)

    ob.reassign_contacts(SLUG, "vesper-lynd")

    dst = tmp_path / "data" / "crm" / "contacts"
    bare = (dst / "bare.md").read_text(encoding="utf-8")
    owned = (dst / "owned.md").read_text(encoding="utf-8")
    assert bare.startswith("---\nowner: vesper-lynd\n---\n")
    assert "# Q Branch" in bare and "Transfer note" in bare
    # The existing branch must keep working: one owner line, rewritten in place.
    # Scoped to the frontmatter block, because the appended transfer note names
    # the previous owner on purpose.
    owned_fm = owned.split("---")[1]
    assert "owner: vesper-lynd" in owned_fm
    assert "owner: james-bond" not in owned_fm
    assert owned_fm.count("owner:") == 1


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
