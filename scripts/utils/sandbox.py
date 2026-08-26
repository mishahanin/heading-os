#!/usr/bin/env python3
"""Run a model-written traversal program in a box it cannot get out of.

This is the compensating control that makes `/census` acceptable at all. The
workspace's global rule forbids executing generated code on input; the carve-out
and the conditions that void it are written in
`.claude/rules/generated-code-execution.md`. This module IS the control that
carve-out names, so a change here changes what the exception is worth.

What the box gives, and what each part is actually for:

  --unshare-all      its own empty network namespace, plus PID, IPC and mount.
                     Nothing inside can reach the network, so nothing inside can
                     exfiltrate what it read. This is also why no model call
                     happens in here: a sub-call would need a channel out, and
                     building that channel is the machinery this design rejected.
  --clearenv         the child's environment is empty but for one explicit PATH.
                     API keys loaded into the parent's environment do not exist
                     inside, so there is nothing to steal even if the traversal
                     program were written to steal it.
  --ro-bind          the corpus is visible and unwritable. A traversal cannot
                     plant a file that outlives its own run.
  --bind <out_dir>   the ONLY writable path, and it is outside the corpus.
  --die-with-parent  the parent dying takes the box with it; nothing is left
                     running that nobody is watching.

Everything not named in a bind does not exist inside the box. Not "forbidden to
read" - absent from the tree.

Two refusals happen BEFORE any process starts, because a control that runs after
the program does is not a control:

  1. No `bwrap` on PATH refuses the run. There is deliberately no unsandboxed
     fallback: a soft degradation here would silently convert the whole design
     into "an agent runs generated Python next to .env with the network up",
     which is the configuration the design exists to refuse.
  2. An air-gapped corpus path refuses the run, via `scripts/utils/air_gap.py`.
     The CEO-private thread branch and any `_secure/` prefix are never mounted,
     whatever the caller asked for.

Verified on this machine 2026-08-13 (WSL2, kernel 6.18.33.2, bubblewrap 0.9.0,
user namespaces permitted): network unreachable, `.env` absent, corpus write
refused with OSError, environment carrying only PATH and PWD.

Usage:
    from scripts.utils.sandbox import run_sandboxed
    result = run_sandboxed(program=Path("t.py"), corpus_paths=[Path("threads")],
                           out_dir=Path("/tmp/out"), timeout_s=120)
    if result.refused:
        ...   # never ran; result.refused says why
"""
from __future__ import annotations

import shutil
import subprocess  # nosec B404 - fixed argv, shell=False, see run_sandboxed
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.air_gap import is_denied  # noqa: E402

BWRAP = "bwrap"

# Read-only system paths the child needs to be able to run python3 at all.
# /lib and /lib64 are symlinks into /usr on a merged-usr system; binding them
# anyway costs nothing and keeps this working on a split-usr host.
SYSTEM_ROBINDS = ("/usr", "/bin", "/lib", "/lib64")

CORPUS_MOUNT = "/data"
PROGRAM_MOUNT = "/traverse.py"
OUT_MOUNT = "/out"
INTERPRETER = "/usr/bin/python3"

DEFAULT_TIMEOUT_S = 120


@dataclass(frozen=True)
class SandboxResult:
    """One sandboxed run, and whether it ran at all.

    `refused` is the field to read first. When it is set the program NEVER
    started, and `exit_code` is None: a caller that treats a refusal as a failed
    traversal reports the wrong cause to the operator, and a caller that treats
    it as an empty result reports success over nothing.
    """

    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    refused: str | None = None
    timed_out: bool = False
    mounts: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.refused is None and not self.timed_out and self.exit_code == 0


def sandbox_available() -> bool:
    """Whether the hard dependency is present. There is no soft path."""
    return shutil.which(BWRAP) is not None


def air_gap_reason(path: Path) -> str | None:
    """Why this corpus path may never be mounted, or None.

    `is_denied` takes a workspace-relative path: its `_secure/` rule is a prefix
    match, which an absolute path can never satisfy, while its `personal` rule
    is a segment match, which an absolute path satisfies fine. So both forms are
    checked - the absolute one catches the segment wherever it sits, and the
    root-relative one catches the prefix. Checking only one of them would leave
    exactly one of the two air-gap rules unenforced.
    """
    resolved = path.resolve()
    # The absolute form is checked with `_secure` added as a SEGMENT rule. The
    # shared predicate spells `_secure/` as a prefix, which no absolute path can
    # satisfy, so without this the vault rule would depend entirely on the data
    # root resolving - and a helper that quietly drops one of two air-gap rules
    # when an unrelated import fails is not a guard.
    if is_denied(str(resolved).lstrip("/"), deny_segments=("_secure",)):
        return (f"air-gapped path refused: {path}; this branch is never mounted")
    for root in _known_roots():
        try:
            candidate = resolved.relative_to(root).as_posix()
        except ValueError:
            continue
        if is_denied(candidate):
            return (f"air-gapped path refused: {path} "
                    f"(matched as {candidate!r}); this branch is never mounted")
    return None


def denied_descendants(path: Path) -> list[Path]:
    """Air-gapped directories sitting INSIDE a requested corpus path.

    Checking only the path the caller named is not enough, and the gap this
    leaves is not theoretical: the CEO-private thread branch is a child of the
    threads directory, so `--corpus <data>/threads` would mount it read-only and
    the traversal would read every private note in it. The named path passes the
    predicate; its child does not.

    Directories only, and denied ones are not descended into - the check costs
    one stat per directory, and a denied subtree has nothing further to say.
    """
    if not path.is_dir():
        return []
    denied: list[Path] = []
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            children = [c for c in current.iterdir() if c.is_dir()]
        except OSError:
            continue
        for child in children:
            if is_denied(child.name) or air_gap_reason(child):
                denied.append(child)
                continue
            stack.append(child)
    return denied


def _known_roots() -> list[Path]:
    """Engine root and data root, when each can be resolved.

    This is redundancy, not the guard: both air-gap rules are enforced on the
    absolute form above, so an unresolvable data root costs a second spelling of
    a check that already ran. It is still reported rather than swallowed - a
    handler that neither logs nor re-raises is exactly how a guard degrades
    without anyone noticing.
    """
    roots: list[Path] = [Path(__file__).resolve().parent.parent.parent]
    try:
        from scripts.utils.workspace import get_data_root
        roots.append(Path(get_data_root()).resolve())
    except (ImportError, OSError, ValueError) as exc:
        print(f"sandbox: data root unresolved ({exc}); air-gap still enforced on "
              "the absolute path form", file=sys.stderr)
    return roots


def _mount_names(corpus_paths: list[Path],
                 preferred: dict[Path, str] | None = None) -> dict[Path, str]:
    """One mount name per corpus path, unique even when names collide.

    Two scopes named `contacts` would otherwise mount over each other and the
    traversal would silently see one corpus where the operator asked for two -
    an undercount that looks exactly like a correct answer.

    `preferred` lets the caller mount under the scope name the OPERATOR typed
    rather than the directory's basename. Without it, `--corpus threads`
    resolves to `<data>/threads/business` and lands at `/data/business`, so a
    traversal written against `/data/threads` reads an empty tree and returns
    zero - a wrong answer with no error anywhere.
    """
    names: dict[Path, str] = {}
    used: set[str] = set()
    preferred = preferred or {}
    for path in corpus_paths:
        base = preferred.get(path) or path.resolve().name or "corpus"
        name = base
        n = 2
        while name in used:
            name = f"{base}-{n}"
            n += 1
        used.add(name)
        names[path] = name
    return names


def build_argv(*, program: Path, corpus_paths: list[Path], out_dir: Path,
               mount_names: dict[Path, str]) -> list[str]:
    """The exact command line verified on 2026-08-13, as a list, never a string."""
    argv = [BWRAP]
    for system_path in SYSTEM_ROBINDS:
        if Path(system_path).exists():
            argv += ["--ro-bind", system_path, system_path]
    for path in corpus_paths:
        mount = f"{CORPUS_MOUNT}/{mount_names[path]}"
        argv += ["--ro-bind", str(path.resolve()), mount]
        # Order matters: bwrap applies operations in sequence, so a tmpfs laid
        # over an air-gapped child AFTER the read-only bind replaces that
        # subtree with an empty directory inside the box. The private branch is
        # not "denied", it is not there.
        for denied in denied_descendants(path):
            rel = denied.resolve().relative_to(path.resolve()).as_posix()
            argv += ["--tmpfs", f"{mount}/{rel}"]
    argv += ["--ro-bind", str(program.resolve()), PROGRAM_MOUNT]
    argv += ["--bind", str(out_dir.resolve()), OUT_MOUNT]
    # The `/tmp` below is a mount point in the box's OWN namespace, an empty
    # tmpfs the traversal may scribble in. It names no host path: WITHOUT the
    # line the child inherits the host `/tmp`, which is the thing both linters'
    # hardcoded-temp rule exists to prevent. Hence the two suppressions on it -
    # S108 is ruff's code for the literal, B108 is bandit's.
    argv += ["--proc", "/proc", "--dev", "/dev"]
    argv += ["--tmpfs", "/tmp"]  # noqa: S108  # nosec B108
    argv += ["--unshare-all", "--clearenv", "--setenv", "PATH", "/usr/bin",
             "--die-with-parent",
             # A new session detaches the controlling terminal. Without it the
             # box inherits the parent's tty, and on any host with
             # `dev.tty.legacy_tiocsti=1` a traversal can push characters into
             # the operator's terminal with TIOCSTI - keystroke injection from
             # inside the thing that is supposed to contain it. Measured 0 on
             # this machine (kernel 6.18), but the engine ships to a fleet.
             "--new-session"]
    argv += [INTERPRETER, PROGRAM_MOUNT]
    return argv


def run_sandboxed(*, program: Path, corpus_paths: list[Path], out_dir: Path,
                  timeout_s: int = DEFAULT_TIMEOUT_S,
                  mount_names: dict[Path, str] | None = None) -> SandboxResult:
    """Run `program` over `corpus_paths`, writing only into `out_dir`.

    Every refusal below happens before a process exists. That ordering is the
    point: a check that runs after the traversal has already read the corpus is
    a report, not a control.

    Among those refusals the order is also load-bearing, and it runs
    specific-before-generic: what the caller ASKED FOR is judged first (program,
    corpus, air-gap, existence, output placement), and only then whether this
    HOST can run it (bubblewrap, interpreter). A generic refusal that fires
    first does not merely reorder the message, it conceals the request.
    """
    if not program.is_file():
        return SandboxResult(None, refused=f"traversal program not found: {program}")

    if not corpus_paths:
        return SandboxResult(None, refused="no corpus path given: nothing to traverse")

    # Air-gap first, existence second, and the order is load-bearing. Checking
    # existence first makes a non-existent air-gapped path refuse with the WRONG
    # reason, which reads as an ordinary typo in a log and hides that someone
    # asked for the private branch. It also means the guard decides before the
    # filesystem is touched at all.
    for path in corpus_paths:
        reason = air_gap_reason(path)
        if reason:
            return SandboxResult(None, refused=reason)
    for path in corpus_paths:
        if not path.exists():
            return SandboxResult(None, refused=f"corpus path does not exist: {path}")

    # The one writable mount is only a control while it lies OUTSIDE the corpus.
    # An `out_dir` under a corpus path re-binds that subtree read-write and
    # defeats the read-only mount on the host - voiding condition #2 of
    # `.claude/rules/generated-code-execution.md`. `census.py` passes a temp
    # directory and was never at risk; this closes the gap in the reusable
    # control, which is what a future second caller would inherit.
    reason = air_gap_reason(out_dir)
    if reason:
        return SandboxResult(None, refused=f"output directory: {reason}")
    resolved_out = out_dir.resolve()
    for path in corpus_paths:
        corpus = path.resolve()
        if resolved_out == corpus or corpus in resolved_out.parents:
            return SandboxResult(None, refused=(
                f"output directory {out_dir} lies inside the corpus path {path}; "
                "the writable mount would re-bind read-only corpus as writable"))
        # The other direction, and it was missing. An `out_dir` that CONTAINS a
        # corpus path is mounted read-write at `/out`, and the corpus then sits
        # under it as a writable subtree. A traversal writing `/out/<name>/x.md`
        # reaches the host corpus through the writable mount without ever
        # touching the read-only one. Measured 2026-08-26 with bwrap 0.9.0: the
        # box reported exit 0, and afterwards the host file `corpus/note.md` had
        # been overwritten and `corpus/planted.md` created. The refusal above
        # was written for exactly this hazard and covered one nesting order out
        # of two, which reads as a closed control while half of it is open.
        if resolved_out in corpus.parents:
            return SandboxResult(None, refused=(
                f"output directory {out_dir} contains the corpus path {path}; "
                "the writable mount would expose read-only corpus for writing"))

    # Host readiness comes LAST, after every judgement about what the caller
    # asked for. Both orderings refuse, and no process starts either way, so on
    # a machine with bubblewrap the difference is invisible - which is exactly
    # why it survived. On a machine without it, every argument refusal above
    # came back as "bwrap is not on PATH", so a request for the CEO-private
    # branch read as a missing tool. That is the same defect the air-gap /
    # existence ordering above was written to prevent, one level out: a refusal
    # naming the wrong reason hides what was actually asked for. CI has no
    # bubblewrap, which is where it finally showed.
    #
    # This is a message-precedence change, not a fallback. `bwrap` absent
    # remains a hard refusal before any process exists, so condition #1 of
    # `.claude/rules/generated-code-execution.md` is untouched.
    if not sandbox_available():
        return SandboxResult(
            None, refused=(
                f"{BWRAP} is not on PATH: /census does not run without its "
                "sandbox, and there is no unsandboxed fallback by design "
                "(install bubblewrap)"))

    if not Path(INTERPRETER).exists():
        return SandboxResult(None, refused=(
            f"{INTERPRETER} is absent on this host, so the box has nothing to run "
            "the traversal with; without this check the run reports 'exited 127', "
            "which reads as a broken traversal rather than a missing interpreter"))

    out_dir.mkdir(parents=True, exist_ok=True)
    names = _mount_names(list(corpus_paths), mount_names)
    argv = build_argv(program=program, corpus_paths=list(corpus_paths),
                      out_dir=out_dir, mount_names=names)
    mounts = {f"{CORPUS_MOUNT}/{n}": str(p.resolve()) for p, n in names.items()}

    try:
        proc = subprocess.run(  # nosec B603 - fixed argv built above, shell=False
            argv, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return SandboxResult(
            None, timed_out=True, mounts=mounts,
            refused=(f"traversal did not finish within {timeout_s}s and was "
                     "killed; a partial result is not reported as an answer"))
    except OSError as exc:
        return SandboxResult(None, refused=f"could not start the sandbox: {exc}")

    return SandboxResult(proc.returncode, proc.stdout, proc.stderr, mounts=mounts)
