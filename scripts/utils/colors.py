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

    `.claude/hooks/checkpoint-statusline.py` carries an older private copy of
    this logic. It is not folded in here yet, because the status line is a
    different, already-proven surface and rewriting a working one to prove an
    unproven one is the wrong order.
    """
    if os.name != "nt":
        return True
    for var in ("WT_SESSION", "TERM_PROGRAM", "ANSICON", "ConEmuANSI"):
        if os.environ.get(var):
            return True
    term = os.environ.get("TERM", "")
    return bool(term) and term != "dumb"
