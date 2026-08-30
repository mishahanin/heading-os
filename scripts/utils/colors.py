"""ANSI terminal color constants."""

import os

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
GRAY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


def supports_ansi() -> bool:
    """Whether the surrounding terminal renders escape codes.

    Deliberately NOT an `isatty()` check. The callers that need this write to a
    pipe rather than to a terminal - a Claude Code hook emits JSON on stdout and
    the TUI renders it - so `isatty()` answers False on exactly the surface where
    colour does work. The environment is the honest signal instead.

    `TERM=dumb` is honoured on EVERY platform. It used to be read only inside
    the `os.name == "nt"` branch, i.e. only where `TERM` is almost never set,
    and ignored on POSIX, where `TERM=dumb` is the one place it actually occurs:
    cron, an Emacs shell buffer, a minimal CI container, `env -i`. MEASURED
    2026-08-30 on Linux, `TERM=dumb` returned True, so a caller gating on this
    still emitted raw escape sequences into a log. A docstring arguing that the
    environment is the honest signal, over a branch that never reads the
    environment, is the contradiction; reading the one variable that means "do
    not" is what makes the sentence true.

    Everything else on POSIX stays unconditional, and that IS deliberate - see
    the paragraph above. The Claude Code TUI sets `TERM` to `xterm-256color` or
    similar, so it is unaffected.

    `.claude/hooks/checkpoint-statusline.py` carries an older private copy of
    this logic. It is not folded in here yet, because the status line is a
    different, already-proven surface and rewriting a working one to prove an
    unproven one is the wrong order.
    """
    if os.environ.get("TERM") == "dumb":
        return False
    if os.name != "nt":
        return True
    for var in ("WT_SESSION", "TERM_PROGRAM", "ANSICON", "ConEmuANSI"):
        if os.environ.get(var):
            return True
    return bool(os.environ.get("TERM", ""))
