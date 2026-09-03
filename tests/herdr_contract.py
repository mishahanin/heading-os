"""Check a herdr argv against the CAPTURED CLI grammar.

Consumed by the `herdr` stub in `tests/conftest.py`, which every test that runs
`yard-bootstrap.sh` uses instead of the operator's live server.

The stub used to exit 0 for anything, so it CONFIRMED EVERY SHAPE IT WAS GIVEN.
Three wrong shapes reached production behind that green suite, and each is now
caught here rather than in the operator's terminal:

    herdr worktree create fix-router     -> no positional accepted
    herdr worktree remove --workspace <branch-name>
                                         -> takes an ID; the shape is not
                                            checkable here, but the flag is
    result.workspace.cwd                 -> a key at no depth (checked in the
                                            bootstrap's own test, not here)

The grammar comes from `tests/fixtures/herdr-cli-contract.json`, generated from
`herdr <cmd> --help` by `scripts/dev/capture-herdr-contract.py`. It is never
hand-written, because a contract written by the caller is the caller agreeing
with itself.

The exit codes mirror what herdr 0.8.2 was MEASURED to do on 2026-09-03: an
unknown option, a missing value, or an unexpected positional all exit 2.

WHAT THIS DOES NOT CHECK, said plainly. It is a grammar, so it sees the SHAPE of
an argument and not its meaning: `--workspace fix-router` passes here, because
whether a string is a live workspace ID is a question only the server can
answer. It also cannot see anything about the response, and a command absent
from the capture is refused rather than guessed at, which is deliberate.
"""
from __future__ import annotations

import json
from pathlib import Path

CONTRACT = Path(__file__).resolve().parent / "fixtures" / "herdr-cli-contract.json"


def load() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def check(argv: list[str], contract: dict | None = None) -> str | None:
    """None when `argv` satisfies the captured grammar, else why it does not."""
    commands = (contract or load())["commands"]

    if not argv:
        return "missing subcommand"

    # Longest match first: `plugin config-dir` before `plugin`.
    name = next((n for n in sorted(commands, key=len, reverse=True)
                 if argv[:len(n.split())] == n.split()), None)
    if name is None:
        return (f"unknown command: {' '.join(argv[:2])!r} is not in the "
                f"captured herdr contract. If the engine really calls it, add "
                f"it to COMMANDS in scripts/dev/capture-herdr-contract.py and "
                f"re-capture -- do not widen this check.")

    spec = commands[name]
    rest = argv[len(name.split()):]

    positionals = 0
    index = 0
    while index < len(rest):
        token = rest[index]
        if token.startswith("--"):
            flag, _, inline = token.partition("=")
            if flag not in spec["options"]:
                return f"unknown option: {flag}"
            if spec["options"][flag] and not inline:
                if index + 1 >= len(rest):
                    return f"missing value for {flag}"
                index += 1
        else:
            positionals += 1
        index += 1

    low = spec["positionals"]["min"]
    high = spec["positionals"]["max"]
    if positionals < low:
        return (f"{name}: expected at least {low} positional argument(s), "
                f"got {positionals}")
    if high is not None and positionals > high:
        return (f"{name}: takes at most {high} positional argument(s), got "
                f"{positionals}: {[t for t in rest if not t.startswith('--')]}")
    return None
