"""Cross-platform terminal launcher for the bridge daemon.

Windows uses Windows Terminal's -w (window-name) flag: creates the
window if absent, attaches a new tab if present.

macOS uses tmux's -A (attach-if-exists, create-if-not) on a named
session, then osascript to focus Terminal.app on that session.

Linux uses the same tmux pattern as macOS, then a detected terminal
emulator (gnome-terminal / konsole / xterm / x-terminal-emulator /
alacritty / kitty) to open a GUI window attached to the session.
On headless Linux (no DISPLAY/WAYLAND_DISPLAY), the tmux session is
spawned but no GUI attach is attempted - the caller can attach later
via `tmux attach -t 31c-<slug>`.

All paths inject `BRIDGE_ORIGIN=browser` so the Stop hook's
origin-gated prompt knows the session was launched from the dashboard
(spec section 3.2). They also inject `BRIDGE_CONTEXT` as a JSON
string when the /launch caller supplies a context dict - skills like
/email-respond use this to pre-populate (conv_id, subject, etc.)
instead of asking the user to retype them.
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
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
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
    """Locate a Linux GUI terminal emulator. Returns path or None on headless."""
    for name in _LINUX_TERMINAL_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def _is_linux_gui_session() -> bool:
    """True iff a Linux X11 or Wayland session appears to be available."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def build_linux_attach_command(terminal_exe: str, user_slug: str) -> list[str]:
    """Build the command to open a terminal window attached to the named tmux session.

    Different Linux terminals use different flags to "run this command in a new window":
      - gnome-terminal: `gnome-terminal -- <cmd>`  (newer versions; `-e` deprecated)
      - konsole:        `konsole -e <cmd>`
      - alacritty:      `alacritty -e <cmd>`
      - kitty:          `kitty <cmd>`               (no flag needed; positional)
      - xterm:          `xterm -e <cmd>`
      - x-terminal-emulator (Debian wrapper): `-e <cmd>` (semantics depend on default)
    """
    target = f"31c-{user_slug}"
    name = Path(terminal_exe).name
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
# The prompt is constrained to ASCII-safe chars without double quotes
# or shell metacharacters so it embeds cleanly inside the cmd.exe
# `/k "..."` parameter without needing aggressive escaping.

_CONV_ID_RE = re.compile(r"^[A-Za-z0-9_=/+\-]{1,256}$")


def _safe_for_shell_arg(s: str) -> str:
    """Strip everything that would break the cmd.exe / shell quoting:
    double quotes, backticks, control chars, leading/trailing whitespace.
    Cap at 200 chars."""
    if not isinstance(s, str):
        return ""
    out = "".join(c for c in s if c.isprintable() and c not in '"`\r\n\t')
    return out.strip()[:200]


def _build_initial_prompt(action: str, context: dict | None) -> str:
    """Return a short ASCII-safe initial prompt for `claude`, or empty
    string if we have no useful context for this action.

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
        # Imperative prompt, no embedded quotes, ASCII-only. Tells Claude
        # exactly what to do without depending on the /email-respond
        # skill's existing 'paste here' contract.
        parts = [
            f"I want to respond to email conversation {conv_id}",
        ]
        if safe_subject:
            parts.append(f"(subject: {safe_subject})")
        parts.append(
            ". Read outputs/operations/email-intelligence/_latest-fetch.json, "
            "locate this conversation by id, show me the participants + summary + "
            "proposed actions, then help me draft a reply in Misha's voice "
            "(reference/misha-voice.md)."
        )
        return "".join(parts)
    return ""


def _encode_context(context: dict | None) -> str | None:
    """Serialize the /launch caller's context dict to a base64-encoded
    JSON string for the BRIDGE_CONTEXT env var. Base64 avoids issues
    with shell metacharacters, quotes, spaces, and Windows cmd.exe
    interpretation. Skills decode with:

        import base64, json, os
        ctx = json.loads(base64.b64decode(os.environ.get('BRIDGE_CONTEXT', '')) or '{}')

    Returns None when context is None or empty (caller skips the env var).
    Caps payload at 8 KB to prevent the cmd.exe line length explosion."""
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
    if session_id:
        inner = f"set BRIDGE_ORIGIN=browser&& set BRIDGE_ACTION={action}&& {ctx_prefix}claude --resume {session_id}{prompt_suffix}"
    else:
        inner = f"set BRIDGE_ORIGIN=browser&& set BRIDGE_ACTION={action}&& {ctx_prefix}claude{prompt_suffix}"
    return [
        "wt.exe", "-w", f"31c-{user_slug}",
        "new-tab", "--title", safe_title, "-d", cwd,
        "cmd", "/k", inner,
    ]


def build_tmux_command(user_slug: str, title: str, cwd: str, action: str,
                       session_id: str | None, context: dict | None = None) -> list[str]:
    """tmux new-session -A creates the session if absent, attaches if
    present. -d keeps it detached so we can spawn it from a daemon
    context; the subsequent osascript call brings Terminal.app to the
    foreground attached to the same named session."""
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
        "tmux", "new-session", "-A", "-d",
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


def spawn_or_focus(user_slug: str, title: str, cwd: Path, action: str,
                   session_id: str | None, context: dict | None = None) -> dict:
    """Dispatch to the platform-specific launcher.

    Returns {launched: True, command: "..."} on success. Raises
    TerminalUnavailable when the required tool is missing, or ValueError
    when any input fails the allowlist.
    """
    if sys.platform == "darwin":
        assert_tmux_available()
        tmux_cmd = build_tmux_command(user_slug, title, str(cwd), action, session_id, context=context)
        subprocess.Popen(tmux_cmd, close_fds=True).wait(timeout=10)
        attach_cmd = build_osascript_attach_command(user_slug)
        subprocess.Popen(attach_cmd, close_fds=True)
        return {
            "launched": True,
            "command": " ".join(shlex.quote(c) for c in tmux_cmd),
        }
    if sys.platform.startswith("linux"):
        # Linux: same tmux pattern as macOS; GUI attach via a detected terminal
        # emulator. On headless Linux (no DISPLAY/WAYLAND_DISPLAY), spawn the
        # session and return without attempting GUI attach.
        assert_tmux_available()
        tmux_cmd = build_tmux_command(user_slug, title, str(cwd), action, session_id, context=context)
        subprocess.Popen(tmux_cmd, close_fds=True).wait(timeout=10)
        terminal = find_linux_terminal() if _is_linux_gui_session() else None
        if terminal:
            attach_cmd = build_linux_attach_command(terminal, user_slug)
            subprocess.Popen(attach_cmd, close_fds=True)
            attached = True
        else:
            attached = False
        return {
            "launched": True,
            "attached": attached,
            "command": " ".join(shlex.quote(c) for c in tmux_cmd),
            "attach_hint": None if attached else f"tmux attach -t 31c-{user_slug}",
        }
    # Default: Windows path. wt.exe is required; raises TerminalUnavailable
    # on unsupported platforms (BSD, etc.).
    assert_wt_available()
    cmd = build_wt_command(user_slug, title, str(cwd), action, session_id, context=context)
    subprocess.Popen(cmd, close_fds=True)
    return {"launched": True, "command": " ".join(cmd)}
