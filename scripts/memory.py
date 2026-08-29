#!/usr/bin/env python3
"""memory.py - one console-first entry point over the six memory operations.

A thin facade: each subcommand shells out to the existing memory script with zero
behavior change and returns its exit code. It adds discovery, not logic. See the
lifecycle map (docs/memory-lifecycle.md) for how the underlying stores relate.

Subcommands:
    status                      read-only overview (index stats + knowledge + count)
    recall "<text>" [args]      semantic query over the memory index
    promote --note <path> ...   promote a knowledge note to corporate
    retire <name> [<name> ...]  all-store retire (the delete that sticks)
    reconcile [--quiet]         sync the native harness store with canonical (CLI mode)
    hygiene [args]              run the objective-defect detector

Usage:
    python scripts/memory.py status
    python scripts/memory.py recall "sovereign deep packet"
    python scripts/memory.py retire feedback_foo.md
"""
import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, RESET  # noqa: E402
from scripts.utils.workspace import get_data_root, get_workspace_root  # noqa: E402

ROOT = get_workspace_root()
PY = sys.executable
RECONCILE_HOOK = ".claude/hooks/memory-reconcile.py"


def _run(rel_script: str, *args: str) -> int:
    """Shell out to a workspace script and return its exit code.

    No env override, so the process-tree trace id (X31C_TRACE_ID) is inherited.
    Console-first: a missing backing script exits non-zero with a plain message
    rather than a traceback.
    """
    script = ROOT / rel_script
    if not script.exists():
        print(f"memory: backing script not found: {rel_script}", file=sys.stderr)
        return 3
    return subprocess.run([PY, str(script), *args]).returncode


def cmd_status(_args: argparse.Namespace) -> int:
    """Read-only, responsive overview: index stats, knowledge health, auto-memory count.

    The hygiene detector is intentionally NOT run here: it compiles the ODIN brain and
    takes minutes, which would defeat a quick `status`. Run it on demand with
    `memory.py hygiene`.
    """
    print(f"{BOLD}Memory status{RESET}")
    print(f"{CYAN}semantic index{RESET}")
    _run("scripts/memory-index.py", "stats")
    print(f"{CYAN}knowledge base{RESET}")
    _run("scripts/knowledge-health.py")
    am = get_data_root() / "auto-memory"
    n = len(list(am.glob("*.md"))) if am.exists() else 0
    print(f"{CYAN}auto-memory{RESET} {GRAY}files:{RESET} {n}")
    print(f"{GRAY}(run `memory.py hygiene` for the defect detector){RESET}")
    return 0


def cmd_recall(args: argparse.Namespace) -> int:
    return _run("scripts/memory-index.py", "query", args.text, *args.extras)


def cmd_promote(args: argparse.Namespace) -> int:
    return _run("scripts/promote-knowledge.py", *args.extras)


def cmd_retire(args: argparse.Namespace) -> int:
    return _run("scripts/retire-memory.py", *args.names)


def cmd_reconcile(args: argparse.Namespace) -> int:
    """Sync the native harness store with canonical auto-memory in CLI mode.

    Reuses the hook's own native-dir resolver so a bare (hook-stdin) call, which
    no-ops on empty stdin, is never used.
    """
    hook = ROOT / RECONCILE_HOOK
    if not hook.exists():
        print(f"memory: reconcile hook not found: {RECONCILE_HOOK}", file=sys.stderr)
        return 3
    spec = importlib.util.spec_from_file_location("memory_reconcile_hook", hook)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    native = mod._native_from_hook({"cwd": str(ROOT)})
    if native is None:
        print("memory: could not resolve the native harness memory dir", file=sys.stderr)
        return 3
    canonical = get_data_root() / "auto-memory"
    extra = ["--quiet"] if args.quiet else []
    return _run(RECONCILE_HOOK, "--native", str(native), "--canonical", str(canonical), *extra)


def cmd_hygiene(args: argparse.Namespace) -> int:
    return _run("scripts/memory-hygiene.py", *args.extras)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="memory.py",
        description="One console-first entry point over the six memory operations.",
    )
    # An unrecognised flag is REFUSED unless the subcommand opts in below. This
    # facade runs on `parse_known_args`, which silently pockets anything it does
    # not know, and three subcommands never read that pocket: `retire --dry-run
    # feedback_foo.md` dropped the flag and performed a REAL all-store retire,
    # and `reconcile --queit` ran loud with no complaint. `retire-memory.py` has
    # no `--dry-run` at all, so the operator's expectation was wrong twice over.
    # The default is the safe one, so a subcommand added later refuses until its
    # author decides otherwise.
    p.set_defaults(passthrough=False)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="read-only overview").set_defaults(func=cmd_status)

    sp = sub.add_parser("recall", help="semantic query over the memory index")
    sp.add_argument("text", help="query text")
    sp.set_defaults(func=cmd_recall, passthrough=True)

    sub.add_parser("promote", help="promote a knowledge note to corporate (passthrough --note PATH --type TYPE)").set_defaults(func=cmd_promote, passthrough=True)

    sp = sub.add_parser("retire", help="all-store retire by name")
    sp.add_argument("names", nargs="+", help="memory file name(s), e.g. feedback_foo.md")
    sp.set_defaults(func=cmd_retire)

    sp = sub.add_parser("reconcile", help="sync native harness store with canonical")
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_reconcile)

    sub.add_parser("hygiene", help="run the objective-defect detector (passthrough --json / --no-report)").set_defaults(func=cmd_hygiene, passthrough=True)

    return p


def leading_flag_error(argv: list[str]) -> str | None:
    """Refuse `recall --flag VALUE "text"`, or None when the argv is safe.

    `parse_known_args` does not put an unknown optional's operand back in
    `extras`: it binds that operand to the positional. Measured on CPython
    3.11.15:

        recall --top-k 3 "sovereign deep packet"
          -> text='3', extras=['--top-k', 'sovereign deep packet']

    so `cmd_recall` handed the backing script `query 3 --top-k "sovereign deep
    packet"` -- the flag's value searched as the query, and the query passed as
    the flag's value. With an int-typed flag the child dies at exit 2 and the
    operator at least learns something is wrong. With a string-typed one it is
    silent: `recall --layer odin "my query"` becomes `query odin --layer "my
    query"`, which parses cleanly and searches for the literal word "odin"
    inside a layer named after the query.

    Reordering for the operator would mean guessing which unknown flags take a
    value, which this facade cannot know -- it does not own the backing
    script's parser. So it refuses and says what to type. The documented order
    (module docstring) has always been text first.
    """
    if len(argv) < 2 or argv[0] != "recall":
        return None
    first = argv[1]
    if not first.startswith("-") or first in ("-h", "--help"):
        return None
    return (
        f"memory.py recall: put the query text FIRST, before {first}.\n"
        f"  wrong: memory.py recall {first} ... \"your query\"\n"
        f"  right: memory.py recall \"your query\" {first} ...\n"
        "A leading flag binds its own value to the query, silently."
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    problem = leading_flag_error(argv)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    # parse_known_args so passthrough flags (recall --top-k, promote --note,
    # hygiene --json) reach the backing script instead of erroring on a leading
    # unknown optional (argparse REMAINDER does not capture a leading flag).
    args, extras = build_parser().parse_known_args(argv)
    if extras and not args.passthrough:
        print(f"memory.py {args.command}: unrecognised argument(s): "
              f"{' '.join(extras)}\n"
              f"This subcommand passes nothing through, so the flag would have "
              f"been DISCARDED and the command run without it.",
              file=sys.stderr)
        return 2
    args.extras = extras
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
