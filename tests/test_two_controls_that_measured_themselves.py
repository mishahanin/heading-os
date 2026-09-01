"""Two send-side controls whose only witness was the thing they were checking.

Both were surfaced by a whole-suite mutation run and both were confirmed by
running the code on 2026-08-29, before anything was changed.

**1. The send boundary compared a list to itself.**

`scripts/heading_cli.py` builds the argv for a headless `claude -p` run. Its
`SEND_DENY` constant is the defense-in-depth half of the lethal-trifecta
control in `.claude/rules/lethal-trifecta.md`: whatever the tier grants, the
outbound transports are named under `--disallowedTools`. Three tests asserted
that boundary, and all three spelled it

    for entry in SEND_DENY:
        assert entry in disallowed

while `build_skill_command` builds `disallowed` as `list(SEND_DENY)`. The
expectation and the subject were the same object, so each loop held for every
possible value of the constant. MEASURED 2026-08-29 by editing the constant and
re-running `tests/test_heading_skill.py`:

    SEND_DENY as shipped (4 entries)  -> 13 passed
    SEND_DENY truncated to 1 entry    ->  1 failed, 12 passed
    SEND_DENY = []                    ->  2 failed, 11 passed
    the three loop-bearing tests, under SEND_DENY = []  ->  3 passed

The three loops never failed in any of those runs, which is the tautology. The
two failures came from somewhere else: `test_draft_tier_send_boundary` asserts
the substrings `"approve"` and `"send-email.py"` literally, and
`test_allowlist_and_tiers` asserted the list was truthy. So the file could feel
a DELETION of the two things it happened to spell, and was blind to every
omission, which is the failure that actually shipped.

Sweeping the 201 files in `scripts/*.py` for a script that calls an outbound
mail API itself returns exactly two, and only one of them was denied:

    outbound transports on disk : send-email.py, gmail-send.py
    named by SEND_DENY          : send-email.py, action-queue.py approve
    MISSING                     : gmail-send.py     (13 passed over it)

`gmail-send.py` sends a Gmail draft from the operator's personal mailbox, and
`gmail-draft.py` beside it composes one from a caller-supplied `--to` and body.
Two Bash calls is a whole exfiltration path. `SEND_DENY` dates from 2026-07-09
and `gmail-send.py` landed 2026-08-08, so the hole was open for 21 days.

Sweeping one process hop further, for a module that names a transport script in
a bare `*.py` literal and can spawn it, adds `action-queue-execute.py` and
`fireside-bot.py`; both run `send-email.py` through `subprocess`, and neither
was denied either. All four are denied now. The closure is stopped at one hop
deliberately: run to a fixed point it reaches `prime-health-parallel.py`, a
read-only health check five hops out, and a rule that names everything names
nothing.

The fix is that the expectation is recomputed here from the scripts' own
source and held against the constant as an EQUALITY, so a new transport that
skips `SEND_DENY` fails, and a `SEND_DENY` entry naming a script that cannot
send fails too.

**2. The content gate's clean line printed the number that refuted it.**

`scripts/content-guard.py` scans engine-routed files against a denylist built
from the private DATA overlay. It opened with `if dl.degraded or not
dl.tokens:` and returned 0 with "skipped". Nothing exercised that branch.
MEASURED 2026-08-29, with the `or` rewritten to `and` and an empty overlay
passed as `--data-root`:

    before (or)  -> "content-guard: denylist unavailable
                     (the overlay holds no entities to guard); skipped."   exit 0
    after  (and) -> "content-guard: clean (1 file(s); 0 denylist tokens)"  exit 0

    tests/test_content_guard.py
    tests/test_a_gate_that_shipped_what_it_never_read.py
    tests/test_detectors_that_reported_clean_over_what_they_could_not_see.py
    tests/test_a_wall_that_stayed_green_over_a_corrupt_source.py
      -> 92 passed against the mutant

A gate that says "clean" because it loaded nothing is the failure this repo
cares about most, and the clean line was printing `0 denylist tokens` in the
same breath. It was untestable where it stood: the four states the branch
separates are only reachable through a real overlay, so the suite could only
ever drive one of them. The decision is now `denylist_verdict`, three scalars
in and a verdict out, and the four states are arguments with a case each. The
clean line carries a second, independent refusal for a zero-token denylist,
which the early return makes unreachable and which is proved here by handing
`main` a `denylist_verdict` that lies rather than by removing anything.

Run: python3 -m pytest tests/test_two_controls_that_measured_themselves.py
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from scripts.heading_cli import SEND_DENY, TIER_ALLOWED, build_skill_command
from scripts.utils.content_denylist import Denylist
from tests.repo_files import ROOT, tracked_paths


def _load(name: str, rel: str):
    """Import a kebab-case script under scripts/ as a module."""
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# The rule: which scripts can put a message on an outbound transport
# ============================================================

# A transport is a library plus one of ITS send verbs. The library alone is not
# enough: ten scripts under scripts/ import exchangelib and nine of them only
# read mail. Measured 2026-08-29, the pair below separates send-email.py from
# crm-backfill-exchange.py, email-intelligence.py, exchange-task.py,
# gal-export.py, scrutinize-dispatch.py, sentinel.py, setup.py,
# sync-exchange.py and workspace-health.py with no false positive.
TRANSPORTS = {
    "exchange": (("exchangelib",), (".send()", ".send_and_save(")),
    "smtp": (("smtplib",), (".sendmail(", ".send_message(")),
    "gmail": (("gmail_auth", "googleapiclient"), ("drafts().send(", "messages().send(")),
}

# A module that holds one of these can start a child process or load a sibling
# by path, which is how action-queue.py, action-queue-execute.py and
# fireside-bot.py reach send-email.py.
_SPAWN_IMPORTS = ("subprocess", "importlib")


def is_direct_transport(source: str) -> bool:
    """True when `source` itself calls an outbound mail API.

    Pure: text in, bool out. Both markers of one transport must be present.
    """
    for libs, verbs in TRANSPORTS.values():
        if any(lib in source for lib in libs) and any(verb in source for verb in verbs):
            return True
    return False


def spawns_any(source: str, names: set[str]) -> bool:
    """True when `source` can start one of `names` as a child or a loaded module.

    Pure: text plus a name set in, bool out. The name has to appear as a bare
    path literal ending in `.py`; a name embedded in a longer command string
    ("python3 scripts/send-email.py --to ...", which `crm_next.py` PRINTS as
    guidance) is prose about the transport, not a call to it, and does not
    count. The module must also be able to spawn at all.
    """
    if not any(f"import {mod}" in source for mod in _SPAWN_IMPORTS):
        return False
    literals = re.findall(r"""["']([^"'\n]*\.py)["']""", source)
    return any(Path(literal).name in names for literal in literals)


def send_reachable_scripts(sources: dict[str, str]) -> set[str]:
    """The scripts that are an outbound transport, or one hop from one.

    Pure: {filename: source} in, {filename} out. ONE hop, not a closure; see
    the module docstring for what a fixed point drags in.
    """
    direct = {name for name, src in sources.items() if is_direct_transport(src)}
    hop = {name for name, src in sources.items()
           if name not in direct and spawns_any(src, direct)}
    return direct | hop


# ============================================================
# The rule discriminates: synthetic sources, both directions
# ============================================================

_SENDER = "import exchangelib\ndef go(msg):\n    msg.send()\n"
_READER = "import exchangelib\ndef go(acct):\n    return list(acct.inbox.all())\n"
_GMAIL = "from scripts.utils import gmail_auth\ndef go(svc):\n    svc.users().drafts().send(userId='me')\n"
_SPAWNER = 'import subprocess\ncmd = ["python3", "scripts/sender.py"]\nsubprocess.run(cmd)\n'
_TALKER = 'import subprocess\nprint("run python3 scripts/sender.py --to x to send it")\n'
_LOADER = 'import importlib.util\nimportlib.util.spec_from_file_location("s", "scripts/sender.py")\n'


def test_a_source_that_calls_a_send_verb_is_a_transport():
    assert is_direct_transport(_SENDER)
    assert is_direct_transport(_GMAIL)


def test_a_source_that_only_imports_the_library_is_not():
    """The negative case is the whole point: nine of ten exchangelib users in
    scripts/ only read mail, and a library-only rule would deny all ten."""
    assert not is_direct_transport(_READER)
    assert not is_direct_transport("import subprocess\nsubprocess.run(['ls'])\n")
    assert not is_direct_transport("")


def test_a_send_verb_without_its_library_is_not_a_transport():
    """`.send()` on its own belongs to sockets, queues and Telegram bots."""
    assert not is_direct_transport("def go(sock):\n    sock.send()\n")


def test_a_bare_path_literal_in_a_spawning_module_is_a_hop():
    assert spawns_any(_SPAWNER, {"sender.py"})
    assert spawns_any(_LOADER, {"sender.py"})


def test_a_printed_command_string_is_not_a_hop():
    """crm_next.py prints send-email.py invocations for the operator to copy.
    Guidance about a transport is not a call to one."""
    assert not spawns_any(_TALKER, {"sender.py"})


def test_a_path_literal_without_a_spawn_import_is_not_a_hop():
    assert not spawns_any('X = "scripts/sender.py"\n', {"sender.py"})


def test_a_spawn_of_something_else_is_not_a_hop():
    assert not spawns_any(_SPAWNER, {"other.py"})


def test_the_set_is_direct_plus_one_hop_and_stops_there():
    """Synthetic tree: a is the transport, b spawns a, c spawns b. Only a and b
    are in the set. c is the fixed-point overreach the docstring names."""
    tree = {
        "a.py": _SENDER,
        "b.py": 'import subprocess\nsubprocess.run(["python3", "scripts/a.py"])\n',
        "c.py": 'import subprocess\nsubprocess.run(["python3", "scripts/b.py"])\n',
        "d.py": _READER,
    }
    assert send_reachable_scripts(tree) == {"a.py", "b.py"}


def test_an_empty_tree_yields_an_empty_set():
    assert send_reachable_scripts({}) == set()


# ============================================================
# The constant, held against the tree instead of against itself
# ============================================================

_WHOLE = re.compile(r"^Bash\(python3? (scripts/[\w.\-]+\.py):\*\)$")
_SUBCOMMAND = re.compile(r"^Bash\(python3? (scripts/[\w.\-]+\.py) ([\w\-]+):\*\)$")


def _parsed():
    """Every SEND_DENY entry as ('whole'|'sub', script, subcommand|None)."""
    out = []
    for entry in SEND_DENY:
        m = _WHOLE.match(entry)
        if m:
            out.append(("whole", m.group(1), None))
            continue
        m = _SUBCOMMAND.match(entry)
        assert m, f"SEND_DENY entry matches neither deny shape: {entry!r}"
        out.append(("sub", m.group(1), m.group(2)))
    return out


def _script_sources() -> dict[str, str]:
    """{filename: source} for every tracked top-level script.

    The walk lists the scripts and this reads them, so a file can go away in
    between. Silently skipping one would SHRINK the reachable-transport set, and
    `test_every_script_that_can_send_is_denied_whole` asserts
    `reachable - denied == set()` - a script dropped from `reachable` is a
    sending script that passes the deny-list control without being denied. That
    is a false pass on a security control, so the race is retried once and then
    named, never skipped.
    """
    out: dict[str, str] = {}
    for p in tracked_paths(["scripts/*.py"]):
        try:
            out[p.name] = p.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            try:
                out[p.name] = p.read_text(encoding="utf-8", errors="replace")
            except FileNotFoundError as gone:
                raise AssertionError(
                    f"{p} vanished between the walk and the read; the deny-list "
                    f"checks below would answer over a corpus that quietly lost "
                    f"a script that may reach a transport") from gone
    return out


def test_the_tree_still_has_a_transport_to_find():
    """A sweep that finds nothing is green whatever the constant says. This is
    the anchor that keeps the equality below from passing over an empty set."""
    found = send_reachable_scripts(_script_sources())
    assert "send-email.py" in found
    assert "gmail-send.py" in found
    assert len(found) >= 4


def test_every_whole_script_deny_is_a_script_that_can_send():
    """Direction 1 of 2: no dead entry. A name here that cannot reach a
    transport is a stale deny, and a stale deny is how a list stops being read."""
    denied = {Path(s).name for kind, s, _ in _parsed() if kind == "whole"}
    reachable = send_reachable_scripts(_script_sources())
    assert denied - reachable == set()


def test_every_script_that_can_send_is_denied_whole():
    """Direction 2 of 2, and the one that was missing. `gmail-send.py` sat
    outside SEND_DENY for its first 21 days and no test could see it."""
    denied = {Path(s).name for kind, s, _ in _parsed() if kind == "whole"}
    reachable = send_reachable_scripts(_script_sources())
    assert reachable - denied == set()


def test_each_denied_script_is_denied_for_both_interpreters():
    """`python` and `python3` are different command strings to the permission
    matcher, so one without the other is a denylist with a spelling around it."""
    for _, script, sub in _parsed():
        tail = f" {sub}" if sub else ""
        for interpreter in ("python", "python3"):
            assert f"Bash({interpreter} {script}{tail}:*)" in SEND_DENY


def test_every_denied_path_exists_on_disk():
    """A pattern naming a moved or deleted script matches no command at all."""
    for _, script, _sub in _parsed():
        assert (ROOT / script).is_file(), script


def test_action_queue_denies_approve_without_denying_the_whole_script():
    """`approve` IS the synchronous send. `list`, `show` and `dismiss` are the
    read surface the operator drives, and denying the script would take them."""
    subs = {(s, sub) for kind, s, sub in _parsed() if kind == "sub"}
    assert ("scripts/action-queue.py", "approve") in subs
    whole = {s for kind, s, _ in _parsed() if kind == "whole"}
    assert "scripts/action-queue.py" not in whole


# ============================================================
# The built argv, checked against the tree rather than the constant
# ============================================================

def _values_after(cmd, flag):
    i = cmd.index(flag) + 1
    vals = []
    while i < len(cmd) and not cmd[i].startswith("--"):
        vals.append(cmd[i])
        i += 1
    return vals


@pytest.mark.parametrize("tier", sorted(TIER_ALLOWED))
def test_every_tier_denies_every_transport_found_on_disk(tier):
    """The assertion the old one should have been: the expectation comes from
    the scripts, so truncating SEND_DENY fails instead of passing."""
    cmd = build_skill_command("state-check", [], tier=tier)
    disallowed = " ".join(_values_after(cmd, "--disallowedTools"))
    for name in sorted(send_reachable_scripts(_script_sources())):
        assert f"scripts/{name}:*" in disallowed, (tier, name)


@pytest.mark.parametrize("tier", sorted(TIER_ALLOWED))
def test_no_tier_grants_any_transport_found_on_disk(tier):
    """The allowlist is the primary boundary; the denylist is the second one."""
    cmd = build_skill_command("state-check", [], tier=tier)
    allowed = " ".join(_values_after(cmd, "--allowedTools"))
    for name in sorted(send_reachable_scripts(_script_sources())):
        assert name not in allowed, (tier, name)


# ============================================================
# The content gate: the verdict, as four states with four cases
# ============================================================

guard = _load("content_guard_under_test", "scripts/content-guard.py")


@pytest.mark.parametrize("root_state", ["unresolved", "absent", "present"])
def test_a_denylist_with_no_tokens_is_never_usable(root_state):
    """The `and` mutant's case. Zero tokens means nothing to compare against,
    whatever the overlay looks like, so the verdict cannot depend on the root."""
    usable, why = guard.denylist_verdict(False, 0, root_state)
    assert usable is False
    assert why


@pytest.mark.parametrize("root_state", ["unresolved", "absent", "present"])
def test_a_degraded_denylist_is_never_usable_even_with_tokens(root_state):
    """A harvest that failed part-way still has tokens. It is a partial answer
    presented as a whole one, so it is refused too."""
    usable, _why = guard.denylist_verdict(True, 4200, root_state)
    assert usable is False


def test_a_healthy_denylist_is_usable():
    """The negative case. Without it the verdict could return False always and
    every assertion above would still hold."""
    usable, why = guard.denylist_verdict(False, 1, "present")
    assert usable is True
    assert why == ""


def test_the_verdict_names_the_state_and_not_a_guessed_cause():
    """Four states, four sentences. They were one sentence until 2026-08-24 and
    the wrong one was printed on the operator's own machine."""
    sentences = {
        guard.denylist_verdict(False, 0, "unresolved")[1],
        guard.denylist_verdict(False, 0, "absent")[1],
        guard.denylist_verdict(True, 0, "present")[1],
        guard.denylist_verdict(False, 0, "present")[1],
    }
    assert len(sentences) == 4
    assert "could not be resolved" in guard.denylist_verdict(False, 0, "unresolved")[1]
    assert "no DATA overlay at this path" in guard.denylist_verdict(False, 0, "absent")[1]
    assert "harvest failed" in guard.denylist_verdict(True, 0, "present")[1]
    assert "no entities to guard" in guard.denylist_verdict(False, 0, "present")[1]


def test_an_unresolved_root_is_reported_before_a_degraded_harvest():
    """Ordering, pinned. The unresolved state used to be decided by calling
    `.is_dir()` on None, so the one path written to degrade gracefully raised
    instead and the operator read a traceback."""
    assert "could not be resolved" in guard.denylist_verdict(True, 0, "unresolved")[1]


# ============================================================
# main() maps a real data root onto those three states
# ============================================================

def _run_main(monkeypatch, capsys, *argv, resolver=None):
    """Drive content-guard.main() with an empty denylist and no scan work."""
    monkeypatch.setattr(guard, "build_denylist", lambda *a, **k: Denylist())
    if resolver is not None:
        monkeypatch.setattr(guard, "get_data_root", resolver)
    monkeypatch.setattr(sys, "argv", ["content-guard.py", *argv])
    rc = guard.main()
    return rc, capsys.readouterr()


def test_a_resolver_that_raises_is_reported_as_unresolved(monkeypatch, capsys):
    """The pure verdict cannot see this: `main` owns the mapping, and it is the
    mapping that raised AttributeError on the graceful path until 2026-08-24."""
    def boom():
        raise RuntimeError("HEADING_OS_DATA names nothing")

    rc, out = _run_main(monkeypatch, capsys, "--files", "scripts/heading_cli.py",
                        resolver=boom)
    assert rc == 0
    assert "could not be resolved" in out.out


def test_a_root_that_is_not_a_directory_is_reported_as_absent(monkeypatch, capsys, tmp_path):
    gone = tmp_path / "not-here"
    rc, out = _run_main(monkeypatch, capsys, "--data-root", str(gone),
                        "--files", "scripts/heading_cli.py")
    assert rc == 0
    assert "no DATA overlay at this path" in out.out


def test_a_real_but_empty_overlay_is_reported_as_present(monkeypatch, capsys, tmp_path):
    """The discriminating third case: the directory IS there, so blaming the
    path would be the guessed cause the gate was rewritten to stop giving."""
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    rc, out = _run_main(monkeypatch, capsys, "--data-root", str(overlay),
                        "--files", "scripts/heading_cli.py")
    assert rc == 0
    assert "no entities to guard" in out.out
    assert "could not be resolved" not in out.out
    assert "no DATA overlay at this path" not in out.out


# ============================================================
# The gate refuses a clean verdict over an empty denylist
# ============================================================

def _run_guard(monkeypatch, capsys, tmp_path, *, tokens, verdict_usable):
    """Drive content-guard.main() over one real engine file.

    `verdict_usable` is what the (mocked) verdict claims, so a BROKEN predicate
    can be simulated without touching the predicate that ships. Nothing is
    removed from the gate: the control under test is the refusal at the clean
    line, and it is reached by lying to the gate, not by disarming it.
    """
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    dl = Denylist(tokens=dict(tokens))
    monkeypatch.setattr(guard, "build_denylist", lambda *a, **k: dl)
    monkeypatch.setattr(guard, "denylist_verdict", lambda *a, **k: (verdict_usable, "x"))
    monkeypatch.setattr(sys, "argv",
                        ["content-guard.py", "--data-root", str(overlay),
                         "--files", "scripts/heading_cli.py"])
    rc = guard.main()
    return rc, capsys.readouterr()


def test_a_zero_token_denylist_cannot_produce_a_clean_verdict(monkeypatch, capsys, tmp_path):
    """The `or`->`and` mutant printed exactly this and exited 0."""
    rc, out = _run_guard(monkeypatch, capsys, tmp_path, tokens={}, verdict_usable=True)
    assert rc == 1
    assert "clean" not in out.out
    assert "REFUSED" in out.err


def test_the_refusal_says_nothing_was_compared(monkeypatch, capsys, tmp_path):
    """The message has to name the reason, or the next reader retries the gate."""
    _rc, out = _run_guard(monkeypatch, capsys, tmp_path, tokens={}, verdict_usable=True)
    assert "0 " in out.err and "token" in out.err


def test_a_populated_denylist_still_reports_clean(monkeypatch, capsys, tmp_path):
    """The negative case. Without it the refusal could fire unconditionally and
    every assertion above would still hold, while the gate refused everything."""
    rc, out = _run_guard(monkeypatch, capsys, tmp_path,
                         tokens={"vex-thorne": "crm-slug"}, verdict_usable=True)
    assert rc == 0
    assert "clean" in out.out


def test_the_early_return_is_what_normally_keeps_the_refusal_unreachable(
        monkeypatch, capsys, tmp_path):
    """With the shipped verdict saying "not usable", an empty denylist exits 0
    and says "skipped", which is the public-clone and CI path, unchanged."""
    rc, out = _run_guard(monkeypatch, capsys, tmp_path, tokens={}, verdict_usable=False)
    assert rc == 0
    assert "skipped" in out.out
    assert "REFUSED" not in out.err
