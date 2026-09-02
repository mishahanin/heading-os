"""Cross-platform terminal launcher for the bridge daemon.

Windows uses Windows Terminal's -w (window-name) flag: creates the
window if absent, attaches a new tab if present.

macOS creates a DETACHED tmux session on a named session (skipping the
create when `tmux has-session` says it is already there), then osascript
to focus Terminal.app on that session. This said "tmux's -A
(attach-if-exists, create-if-not)" until 2026-08-24; `-A` attaches, which
a daemon with no controlling terminal cannot do, so the flag is gone and
the has-session check carries the idempotency.

Linux uses the same tmux pattern as macOS, then a detected terminal
emulator to open a GUI window attached to the session. `find_linux_terminal`
returns the FIRST match in `_LINUX_TERMINAL_CANDIDATES`, so that tuple's order
is the precedence, and this line now repeats it: x-terminal-emulator (the
Debian alternatives wrapper, i.e. the user's own default) / gnome-terminal /
konsole / alacritty / kitty / xterm. It used to list the same six in a
different order, putting xterm third and the wrapper fourth - the inverse of
the deliberate choice the tuple's own comment explains, and exactly the kind of
line a maintainer reorders the CODE to match.
On headless Linux (no DISPLAY/WAYLAND_DISPLAY), the tmux session is
spawned but no GUI attach is attempted - the caller can attach later
via `tmux attach -t 31c-<slug>`.

All paths inject `BRIDGE_ORIGIN=browser` so the Stop hook's
origin-gated prompt knows the session was launched from the dashboard
(spec section 3.2). They also inject `BRIDGE_CONTEXT` as a JSON
string when the /launch caller supplies a context dict - skills like
/email-respond use this to pre-populate (conv_id, subject, etc.)
instead of asking the user to retype them.

Tests: tests/test_an_allowlist_that_admitted_a_flag.py
"""
import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

# Allowlists for user-controllable inputs. Each pattern is the most-restrictive
# that still accepts every legitimate value. Defense-in-depth: validation here
# protects every code path that builds wt.exe / tmux commands, including the
# registry fallback in Task 14's /launch endpoint and any future internal caller.
# The FIRST character must be alphanumeric. `{session_id}` is interpolated bare
# straight after `claude --resume`, and a session id never begins with `-`, so
# the old pattern was not "the most-restrictive that still accepts every
# legitimate value" the comment above promises: `--dangerously-skip-permissions`
# is 30 characters of `[A-Za-z0-9_-]`, passed validation, and arrived on the
# command line as a FLAG TO CLAUDE rather than as a resume target. Every other
# token in those command strings is prefixed, env-assignment-bound, base64 or
# metacharacter-stripped; this was the one uncontrolled argument-position value,
# and the dashboard's /launch path supplies it.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_USER_SLUG_RE = re.compile(r"^[a-z0-9-]{1,32}$")
_ACTION_RE = re.compile(r"^[a-z0-9-]{1,40}$")


class TerminalUnavailable(RuntimeError):
    pass

def assert_wt_available() -> None:
    if not shutil.which("wt.exe"):
        raise TerminalUnavailable(
            "wt.exe not found. Install Windows Terminal 1.16+ from the Microsoft Store."
        )


def assert_tmux_available() -> None:
    if not shutil.which("tmux"):
        raise TerminalUnavailable(
            "tmux not found. Install via 'brew install tmux' (macOS) or "
            "'apt install tmux' / 'dnf install tmux' (Linux) before launching from the bridge."
        )


_LINUX_TERMINAL_CANDIDATES = (
    "x-terminal-emulator",  # Debian/Ubuntu alternatives wrapper - points at the user's default
    "gnome-terminal",
    "konsole",
    "alacritty",
    "kitty",
    "xterm",
)


def find_linux_terminal() -> str | None:
    """Locate a Linux terminal emulator. Returns a path, or None when none of
    the candidates is installed.

    NOT a headless check, which the previous sentence ("None on headless")
    claimed. This function reads nothing but `shutil.which`: on a headless host
    with xterm installed it returns `/usr/bin/xterm`, and on a full desktop with
    none of the six candidates installed it returns None. Whether a GUI session
    exists is `_is_linux_gui_session()`'s question, asked by `spawn_or_focus`
    before this is reached. A maintainer trusting the old sentence could drop
    that caller-side gate as redundant and reintroduce GUI-attach attempts on
    headless hosts.
    """
    for name in _LINUX_TERMINAL_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def _is_linux_gui_session() -> bool:
    """True iff a Linux X11 or Wayland session appears to be available."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _resolved_terminal_name(terminal_exe: str) -> str:
    """The basename of what `terminal_exe` actually points at.

    A `.wrapper` suffix is kept: `gnome-terminal.wrapper` is the legacy-flag
    shim and DOES accept `-e`, while `gnome-terminal` does not, so the two must
    not collapse to one name.

    No try/except: `Path.resolve()` is non-strict by default since 3.6, so a
    path that does not exist comes back unchanged rather than raising, and
    `/no/such/kitty` still yields `kitty`. A guard here would be a handler for
    a case that cannot occur, which reads as protection and tests as nothing.
    """
    return Path(terminal_exe).resolve().name


def build_linux_attach_command(terminal_exe: str, user_slug: str) -> list[str]:
    """Build the command to open a terminal window attached to the named tmux session.

    Different Linux terminals use different flags to "run this command in a new window":
      - gnome-terminal: `gnome-terminal -- <cmd>`  (newer versions; `-e` deprecated)
      - konsole:        `konsole -e <cmd>`
      - alacritty:      `alacritty -e <cmd>`
      - kitty:          `kitty <cmd>`               (no flag needed; positional)
      - xterm:          `xterm -e <cmd>`
      - x-terminal-emulator (Debian wrapper): `-e <cmd>` (semantics depend on default)

    `user_slug` is allowlist-validated here and not only upstream. The module
    header claims validation "protects every code path that builds wt.exe /
    tmux commands, including ... any future internal caller"; this exported
    builder was the one that did not, so the claim was false for it.
    """
    _validate_inputs(user_slug, "noop", None)
    target = f"31c-{user_slug}"
    # Resolve first, then dispatch on the REAL binary. `x-terminal-emulator` is
    # a Debian alternatives symlink and carries no flag semantics of its own:
    # dispatching on that name always fell through to `-e`, which is right for
    # the gnome-terminal WRAPPER and wrong for gnome-terminal itself. On this
    # machine the alternative points at zutty, which takes `-e`; on a default
    # GNOME desktop it can point straight at the binary that rejects it.
    name = _resolved_terminal_name(terminal_exe)
    inner = ["tmux", "attach", "-t", target]
    if name == "gnome-terminal":
        return [terminal_exe, "--", *inner]
    if name == "kitty":
        return [terminal_exe, *inner]
    # konsole, alacritty, xterm, x-terminal-emulator all accept -e
    return [terminal_exe, "-e", *inner]

def _validate_inputs(user_slug: str, action: str, session_id: str | None) -> None:
    if not _USER_SLUG_RE.match(user_slug):
        raise ValueError(f"invalid user_slug: {user_slug!r}")
    if not _ACTION_RE.match(action):
        raise ValueError(f"invalid action: {action!r}")
    if session_id is not None and not _SESSION_ID_RE.match(session_id):
        raise ValueError(f"invalid session_id: {session_id!r}")


def _safe_title(title: str) -> str:
    return "".join(c for c in title if c.isprintable() and c not in '"\r\n')[:80]


# Initial-prompt construction per action+context. Sent as the positional
# arg to `claude` so the terminal session opens with the right context
# already loaded - not a blank terminal that requires the user to
# remember which skill to invoke and which id to paste.
#
# EVERY value interpolated into the prompt passes _safe_for_shell_arg, so the
# whole string is free of the characters cmd.exe and POSIX shells act on. It is
# NOT restricted to ASCII: a Cyrillic or Hebrew email subject reaches the prompt
# intact, because mojibake under a legacy Windows codepage is a display problem
# and dropping the subject entirely is a real regression for a bilingual
# operator. The comment here used to promise "ASCII-safe chars without double
# quotes or shell metacharacters" while the code shipped `%`, `&`, `|`, `<`,
# `>`, `^`, `!` and every non-ASCII printable, and applied nothing at all to the
# operator's own name.

_CONV_ID_RE = re.compile(r"^[A-Za-z0-9_=/+\-]{1,256}$")

# Characters that are live to cmd.exe or to a POSIX shell. `"` closes the
# `cmd /k "..."` region outright; `%` is expanded by cmd.exe INSIDE that region,
# so a subject reading `%SOMEVAR%` is substituted before `claude` sees it, and a
# value carrying a quote reopens the breakout through a field that was
# "sanitized". The rest (`^ ! & | < > ( ) $ backtick`) are inert while the
# quoting holds and become live the instant it does not, which is exactly when
# it matters. Cheap to drop, so all of them go.
_SHELL_METACHARS = frozenset('"%^!&|<>()$`')


def _safe_for_shell_arg(s: str) -> str:
    """Drop every character a shell would act on, plus control characters.

    Keeps ordinary printable text INCLUDING non-ASCII. Strips surrounding
    whitespace and caps at 200 chars.
    """
    if not isinstance(s, str):
        return ""
    out = "".join(
        c for c in s
        if c.isprintable() and c not in _SHELL_METACHARS and c not in "\r\n\t"
    )
    return out.strip()[:200]


def _build_initial_prompt(action: str, context: dict | None) -> str:
    """Return a short shell-safe initial prompt for `claude`, or empty
    string if we have no useful context for this action.

    Shell-safe, NOT ASCII-safe. This line and the comment below both said
    ASCII until 2026-08-24, contradicting `_safe_for_shell_arg` twenty lines
    up, which deliberately keeps non-ASCII printables so a Cyrillic or Hebrew
    subject survives to the prompt. Anyone reading only these two would have
    "restored" an ASCII filter that was removed on purpose.

    Per-action prompt templates:
    - email-respond: load the conversation from _latest-fetch.json and
      help draft a reply. conv_id from context.
    - other actions: empty (terminal opens with bare claude)
    """
    if not context:
        return ""
    if action == "email-respond":
        conv_id = context.get("conv_id", "")
        subject = context.get("subject", "")
        if not (isinstance(conv_id, str) and _CONV_ID_RE.match(conv_id)):
            return ""
        safe_subject = _safe_for_shell_arg(subject)
        # Imperative prompt, no embedded quotes; any script, see above. Tells Claude
        # exactly what to do without depending on the /email-respond
        # skill's existing 'paste here' contract.
        parts = [
            f"I want to respond to email conversation {conv_id}",
        ]
        if safe_subject:
            parts.append(f"(subject: {safe_subject})")
        # Template the whole voice clause off the operator seam: the name AND the
        # voice-reference path are personalized, resolved from operator.yaml / env
        # (generic "Operator" / "reference/voice.md" on a fresh clone).
        from scripts.utils.operator_identity import get_operator
        op = get_operator()
        # Sanitized like every other interpolated value. operator.yaml is the
        # operator's own file, not a remote input, but it lands in the same
        # `cmd /k "..."` string: a single `"` in the name closes the quoted
        # region and everything after it runs as a cmd.exe command. Measured
        # 2026-08-24 with a name of `A" & whoami > C:\pwn.txt & "` - the
        # breakout was present verbatim in the built command.
        op_name = _safe_for_shell_arg(op["name"]) or "the operator"
        op_voice = _safe_for_shell_arg(op["voice_reference"]) or "the voice reference"
        parts.append(
            ". Read outputs/operations/email-intelligence/_latest-fetch.json, "
            "locate this conversation by id, show me the participants + summary + "
            f"proposed actions, then help me draft a reply in {op_name}'s voice "
            f"({op_voice})."
        )
        return "".join(parts)
    return ""


# cmd.exe's command-line limit, applied to the whole `cmd /k <inner>` string
# rather than to the base64 context blob inside it. `_encode_context` caps the
# blob; this caps what the shell actually parses.
CMD_INNER_MAX = 8191


def _encode_context(context: dict | None) -> str | None:
    """Serialize the /launch caller's context dict to a base64-encoded
    JSON string for the BRIDGE_CONTEXT env var. Base64 avoids issues
    with shell metacharacters, quotes, spaces, and Windows cmd.exe
    interpretation. Skills decode with:

        import base64, json, os
        ctx = json.loads(base64.b64decode(os.environ.get('BRIDGE_CONTEXT', '')) or '{}')

    Returns None when context is None or empty (caller skips the env var).

    Caps the ENCODED string at 8 KB. Base64 inflates by 4/3, so the usable
    payload is about 6 KB. This line said "caps payload at 8 KB", which sizes
    the caller's dict against a limit the code does not apply: a 7 KB context
    was silently dropped and the skill lost its pre-population with no error
    anywhere.

    This cap is NOT the cmd.exe command-line limit, which the same sentence
    used to claim ("what the cmd.exe length limit actually sees"). What cmd.exe
    parses is the whole `cmd /k` inner string `build_wt_command` assembles, and
    that wraps this blob in two `set` statements, `claude`, an optional
    `--resume <id>`, and an optional quoted initial prompt. So an encoding that
    passes here at 8,050 bytes still produced an inner string over the 8,191
    character limit. `build_wt_command` owns that check, against the finished
    string, because only the finished string is what the limit applies to."""
    if not context:
        return None
    try:
        encoded = base64.b64encode(json.dumps(context, default=str).encode("utf-8")).decode("ascii")
    except (TypeError, ValueError):
        return None
    if len(encoded) > 8192:
        return None  # too big to fit in env safely; caller proceeds without
    return encoded


def build_wt_command(user_slug: str, title: str, cwd: str, action: str,
                     session_id: str | None, context: dict | None = None) -> list[str]:
    # Validate inputs that flow into cmd.exe's parsed inner string.
    # title and cwd are passed as separate argv to wt.exe (positional args)
    # so they cannot inject into cmd.exe - but title with a literal " can
    # break argv reconstruction. Strip embedded quotes and control chars
    # defensively.
    _validate_inputs(user_slug, action, session_id)
    safe_title = _safe_title(title)
    ctx_prefix = ""
    encoded_ctx = _encode_context(context)
    if encoded_ctx:
        # Base64 is allowlist-safe (A-Za-z0-9+/=) - no shell metacharacters.
        ctx_prefix = f"set BRIDGE_CONTEXT={encoded_ctx}&& "
    # Initial-prompt suffix: 'claude "<prompt>"' so the session opens
    # with the right context already loaded instead of a blank terminal.
    initial = _build_initial_prompt(action, context)
    prompt_suffix = f' "{initial}"' if initial else ""

    def _inner(prefix: str) -> str:
        if session_id:
            return (f"set BRIDGE_ORIGIN=browser&& set BRIDGE_ACTION={action}&& "
                    f"{prefix}claude --resume {session_id}{prompt_suffix}")
        return (f"set BRIDGE_ORIGIN=browser&& set BRIDGE_ACTION={action}&& "
                f"{prefix}claude{prompt_suffix}")

    inner = _inner(ctx_prefix)
    if len(inner) > CMD_INNER_MAX and ctx_prefix:
        # The cap in `_encode_context` sizes the base64 blob alone, and cmd.exe
        # measures this whole string. The wrapper is 120 to 570 characters
        # depending on the action and whether an initial prompt is built, so an
        # encoding that passed the 8 KB cap could still overrun 8,191 here.
        # cmd.exe answers "The input line is too long." and the launched window
        # shows an error instead of running claude, which loses the session, not
        # just the pre-population. Dropping the context keeps the launch: the
        # skill opens without pre-populated state, which is what `_encode_context`
        # already does when the blob alone is too big.
        inner = _inner("")
    return [
        "wt.exe", "-w", f"31c-{user_slug}",
        "new-tab", "--title", safe_title, "-d", cwd,
        "cmd", "/k", inner,
    ]


def build_tmux_command(user_slug: str, title: str, cwd: str, action: str,
                       session_id: str | None, context: dict | None = None) -> list[str]:
    """A DETACHED-CREATE tmux command. It does not attach, and it is not
    idempotent on its own - `_run_tmux_session` owns the already-exists case
    with a `has-session` pre-check.

    No `-A`, since 2026-08-24. `-A` was carried here (and asserted in the tests
    as "idempotent attach-or-create") on the belief that it made a repeat launch
    a no-op. It does the opposite from a daemon: `-A` makes `new-session` behave
    like `attach-session` when the session exists, and attaching needs a
    terminal. Measured on tmux 3.4 with no controlling tty, the second launch
    exits 1 with `open terminal failed: not a terminal` while the session sits
    there healthy -- so every launch after the first reported failure, and
    reported it as a stale socket.

    What attaches a GUI afterwards depends on the platform, and this docstring
    used to name only one of the two: macOS follows with osascript, Linux
    follows with a detected terminal emulator, and headless Linux follows with
    nothing at all - the caller attaches later via the returned attach_hint.
    """
    _validate_inputs(user_slug, action, session_id)
    safe_title = _safe_title(title)
    ctx_prefix = ""
    encoded_ctx = _encode_context(context)
    if encoded_ctx:
        ctx_prefix = f"BRIDGE_CONTEXT={encoded_ctx} "
    initial = _build_initial_prompt(action, context)
    # shlex.quote handles embedded apostrophes (e.g. "Misha's voice") that
    # would otherwise terminate a naive single-quoted wrapper on POSIX shells.
    prompt_suffix = f" {shlex.quote(initial)}" if initial else ""
    if session_id:
        inner = f"BRIDGE_ORIGIN=browser BRIDGE_ACTION={action} {ctx_prefix}claude --resume {session_id}{prompt_suffix}"
    else:
        inner = f"BRIDGE_ORIGIN=browser BRIDGE_ACTION={action} {ctx_prefix}claude{prompt_suffix}"
    return [
        "tmux", "new-session", "-d",
        "-s", f"31c-{user_slug}",
        "-n", safe_title,
        "-c", cwd,
        inner,
    ]


def build_osascript_attach_command(user_slug: str) -> list[str]:
    """osascript invocation that opens / focuses Terminal.app and attaches
    to the named tmux session. user_slug is allowlist-validated (no
    quotes or shell metacharacters), so embedding directly inside the
    AppleScript string is safe."""
    _validate_inputs(user_slug, "noop", None)
    script = f'tell application "Terminal" to do script "tmux attach -t 31c-{user_slug}"'
    return ["osascript", "-e", script]


_TMUX_SPAWN_TIMEOUT = 10


def _tmux_has_session(session: str) -> bool:
    """True when a tmux session of that name is already live.

    `tmux has-session` needs no terminal, so it works from the daemon. Any
    failure to run tmux at all reads as "no session", which sends the caller
    down the create path where the real error is reported.
    """
    try:
        return subprocess.run(
            ["tmux", "has-session", "-t", session],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=_TMUX_SPAWN_TIMEOUT, check=False,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _run_tmux_session(tmux_cmd: list[str]) -> None:
    """Create the detached tmux session, or raise TerminalUnavailable.

    Two silent-failure paths used to live in the one line this replaces,
    `subprocess.Popen(tmux_cmd, close_fds=True).wait(timeout=10)`:

    * The exit code was never read. `tmux new-session` fails on a stale socket,
      a deleted `-c` directory, or conflicting session state - and the caller
      still returned ``launched: True``, then fired an attach at a session that
      does not exist. A false success at exactly the step the function exists
      to perform.
    * `TimeoutExpired` propagated out of a function whose docstring promised
      only TerminalUnavailable or ValueError, AND the Popen object was
      discarded on the same line, so there was no handle left to kill the
      still-running tmux. Slow disk or a cold tmux server leaked a process and
      crashed the caller.

    Idempotency lives here, not in the `-A` flag it used to live in. A session
    that already exists is a SUCCESS: nothing to create, and the caller attaches
    through its own platform path.

    The failure message quotes tmux's own stderr instead of guessing at a cause.
    It used to assert "stale socket, or the working directory no longer exists",
    and the failure it actually fired on most was neither.
    """
    session = tmux_cmd[tmux_cmd.index("-s") + 1]
    if _tmux_has_session(session):
        return
    proc = subprocess.Popen(tmux_cmd, close_fds=True, stderr=subprocess.PIPE)
    try:
        _out, err = proc.communicate(timeout=_TMUX_SPAWN_TIMEOUT)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        raise TerminalUnavailable(
            f"tmux did not finish within {_TMUX_SPAWN_TIMEOUT}s; no session was created"
        ) from None
    if rc == 0:
        return
    detail = (err or b"").decode("utf-8", "replace").strip()
    if "duplicate session" in detail:
        # Lost a race with a second launch between the has-session check above
        # and this create. The session exists, which is what the caller wanted.
        return
    raise TerminalUnavailable(
        f"tmux new-session exited {rc}; no session was created"
        + (f": {detail}" if detail else "")
    )


def spawn_or_focus(user_slug: str, title: str, cwd: Path, action: str,
                   session_id: str | None, context: dict | None = None) -> dict:
    """Dispatch to the platform-specific launcher.

    Returns ONE schema on every platform::

        {"launched": True,
         "attach_attempted": bool,
         "attach_hint": str | None,
         "command": str}

    The three branches used to return three different shapes - `attached` and
    `attach_hint` existed only on Linux - so any caller reading either key got a
    KeyError on macOS and Windows. A dispatch function returns one contract.

    `attach_attempted` is deliberately not called `attached`. The attach is
    fire-and-forget by design (a GUI window takes longer than a request), so
    what this function establishes is that the command was spawned, never that a
    window appeared: osascript is refused outright when macOS automation
    permission is denied, and a terminal emulator can reject its own flags. The
    old key asserted the outcome and suppressed the fallback hint along with it,
    leaving the user told a window had opened and given no way to reach the
    session. The hint is now always returned.

    Raises TerminalUnavailable when the required tool is missing or the tmux
    session could not be created, and ValueError when any input fails the
    allowlist.
    """
    if sys.platform == "darwin":
        assert_tmux_available()
        tmux_cmd = build_tmux_command(user_slug, title, str(cwd), action, session_id, context=context)
        _run_tmux_session(tmux_cmd)
        attach_cmd = build_osascript_attach_command(user_slug)
        subprocess.Popen(attach_cmd, close_fds=True)
        return {
            "launched": True,
            "attach_attempted": True,
            "attach_hint": f"tmux attach -t 31c-{user_slug}",
            "command": " ".join(shlex.quote(c) for c in tmux_cmd),
        }
    if sys.platform.startswith("linux"):
        # Linux: same tmux pattern as macOS; GUI attach via a detected terminal
        # emulator. On headless Linux (no DISPLAY/WAYLAND_DISPLAY), spawn the
        # session and return without attempting GUI attach.
        assert_tmux_available()
        tmux_cmd = build_tmux_command(user_slug, title, str(cwd), action, session_id, context=context)
        _run_tmux_session(tmux_cmd)
        terminal = find_linux_terminal() if _is_linux_gui_session() else None
        if terminal:
            subprocess.Popen(build_linux_attach_command(terminal, user_slug), close_fds=True)
        return {
            "launched": True,
            "attach_attempted": bool(terminal),
            "attach_hint": f"tmux attach -t 31c-{user_slug}",
            "command": " ".join(shlex.quote(c) for c in tmux_cmd),
        }
    # Default: Windows path. wt.exe is required; raises TerminalUnavailable
    # on unsupported platforms (BSD, etc.).
    assert_wt_available()
    cmd = build_wt_command(user_slug, title, str(cwd), action, session_id, context=context)
    subprocess.Popen(cmd, close_fds=True)
    # wt.exe IS the attach - there is no separate step and no tmux to fall back
    # on, so the hint is None rather than a command that would not work here.
    return {
        "launched": True,
        "attach_attempted": True,
        "attach_hint": None,
        "command": " ".join(cmd),
    }
