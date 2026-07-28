#!/usr/bin/env python3
"""The Canopus freeze check, as run by every route into the test suite.

Separate from scripts/canopus.py (an operator CLI) and from run-tests.py (which
re-execs the interpreter at import time via ensure_venv, so it is not safely
importable from a test).

Two callers, deliberately: tests/conftest.py runs it at pytest session start,
and scripts/run-tests.py runs it before spawning the suite. conftest covers the
CLASS of invocations rather than one command — bare `pytest tests/test_thing.py`
is the inner-loop command a build runs dozens of times per slice, while
run-tests.py runs once at the end or not at all. The duplicate call costs one
extra read_freeze.

This is where the freeze guarantee actually fires. Everything else about the
freeze is inert without it, because a verification that is never invoked fails
100% of the time regardless of how well its expected value is protected.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

from scripts.utils.atomic import atomic_write_text
from scripts.utils.canopus_freeze import (
    ANCHOR_MISSING,
    ANCHOR_RECORDED,
    ANCHOR_UNBOUND,
    APPROVED,
    LOCK_HELD,
    LOSS_OF_LOCK,
    FreezeCorrupt,
    build_attestation,
    frozen_test_files,
    lock_state,
    open_release_window,
    read_freeze,
    read_ledger,
    tally_collection,
    unreleased_freeze,
    verify_manifest,
    write_attestation,
)
from scripts.utils.canopus_git import AnchorResolution, resolve_anchor
from scripts.utils.canopus_tree import tree_state
from scripts.utils.colors import GREEN, RED, RESET, YELLOW


def loss_of_lock_sentences(report: dict, resolution: AnchorResolution) -> list[str]:
    """One sentence per CAUSE of LOSS OF LOCK, said together when several hold.

    `lock_state` reaches this state from FOUR independent causes, and three of
    them arrive with `report["held"]` TRUE: the contract has not moved, so a
    blanket "the frozen contract moved" is simply false, and it sends the
    operator to `verify` for a per-file report that lists nothing.

    Fixed as a SHAPE rather than as the instance that was reported. This is the
    tenth time on this project that a guard was repaired for the case in front of
    its author and left open for its siblings, so every cause gets its own
    sentence here and the enumeration is the thing a reader checks against
    `lock_state`.

    Pure string work, and it never raises: the gate that calls it fails OPEN if
    it does. The closing fallback covers a `lock_state` that reddens for a cause
    this function does not know, which is exactly the drift the enumeration is
    otherwise vulnerable to.
    """
    sentences: list[str] = []
    if not report["held"]:
        sentences.append("The frozen contract moved; run "
                         "`python scripts/canopus.py verify` for the per-file "
                         "report.")
    if resolution.status == ANCHOR_UNBOUND:
        sentences.append(resolution.approval_reason
                         or "the anchor is not in the repository this freeze "
                            "recorded, so the approval cannot be attributed")
    if resolution.status == ANCHOR_MISSING:
        sentences.append(f"The anchor artifact {resolution.anchor} is gone, so "
                         f"the approved hash cannot be read from it.")
    if (resolution.status == ANCHOR_RECORDED
            and resolution.value != report["recomputed_root"]):
        sentences.append(f"The anchor records {resolution.value} and this tree "
                         f"computes {report['recomputed_root']}, so this freeze "
                         f"is not the one that was approved.")
    if not sentences:
        sentences.append(f"The lock is red with anchor status "
                         f"{resolution.status!r}, which this gate cannot name; "
                         f"run `python scripts/canopus.py verify`.")
    return sentences


def _entries(root: Path) -> list:
    """The ledger, or an empty list. Answers rather than raising.

    read_ledger swallows OSError and UnicodeDecodeError and skips damaged lines
    from wire 2.2 onward, so this guard is a second wall rather than the first
    one. Both walls are wanted here and the reason is not symmetry: freeze_gate
    runs at every pytest session start, and a raise here fails OPEN, crashing
    the harness that was supposed to report the state. A guard whose only cost
    is four lines is cheaper than the next input nobody predicted.
    """
    try:
        return read_ledger(root)
    except (OSError, ValueError):
        return []


def _no_manifest(root: Path) -> int:
    """What the gate says when there is no manifest, which is TWO states.

    An ordinary day is silent, and it has to stay silent: a fresh clone has no
    `.canopus/` at all, and a gate that speaks on every CI run teaches an
    operator to skim it.

    The other two states are not ordinary, and until wire 2.2 the WORSE of them
    was the quieter one. A sanctioned `release --window` printed an amber line at
    every later session start. `rm .canopus/freeze.json` under a held lock
    printed NOTHING and exited 0, so deleting the manifest was cheaper than
    releasing it and the ledger, whose whole purpose is to be evidence against
    exactly that, was never read for it.

    So the deletion answers RED, one step louder than the window rather than one
    step quieter, and the escape it names is the LOGGED one. A forced release
    clears state it never parses and writes a `force_release` line, which is
    precisely what tells a cleared lock apart from a deleted one.
    """
    entries = _entries(root)
    vanished = unreleased_freeze(entries)
    if vanished is not None:
        print(f"{RED}canopus: the ledger records a freeze taken "
              f"{vanished.get('ts') or 'at an unrecorded time'} "
              f"(label: {vanished.get('label') or 'unrecorded'}) that no release "
              f"closed, and the manifest it wrote is GONE. There is no contract "
              f"left to check, so the suite is treated as unverified. Re-freeze "
              f"it, or end the lock the way the ledger can see: `python "
              f"scripts/canopus.py release --force --window --reason "
              f"\"<why>\"`.{RESET}")
        return 1
    window = open_release_window(entries)
    if window is not None:
        print(f"{YELLOW}canopus: a release window is open{RESET}  opened "
              f"{window.get('ts') or 'at an unrecorded time'}: "
              f"{window.get('reason') or 'no reason recorded'}. No lock is "
              f"held, so a green suite proves nothing about the contract.")
    return 0


def freeze_gate(root: Path) -> int:
    """The claim, as it stands after wire 2.2, and no wider.

    A build cannot reach green while its contract is moved, AND it cannot reach
    green by making the anchor's repository invisible. The older wording here
    claimed only the first half and was falsified by the second: exporting
    GIT_DIR turned a genuine LOSS OF LOCK into LOCK HELD with exit 0, because
    resolution fell back to the anchor's WORKING copy the moment git answered
    no_repo or no_git.

    The claim scoped to what was measured, because the earlier headline here read
    wider than its evidence. Three routes to "I cannot see the anchor's
    repository" were measured red after this slice where they were green before
    it: exporting GIT_DIR at a decoy directory, renaming the anchor repository's
    `.git`, and removing `git` from PATH. For BLINDING, and only for blinding,
    the bypass now costs a builder more than doing nothing. It does not follow
    that the gate cannot be defeated. Three routes stay open at zero cost and
    exit 0: `rm -rf .canopus/`, which is gitignored so git never saw it;
    `pytest --noconftest`; and `pytest --confcutdir` pointed past this conftest.
    All three are in the "what this does not buy" material in docs/EXTENDING.md,
    so nothing is hidden — what was wrong was a headline broader than its proof.

    What it still does NOT claim, said here rather than left to be discovered.
    Nothing in this file defends against a builder who replaces the `git` binary
    on PATH: every answer this gate binds to comes from a child process, and a
    substituted binary can say whatever the builder wants. And the identity binds
    a repository's LINEAGE rather than an instance — it is a digest over the root
    commits — so a `git clone` of the anchor's repository carries the same root
    commits and reads as the same repository. That is deliberate (a relocated or
    cloned repository IS the same repository, and this workspace has been
    relocated once), and it means the binding proves which HISTORY the anchor
    belongs to, never which copy of it a command read.

    Silent when no freeze is active, no release window is open, and the ledger
    records no freeze whose manifest has vanished. That is the ordinary day. The
    other two states are `_no_manifest`'s business, and the ordering of their
    volumes is the point: a deleted manifest is louder than a released one, not
    quieter.

    NEVER RAISES, whatever the state directory looks like. That is a SHAPE, held
    by the wrapper below rather than by a handler per input, because this is the
    third repair of the same invariant in one slice: a raise here fails OPEN. The
    gate runs at every pytest session start, so an escaping exception crashes the
    harness that was supposed to report a state, and the PreToolUse dispatcher's
    catch-all logs an advisory and CONTINUES while writes to frozen paths sail
    through. Measured, not reasoned: `.canopus/` at mode 000 made `read_freeze`
    raise PermissionError out of `Path.exists()`, past a handler that named only
    FreezeCorrupt.
    """
    try:
        return _freeze_gate(root)
    except Exception as exc:  # noqa: BLE001 — totality IS the requirement
        # Named, so this is a report rather than a swallow, and RED with exit 1
        # so the unexpected fails closed. An operator seeing this line is looking
        # at a gate that could not establish a state, which is not the same claim
        # as a moved contract, and the sentence says so.
        print(f"{RED}canopus: the freeze state could not be established, so the "
              f"contract is treated as unverified: "
              f"{type(exc).__name__}: {exc}{RESET}")
        return 1


def _freeze_gate(root: Path) -> int:
    """The gate proper. Call `freeze_gate`; this one is allowed to raise."""
    try:
        manifest = read_freeze(root)
    except FreezeCorrupt as exc:
        print(f"{RED}canopus: {exc}{RESET}")
        print(f"{RED}canopus: clear it with `python scripts/canopus.py release "
              f"--force --window --reason \"<why>\"`{RESET}")
        return 1
    if manifest is None:
        return _no_manifest(root)

    # A freeze is active, so the contract cannot be checked and NOT be checked:
    # an unreadable member (permissions, a vanished mount) must fail the gate,
    # not crash run-tests.py with a traceback that reads like a tooling bug.
    try:
        report = verify_manifest(manifest, root)
        resolution = resolve_anchor(manifest)
        status, value = resolution.status, resolution.value
    except OSError as exc:
        # The handler stays the filesystem one, and git_output is what keeps
        # that true: it converts OSError, SubprocessError AND ValueError into
        # None. ValueError is not decoration. subprocess.run raises it for an
        # argument holding an embedded NUL byte, and text=True decoding raises
        # UnicodeDecodeError, a ValueError subclass, on a non-UTF-8 gate
        # artifact. Both escaped before wire 2.1, and either one raising here
        # fails OPEN: this gate crashes the pytest session instead of reporting
        # a state, which is worse than any state it could report.
        print(f"{RED}canopus: the frozen contract could not be read, so it cannot "
              f"be verified: {exc}{RESET}")
        return 1
    state = lock_state(report, status, value)

    if state == LOSS_OF_LOCK:
        # Every cause, never the first one thought of. An operator who fixes
        # only the half they were told about is back here on the next run, and
        # an operator told the contract moved when it did not goes looking
        # through a per-file report that lists nothing.
        detail = " ".join(loss_of_lock_sentences(report, resolution))
        print(f"{RED}canopus: {LOSS_OF_LOCK}. {detail}{RESET}")
        return 1
    colour = GREEN if state == LOCK_HELD else YELLOW
    print(f"{colour}canopus: {state}{RESET} (label: {manifest['label']})")
    if resolution.approval != APPROVED:
        # The fourth surface, and the one that actually fires: conftest runs it
        # at every pytest session start and run-tests.py runs it before the
        # suite, while status, verify and pack are commands an operator chooses
        # to type. The lock axis already falls to amber when the approval is
        # uncommitted, so this line adds the REASON rather than the signal,
        # which is precisely what an unexplained amber costs an operator.
        print(f"{YELLOW}canopus: {resolution.approval}{RESET}  "
              f"{resolution.approval_reason}")
    return 0


def pytest_child_env(**overrides: str) -> dict:
    """The environment for a pytest child this codebase launches: ours, minus PYTEST_.

    Blanket prefix, never a denylist. PYTEST_ADDOPTS alone can load a plugin that
    overrides pytest_pyfunc_call and makes every frozen test report passed
    without executing, and naming the variables you thought of leaves whichever
    one you did not. The same shape as canopus_git._child_env, which does this
    for GIT_.

    ONE definition, because the two children it serves are COMPARED against each
    other: run-tests.py launches the gate run, canopus_contract launches the
    freeze-time capture, and build_attestation holds the first to the plugin set
    the second recorded. While only the gate child was scrubbed, the baseline was
    a photograph of the operator's shell. Measured on a scratch tree: a clean
    shell captured

        ['dist:_pytest', 'dist:anyio', 'dist:pytest_asyncio', 'dist:pytest_cov',
         'dist:xdist']

    and the same freeze with PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 exported captured
    ['dist:_pytest'] alone. The scrubbed gate run then loaded all five and
    refused with four "a plugin the freeze did not record was loaded" reasons,
    permanently, until the freeze was retaken in a clean shell. Fail-closed, and
    still the wrong posture: the canonical gate was the run that refused while a
    bare `pytest` in the same shell was the run that attested. The baseline is a
    property of the TREE, not of the shell the operator froze from.

    The CANOPUS_ names are deliberately NOT scrubbed here. CANOPUS_NO_ATTEST and
    CANOPUS_PLUGIN_DUMP are how a caller tells a child what it is for, and both
    are passed in as *overrides* by the callers that need them.
    """
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("PYTEST_")}
    env.update(overrides)
    return env


def _library_dirs() -> tuple:
    """Every directory the running interpreter treats as its own library.

    Named by role rather than by path. `.venv/` sits UNDER the working tree, so a
    plugin from site-packages resolves in-tree and would be marked; excluding
    `.venv` by name would be the denylist shape this project keeps getting wrong,
    and it would also miss a venv named anything else.

    NEVER RAISES, and the reason is WHERE it runs rather than how likely a raise
    is. The result is cached at module import, and every import of this module is
    outside a handler: tests/conftest.py imports `freeze_gate` from inside
    `pytest_sessionstart`, which has no handler of its own, and run-tests.py
    imports at its top level. So an exception here does not fail OPEN the way
    `freeze_gate` deliberately does — it kills the pytest session with an
    internal error before any gate can report a state, and kills run-tests.py
    before it can run anything. Probability low, cost total.

    Degrading to `()` marks MORE plugins in-tree, which is the conservative
    direction for a field nothing judges yet, and the failure is named on stderr
    rather than swallowed: unlike the sentinel handlers below, this one loses
    information a reader of the record would want.
    """
    try:
        import site
        import sysconfig

        dirs = []
        for key in ("purelib", "platlib", "stdlib", "platstdlib"):
            path = sysconfig.get_paths().get(key)
            if path:
                dirs.append(Path(path).resolve())
        # Asked for rather than caught. Some embedded builds and older
        # virtualenvs ship a `site` without this function, and a handler around
        # it would report nothing this one does not already report.
        getsitepackages = getattr(site, "getsitepackages", None)
        if getsitepackages is not None:
            dirs.extend(Path(p).resolve() for p in getsitepackages())
        return tuple(dirs)
    except Exception as exc:  # noqa: BLE001 — an import-time raise kills the session
        print(f"canopus: the interpreter's library directories could not be "
              f"located, so a plugin loaded from one reads as in-tree: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return ()


_LIBRARY_DIRS = _library_dirs()


def _plugin_pairs(config) -> list:
    """Every (name, plugin) pytest has registered, or [] when there is no manager.

    Answers rather than raising, like everything else on this path: the caller is
    reached from a session-finish hook, and a description that raises would cost
    the record it was written to produce.
    """
    lister = getattr(getattr(config, "pluginmanager", None), "list_name_plugin", None)
    return list(lister()) if callable(lister) else []


def _intree_rel(origin, root: Path) -> str | None:
    """*origin* as a root-relative POSIX path, when it is the TREE's own code.

    None for anything the interpreter owns. `.venv/` lives under the working
    tree, so "under root" alone would mark every installed plugin as in-tree.

    The handler below neither logs nor re-raises, and that is the reading this
    diff applies wherever a handler's SENTINEL IS THE ANSWER: an origin that
    cannot be resolved to a path is not in the tree, which is exactly what None
    says here, and the caller acts on it. Nothing is lost, so there is nothing
    to report — and at session finish, over every registered plugin, a line per
    unresolvable origin would be noise on the one surface an operator reads. The
    opposite reading governs `_library_dirs` above, where the failure silently
    degrades a whole exclusion set and IS therefore printed. `_rel` on this
    class takes the same shape as this one and is left as it stands.
    """
    if not origin:
        return None
    try:
        resolved = Path(str(origin)).resolve()
    except (OSError, ValueError):
        return None
    if not resolved.is_relative_to(root):
        return None
    if any(resolved.is_relative_to(library) for library in _LIBRARY_DIRS):
        return None
    return resolved.relative_to(root).as_posix()


def _module_name(plugin) -> str | None:
    """The dotted module a plugin came from, or None when it cannot be read.

    Four shapes, and every one of them was MEASURED on a real session rather
    than reasoned about. The first draft of this function had two and the
    measurement falsified it twice in one run.

    * A plugin FILE is registered as a module object, whose `__name__` is the
      dotted module.
    * A BLOCKED name (`-p no:cacheprovider`, and pytest blocks four of its own
      that way) is registered with the plugin `None`. It names a plugin that is
      NOT loaded, so it has no distribution — and reading `type(None).__module__`
      gave it one called `builtins`, which then differed between the freeze
      probe (which passes `-p no:cacheprovider`) and the gate run. A plugin that
      is absent by request must never enter the compared set.
    * A CLASS is registered occasionally (`legacypath-tmpdir`), and a class's own
      `__module__` is the answer; `type(a class).__module__` is `builtins` for
      every class there is.
    * Anything else is an INSTANCE (xdist's DSession, pytest's TerminalReporter,
      and every plugin registered under `str(id(plugin))`), which carries no
      `__file__` and no module `__name__`, so its distribution can only be read
      from the class that defined it.

    `isinstance(plugin, ModuleType)` rather than "has a `__name__`": a class and
    a function both carry a `__name__` that is their OWN name, not a module's,
    and reading one would invent a distribution called `TerminalReporter`.
    An INFERRED name is additionally checked against `sys.modules`, and a module
    object's own name is not. A module object is its own evidence; a class
    attribute merely CLAIMS a module. Measured, and it is the difference between
    a gate that runs and one that refuses every parallel run: under `-n auto`
    each xdist worker registers a `WorkerInteractor` whose class `__module__` is
    `__channelexec__`, execnet's synthetic namespace for source sent down a
    channel. Nothing of that name was ever imported, it has no file, and no
    controller and no freeze probe carries it — so trusting the claim gave every
    worker a distribution its baseline could never hold. That plugin is
    registered under `str(id(plugin))`, so with the claim refused it lands on
    `anon:unresolved`, the row spec 1.3a wrote for exactly this.

    The check is not extended to module objects on purpose. A module absent from
    `sys.modules` would fall through to `anon:`/`name:`, which is NOT compared,
    so tightening there would hand an attacker a way OUT of the comparison; the
    weaker reading leaves them only the same-name substitution that the by-name
    comparison already names as its limit.
    """
    if plugin is None:
        return None
    if isinstance(plugin, ModuleType):
        candidate = getattr(plugin, "__name__", None)
        return candidate if isinstance(candidate, str) and candidate else None
    claimed = (getattr(plugin, "__module__", None) if isinstance(plugin, type)
               else getattr(type(plugin), "__module__", None))
    return claimed if isinstance(claimed, str) and claimed in sys.modules else None


def _plugin_identity(name, plugin, root: Path) -> str:
    """The COMPARABLE identity of one registered plugin. See spec 1.3a.

    A registration name is not comparable across two processes, which is what
    wire 2.3's first measurement established rather than assumed: pytest
    registers an anonymous plugin under `str(id(plugin))`, its memory address,
    and a conftest plugin under its ABSOLUTE path. Seventeen of one gate run's
    sixty-six names were addresses, and a comparison over them refuses every
    honest run and leaks an operator's home directory into a public hash.

    So the identity is derived from the plugin's ORIGIN, in the four cases the
    spec's table names:

      intree:<path>   under the tree, outside the interpreter's own libraries
      dist:<package>  anywhere else, with a readable module
      anon:unresolved no readable module, registered under an address
      name:<name>     no readable module, registered under something else

    Measured: this collapses sixty-six raw names to seven identities, and the
    `dist:` subset is identical in the freeze probe, the gate controller and
    every gate worker. That subset is compared, and so is every `intree:`
    identity pytest did not register as a collected conftest. `process_facts`
    owns that partition and argues it. A collected conftest, and every
    `anon:`/`name:` entry, are recorded and not compared.
    """
    rel = _intree_rel(getattr(plugin, "__file__", None), root)
    if rel is not None:
        return f"intree:{rel}"
    module = _module_name(plugin)
    if module:
        return f"dist:{module.split('.')[0]}"
    if str(name).isdigit():
        return "anon:unresolved"
    return f"name:{name}"


def _intree_path(identity: str) -> str | None:
    """The tree-relative path inside an `intree:` identity, else None."""
    prefix = "intree:"
    return identity[len(prefix):] if identity.startswith(prefix) else None


def _collected_conftest_modules(config) -> tuple:
    """The conftest MODULE OBJECTS collection actually imported, or ().

    pytest keeps them in `pluginmanager._dirpath2confmods`, declared
    `dict[Path, list[ModuleType]]` at `_pytest/config/__init__.py:481`, filled
    at :714 as collection walks directories and read back at :718. Object
    identity is the point: a plugin cannot put itself in here by writing an
    attribute, only by being the module collection imported.

    FAILS CLOSED. An absent or wrong-shaped attribute yields (), so NOTHING is
    exempt and every in-tree plugin is compared. If a future pytest renames this
    private attribute the gate gets noisy — an honest run's in-tree conftests
    start needing to match the freeze — rather than quietly exempting everything
    in the tree. Noisy and safe beats quiet and wrong; the private-attribute
    dependency is a real cost and is on the slice's open list.
    """
    mapping = getattr(getattr(config, "pluginmanager", None),
                      "_dirpath2confmods", None)
    if not isinstance(mapping, dict):
        return ()
    modules: list = []
    for entry in mapping.values():
        if isinstance(entry, (list, tuple)):
            modules.extend(entry)
    return tuple(modules)


def _registered_by_collection(plugin, collected_modules) -> bool:
    """True when THIS registration is one collection imported. `is`, not `==`.

    Every earlier version of this predicate decided on a string the adversary
    writes, and each was defeated in turn: the file's basename (`plug/conftest.py`
    named by `pytest_plugins` hijacked the run), then "the registration name is
    the origin's path" — which compares pytest's trustworthy `name` against
    `plugin.__file__`, an attribute the PLUGIN AUTHOR sets. `plug/evil.py`
    containing `__file__ = __name__` makes the two coincide, and the hijacker
    reads as collected. Reproduced before this predicate was written.

    An object cannot forge `is`. The plugin either IS a module collection
    imported, or it is not.
    """
    return any(plugin is module for module in collected_modules)


def process_facts(config, root: Path) -> dict:
    """What configured this interpreter, recorded and normalised, not judged.

    Judgement lives in `build_attestation`, so this stays a description of the
    process and the reasons stay in one list with every other reason an operator
    reads beneath NOT ATTESTED.

    The three plugin fields are one partition of the registered set by identity
    (see `_plugin_identity`), not three separate readings of it: `plugins` is
    the COMPARED subset, mapped to one representative origin for a human to
    read, and the other two carry the entries the comparison deliberately leaves
    alone. An entry belongs to exactly one of them.

    The compared subset is every `dist:` identity plus every `intree:` identity
    that is not exempt, and an identity is exempt only when EVERY registration
    folding into it is a conftest module collection itself imported
    (`_collected_conftest_modules`, `_registered_by_collection`).

    That is a PROPERTY, and it is decided on OBJECT IDENTITY rather than on any
    string, because five earlier versions decided on strings and review defeated
    each in turn. Three enumerated channels: `-p`, then PYTEST_PLUGINS one
    channel over, then `pytest_plugins` declared in a test module. The fourth
    keyed on the file being called `conftest.py`, and a rename defeated it. The
    fifth asked whether the registration name equalled `plugin.__file__`, and
    `__file__ = __name__` in the hijacker made that trivially true. Every string
    in that list is one the adversary writes; `is` is not.

    The AND across registrations is the other half, and it closes a defeat older
    than any of them: several registrations can fold into ONE identity, so an
    identity exempted because SOME registration was collected let a plugin that
    forged its `__file__` onto the honest `tests/conftest.py` vanish from the
    record entirely.

    Spec 1.3a justifies leaving in-tree plugins uncompared by
    collection-dependence, and collection-dependence is a property of what
    COLLECTION loads: which conftests load depends on what is collected, so the
    freeze probe (the contract directory) and the gate run (the whole suite)
    legitimately differ. Anything else in the tree is registered because a name
    reached pytest, through one of those three routes or a fourth nobody has
    found, and every one of those is the explicitly-named side of the same rule.

    Measured before adopting it: in an honest run of this repository the only
    in-tree identity is `intree:tests/conftest.py`, registered by collection
    under its own path, so the count of compared in-tree plugins is ZERO and the
    compared set does not move. The freeze probe and the gate run stay in
    agreement.

    What stays open, stated where the superseded reasoning stood rather than
    declared closed: an in-tree CONFTEST is still uncompared, for the
    collection-dependence reason above. `GUARD_NAMES_ANCESTOR` watches only
    `conftest.py`, so a NEW non-conftest file under a guarded ancestor still does
    not redden `verify_manifest` — under this rule it can no longer smuggle a
    plugin past the comparison, but it is not the guard that stops it.
    """
    root = Path(root).resolve()
    collected_modules = _collected_conftest_modules(config)
    identities: dict[str, str | None] = {}
    exempt: dict[str, bool] = {}
    for name, plugin in _plugin_pairs(config):
        origin = getattr(plugin, "__file__", None)
        identity = _plugin_identity(name, plugin, root)
        # First origin wins: forty-six `_pytest` registrations fold into one
        # identity, and one representative file answers "where did this come
        # from" as well as forty-six would.
        identities.setdefault(identity, str(origin) if origin else None)
        # Per REGISTRATION, then folded with AND, because an identity can carry
        # several. A set of "identities that had a collected registration"
        # exempted the whole identity on the strength of ONE member: measured,
        # a plugin whose `__file__` was forged onto the honest
        # `tests/conftest.py` folded into that identity, was covered by the
        # honest module's exemption, and did not appear in the record at all.
        # Every registration must be collected for the identity to be exempt.
        by_collection = _registered_by_collection(plugin, collected_modules)
        exempt[identity] = exempt.get(identity, True) and by_collection
    option = getattr(getattr(config, "option", None), "plugins", None) or ()
    compared = {
        identity: origin for identity, origin in sorted(identities.items())
        if identity.startswith("dist:")
        or (_intree_path(identity) is not None and not exempt.get(identity, False))
    }
    return {
        "plugins": compared,
        "intree_plugins": sorted(
            _intree_path(identity) for identity in identities
            if _intree_path(identity) is not None and identity not in compared
        ),
        # Recorded, never compared. An anonymous plugin was created in-process
        # by an already-loaded one, so it is downstream of a `dist:` entry the
        # comparison does see; a `name:` entry has no origin to compare at all.
        # Their absence from the record would be the quiet part: a reader of the
        # evidence pack would not know they were there.
        "other_plugins": sorted(
            identity for identity in identities
            if identity.startswith(("anon:", "name:"))
        ),
        # The PARSED option. argv carries only one of the three channels that
        # reach it: `-p` on the command line shows up in invocation_params.args,
        # while PYTEST_ADDOPTS and an ini `addopts` never do, and a reader
        # written against argv would see one channel in three.
        "option_plugins": [str(name) for name in option],
        # Names only. A value can carry a token, and this record is committed
        # into the evidence pack.
        "env_configured": sorted(k for k in os.environ if k.startswith("PYTEST_")),
        "launcher": os.environ.get("CANOPUS_LAUNCHER") or "bare",
        "workers": [],
    }


class AttestationRecorder:
    """One pytest session's attestation state, driven by the conftest hooks.

    A plain object rather than module-level globals in conftest, because the
    tests that exercise these hooks would otherwise have to monkeypatch the
    LIVE session's counters. Measured: they did, and the suite's own run
    recorded 20 of 31 reports because eleven tests redirected the tally into a
    throwaway dict. A record that the test suite can silently corrupt is worse
    than no record.

    Duck-typed against pytest's session, config, item, and report objects; no
    pytest import, so run-tests.py can import this module outside the venv.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root_digest: str | None = None
        self.frozen: dict | None = None
        self.patterns: list[str] = ["test_*.py"]
        # The per-file item counts taken at freeze time by `freeze --contract`.
        # Empty for a wire 1 freeze, and an absent entry keeps the wire 1
        # behaviour for that file rather than failing it.
        self.baseline: dict = {}
        # The plugin set captured at freeze time, or None when the freeze
        # recorded none. None and empty are the same answer here and both refuse:
        # with nothing to compare against, every plugin set is acceptable.
        self.plugin_baseline: list | None = None
        # Deselections arrive BEFORE the tally exists (see deselected below), so
        # they are buffered by root-relative path and folded in on every route
        # that builds or rebuilds self.frozen.
        self.pending_deselected: dict[str, int] = {}
        # One plugin-name list per xdist worker, shipped home the way the
        # deselection counts already are. A worker is a separate interpreter and
        # can be configured separately from the controller, so the controller's
        # own list describes the controller and nothing else.
        # None stands for a worker whose description failed; see merge_worker.
        self.worker_plugins: list[list[str] | None] = []
        # The tree sample taken at collection, once per session. Sampled at
        # collection rather than at __init__ time, because __init__ can run
        # before pytest has even started, and sampled once rather than on every
        # call because it is the FINISH sample that gets compared against it --
        # taking a fresh one on every read would compare the tree to itself.
        self.tree_at_start: dict | None = None

    def _rel(self, candidate) -> str | None:
        """Root-relative POSIX path, or None when it lies outside the tree."""
        path = Path(str(candidate))
        if not path.is_absolute():
            path = self.root / path
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except (ValueError, OSError):
            return None

    def _frozen_names(self, config) -> list[str] | None:
        """The frozen test files, or None when no freeze is active."""
        manifest = read_freeze(self.root)
        if manifest is None:
            return None
        self.patterns = config.getini("python_files") or ["test_*.py"]
        self.baseline = manifest.get("baseline") or {}
        self.plugin_baseline = manifest.get("plugins") or None
        self.root_digest = verify_manifest(manifest, self.root)["recomputed_root"]
        return frozen_test_files(manifest, self.patterns)

    def collect(self, session) -> None:
        """Record which frozen tests this run will actually execute.

        Fires in a plain run and inside each xdist worker. The xdist CONTROLLER
        never reaches it: collection happens in the workers, and the controller's
        session.items stays empty. seed_from_ids below is the controller's route.

        The tree's START sample is taken here, unconditionally and before the
        no-freeze early return below: it is the sample `finish`'s later one is
        compared AGAINST, and it has to exist even on a session this recorder
        goes on to decide has no freeze to attest.
        """
        if self.tree_at_start is None:
            self.tree_at_start = self._tree()
        frozen = self._frozen_names(session.config)
        if frozen is None:
            return
        collected = [
            rel for rel in (self._rel(getattr(item, "path", "")) for item in session.items)
            if rel is not None
        ]
        self.frozen = tally_collection(frozen, collected)
        self._apply_pending()

    def seed_from_ids(self, config, ids) -> None:
        """Seed the controller's tally from a worker's collected node ids.

        Measured, not assumed: under -n auto the controller sees no items at
        collection time, so without this the canonical gate records
        "collected nothing" for every frozen file and can never attest. The ids
        arrive post-deselection, which is why workers ship their deselection
        counts back separately.

        Also the controller's ONLY collection-time hook under xdist -- `collect`
        above never fires there, since collection happens in the workers and the
        controller's own `session.items` stays empty. So the tree's START sample
        is taken here too, unconditionally and before the tally guard below, for
        the same reason `collect` takes it before ITS early return: without it
        the controller's `finish` had a live finish sample and no start sample to
        compare it against, and `build_attestation` refused every `-n auto` run
        on that ground alone. Measured, not assumed: a plain `-n auto` run over
        this suite read NOT ATTESTED with exactly that reason before this line
        existed.
        """
        if self.tree_at_start is None:
            self.tree_at_start = self._tree()
        if self.frozen is not None:
            return
        frozen = self._frozen_names(config)
        if frozen is None:
            return
        collected = [
            rel for rel in (self._rel(str(node_id).split("::", 1)[0]) for node_id in ids)
            if rel is not None
        ]
        self.frozen = tally_collection(frozen, collected)
        self._apply_pending()

    def merge_worker(self, worker_output) -> None:
        """Fold one worker's deselection counts into the controller's tally.

        pytest's deselection hook fires inside the worker that did the
        collecting, so the controller learns about it only through the worker's
        shipped-back output.

        Taken at face value, never summed: every xdist worker collects the FULL
        set and deselects identically, so adding across workers multiplied the
        count by the worker number. The larger of the two wins, so a worker that
        somehow filtered more is not silently under-reported.

        The plugin list is folded in BEFORE the tally guard, and the two are not
        the same question. A deselection count is meaningless without a tally to
        fold it into; a worker's plugin list describes that worker's interpreter
        whether or not this controller ever built one.

        A worker whose description FAILED ships the key carrying None, and that
        is kept as None rather than dropped: `build_attestation` refuses a record
        whose worker could not be described, exactly as it refuses one whose own
        process block is missing. A worker that ships no key AT ALL is a
        different state and is still passed over: it never reached the
        description step, which is what an older worker and a worker that
        returned early both look like.
        """
        if not isinstance(worker_output, dict):
            return
        shipped = worker_output.get("canopus_plugins")
        if isinstance(shipped, list):
            self.worker_plugins.append([str(name) for name in shipped])
        elif "canopus_plugins" in worker_output:
            self.worker_plugins.append(None)
        if not self.frozen:
            return
        for rel, count in (worker_output.get("canopus_deselected") or {}).items():
            counts = self.frozen.get(rel)
            if counts is not None:
                counts["deselected"] = max(counts["deselected"], int(count))

    def _apply_pending(self) -> None:
        """Fold buffered deselections into the tally, once there is one.

        Never lowers a count: merge_worker may already have folded a worker's
        larger figure in.
        """
        if not self.frozen:
            return
        for rel, count in self.pending_deselected.items():
            counts = self.frozen.get(rel)
            if counts is not None:
                counts["deselected"] = max(counts["deselected"], count)

    def deselected(self, items) -> None:
        """Count items filtered out of frozen test files.

        -k, -m, --lf and --deselect all route through pytest's deselection hook,
        which is why nothing here inspects an option, or whether one was given.

        BUFFERED, not written straight into the tally. pytest fires this hook
        from inside pytest_collection_modifyitems, which runs BEFORE
        pytest_collection_finish builds self.frozen — so an earlier revision's
        `if not self.frozen: return` guard dropped every deselection on the
        floor, and collect() then seeded a fresh all-zero tally over the top.
        Measured, not theorised: `pytest -k test_a` on a 3-test frozen file
        printed "2 deselected" and still attested "none deselected", in a plain
        run and under -n 2 alike. The entire -k / -m / --lf / --deselect
        detection axis was inert while its unit tests passed, because they call
        this method after seeding the tally by hand and so invert the real hook
        order.
        """
        for item in items:
            rel = self._rel(getattr(item, "path", ""))
            if rel is not None:
                self.pending_deselected[rel] = self.pending_deselected.get(rel, 0) + 1
        self._apply_pending()

    def report(self, report) -> None:
        """Tally one outcome, for frozen test files only."""
        if not self.frozen:
            return
        counts = self.frozen.get(self._rel(report.fspath))
        if counts is None:
            return
        if report.outcome == "failed":
            counts["failed"] += 1
        elif report.outcome == "skipped" and report.when in ("setup", "call"):
            counts["skipped"] += 1
        elif report.outcome == "passed" and report.when == "call":
            counts["passed"] += 1

    def _describe(self, config) -> dict | None:
        """The process description, or None when it could not be taken.

        NEVER RAISES, on either of `finish`'s two paths. The controller's path
        is obvious — a description that escaped would cost the record it was
        written to produce. The worker's path is the one that was left unguarded
        first: a raise there loses the deselection counts already assigned
        beside it, and those are the worker's only route home. Blast radius one
        worker rather than the run, and still not worth a bare call.

        One describer for both, rather than a plugin-name reader beside a full
        one. A second spelling of "what configured this interpreter" is how the
        controller and its workers come to answer the same question differently,
        which is precisely the difference the record exists to show.
        """
        try:
            return process_facts(config, self.root)
        except Exception as exc:  # noqa: BLE001 - description never breaks a run
            print(f"canopus: could not describe the process: {exc}", file=sys.stderr)
            return None

    def _tree(self):
        """The working tree's state, or None, never a raise.

        The same posture `_describe` takes and for the same reason: this runs
        inside a pytest hook, and a raise there takes the session's exit code
        with it. A failure is named on stderr and reported as None, which
        `build_attestation` refuses.
        """
        try:
            return tree_state(self.root)
        except Exception as exc:  # noqa: BLE001 - a raise here fails the session
            print(f"canopus: could not describe the tree this run ran against: "
                  f"{exc}", file=sys.stderr)
            return None

    def _dump_plugins(self, config) -> bool:
        """Write the plugin identities to CANOPUS_PLUGIN_DUMP. True when asked.

        The NORMALISED identities, never pytest's registration names. A raw dump
        is what makes a baseline machine-specific: a conftest plugin registers
        under its absolute path and an anonymous one under a memory address, so
        the captured set would diverge on the next clone and would carry an
        operator's home directory into a hash this public repository commits.

        True is returned whenever the variable was SET, not whenever the write
        succeeded, so a failed write never falls through into writing an
        attestation the freeze probe must not write. The failure is named on
        stderr rather than swallowed: `freeze` reads this file back, and a
        silently absent dump becomes a freeze with no plugin baseline, which
        attests nothing forever after.
        """
        target = os.environ.get("CANOPUS_PLUGIN_DUMP")
        if not target:
            return False
        described = self._describe(config)
        if described is None:
            # `_describe` has already named the failure. Writing an empty set
            # here would capture "this contract loaded no plugins", which is a
            # claim rather than a gap; leaving the file absent is what lets
            # `freeze` say the baseline could not be captured.
            return True
        try:
            atomic_write_text(
                Path(target), json.dumps(sorted(described["plugins"]), indent=2) + "\n"
            )
        except (OSError, ValueError) as exc:
            print(f"canopus: the plugin set could not be written to {target}: "
                  f"{exc}", file=sys.stderr)
        return True

    def finish(self, session, exitstatus) -> bool:
        """Write the record from the controller only. True when one was written.

        Under pytest-xdist every worker reaches session finish holding a partial
        tally and its own exit status, and the last writer would win. A worker is
        the only process carrying config.workerinput.

        CANOPUS_PLUGIN_DUMP is answered FIRST, before every other branch. It is
        how `freeze --contract` captures the baseline: the contract already runs
        through a real pytest child, so the set is taken from the recorder that
        computes it anyway rather than from a second session or a second
        describer. That child also sets CANOPUS_NO_ATTEST, and the tally is
        empty because no freeze is held yet, so any later branch would return
        before writing the dump.
        """
        if self._dump_plugins(session.config):
            return False
        if os.environ.get("CANOPUS_NO_ATTEST"):
            # The contract runner sets this in the child it spawns. `probe` can
            # run while a freeze is held, and a probe's partial tally must never
            # overwrite the record left by a real gate run.
            return False
        if self.frozen is None:
            return False
        if hasattr(session.config, "workerinput"):
            # A worker ships its deselection counts home instead of writing.
            output = getattr(session.config, "workeroutput", None)
            if output is not None:
                output["canopus_deselected"] = {
                    rel: counts["deselected"] for rel, counts in self.frozen.items()
                    if counts["deselected"]
                }
                # A worker is a separate interpreter, configured separately: it
                # reads its own PYTEST_ADDOPTS and its own ini, so the
                # controller's plugin list says nothing about what ran in here.
                # The whole description is taken and only the names are shipped,
                # deliberately: one describer answers for both processes (see
                # _describe), and the rest of a worker's description is the
                # controller's to hold.
                #
                # A FAILED description ships None rather than nothing, and the
                # two are not the same. `build_attestation` reads a missing
                # process block as damage and refuses the whole record; under
                # -n auto the workers are the interpreters that actually run the
                # frozen tests, so a worker nobody could describe is the same
                # damage in the place it matters most. While this shipped
                # nothing, `merge_worker` appended nothing, the controller's list
                # was simply one entry shorter, and the run attested with one
                # worker's interpreter unaccounted for.
                described = self._describe(session.config)
                output["canopus_plugins"] = (
                    sorted(described["plugins"]) if described is not None else None
                )
            return False
        process = self._describe(session.config)
        if process is not None:
            process["workers"] = sorted(self.worker_plugins, key=repr)
        write_attestation(self.root, build_attestation(
            root_digest=self.root_digest or "",
            frozen_tests=self.frozen,
            exit_status=int(exitstatus),
            attested_at=datetime.now(timezone.utc).isoformat(),
            baseline=self.baseline,
            process=process,
            plugin_baseline=self.plugin_baseline,
            tree_at_start=self.tree_at_start,
            tree_at_finish=self._tree(),
        ))
        return True
