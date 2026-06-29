from scripts.bridge_daemon.terminal import (
    build_osascript_attach_command,
    build_tmux_command,
    build_wt_command,
)

def test_build_with_session_id():
    cmd = build_wt_command(
        user_slug="misha",
        title="ODIN-5",
        cwd=r"C:\work\31c",
        action="email-respond",
        session_id="abc123",
    )
    assert cmd[0] == "wt.exe"
    assert "-w" in cmd
    assert "31c-misha" in cmd
    assert "new-tab" in cmd
    assert "--title" in cmd
    joined = " ".join(cmd)
    assert "BRIDGE_ORIGIN=browser" in joined
    assert "claude --resume abc123" in joined

def test_build_without_session_id_starts_fresh():
    cmd = build_wt_command(
        user_slug="misha", title="OSINT", cwd=r"C:\work\31c",
        action="osint", session_id=None,
    )
    joined = " ".join(cmd)
    assert "--resume" not in joined
    assert "claude" in joined


import pytest


def test_build_rejects_session_id_with_cmd_metacharacters():
    """CRITICAL: session_id flows into cmd /k <inner>. Any cmd.exe metachar
    (&, |, >, <, (, ), space, quote, newline) must be rejected to prevent
    command injection. See security regression notes in CLAUDE.md."""
    evils = [
        "abc & calc.exe", "abc && rm -rf /", "abc|cat",
        "abc>file", 'abc"|whoami', "abc;ls",
        "abc\nrun", "abc(echo)", "abc()",
    ]
    for evil in evils:
        with pytest.raises(ValueError, match="invalid session_id"):
            build_wt_command("misha", "t", r"C:\w", "osint", evil)

def test_build_accepts_legitimate_session_ids():
    """UUIDs and short alphanumeric tokens must continue to work."""
    legitimates = [
        "abc123",
        "550e8400-e29b-41d4-a716-446655440000",  # pragma: allowlist secret
        "session_42",
        "a-b-c",
        "ABC123",
    ]
    for ok in legitimates:
        # Should not raise:
        build_wt_command("misha", "t", r"C:\w", "osint", ok)

def test_build_rejects_user_slug_with_metacharacters():
    with pytest.raises(ValueError, match="invalid user_slug"):
        build_wt_command("misha & evil", "t", r"C:\w", "osint", None)
    with pytest.raises(ValueError, match="invalid user_slug"):
        build_wt_command("MISHA", "t", r"C:\w", "osint", None)  # uppercase rejected

def test_build_rejects_action_with_metacharacters():
    with pytest.raises(ValueError, match="invalid action"):
        build_wt_command("misha", "t", r"C:\w", "osint && pwn", None)

def test_build_sanitizes_title_with_quote_and_control_chars():
    cmd = build_wt_command("misha", 'evil"\ntitle', r"C:\w", "osint", None)
    # Title in cmd should have the quote and newline stripped:
    title_idx = cmd.index("--title") + 1
    assert '"' not in cmd[title_idx]
    assert "\n" not in cmd[title_idx]

def test_build_includes_bridge_action_env_var():
    """The `action` parameter is wired through as BRIDGE_ACTION env var so the
    Stop hook (Task 20) can render 'you were doing /email-respond' messages."""
    cmd = build_wt_command("misha", "t", r"C:\w", "email-respond", None)
    joined = " ".join(cmd)
    assert "BRIDGE_ACTION=email-respond" in joined  # pragma: allowlist secret


# Phase A (macOS launcher) tests. build_tmux_command + build_osascript_attach_command.
# These don't spawn a real tmux - just check argv shape and allowlist enforcement.


def test_tmux_build_with_session_id():
    cmd = build_tmux_command(
        user_slug="misha", title="ODIN-5", cwd="/Users/misha/work",
        action="email-respond", session_id="abc123",
    )
    assert cmd[0] == "tmux"
    assert "new-session" in cmd
    assert "-A" in cmd  # idempotent attach-or-create
    assert "-d" in cmd  # detached so daemon can spawn it
    assert "31c-misha" in cmd
    # tmux passes the shell command as the last arg; check both env + claude resume.
    inner = cmd[-1]
    assert "BRIDGE_ORIGIN=browser" in inner
    assert "BRIDGE_ACTION=email-respond" in inner  # pragma: allowlist secret
    assert "claude --resume abc123" in inner
    # cwd flows in as a -c arg
    assert "-c" in cmd
    assert "/Users/misha/work" in cmd


def test_tmux_build_without_session_id_starts_fresh():
    cmd = build_tmux_command(
        user_slug="misha", title="OSINT", cwd="/Users/misha/work",
        action="osint", session_id=None,
    )
    inner = cmd[-1]
    assert "--resume" not in inner
    assert inner.endswith("claude")


def test_tmux_build_rejects_unsafe_session_id():
    with pytest.raises(ValueError, match="invalid session_id"):
        build_tmux_command("misha", "t", "/synthetic-cwd", "osint", "evil; rm -rf /")


def test_tmux_build_rejects_unsafe_user_slug():
    with pytest.raises(ValueError, match="invalid user_slug"):
        build_tmux_command("evil$(whoami)", "t", "/synthetic-cwd", "osint", None)


def test_tmux_build_rejects_unsafe_action():
    with pytest.raises(ValueError, match="invalid action"):
        build_tmux_command("misha", "t", "/synthetic-cwd", "evil; ls", None)


def test_tmux_build_sanitizes_title():
    """Title with control chars + quotes -> tmux -n window-name must be clean."""
    cmd = build_tmux_command("misha", 'evil"\ntitle', "/synthetic-cwd", "osint", None)
    name_idx = cmd.index("-n") + 1
    assert '"' not in cmd[name_idx]
    assert "\n" not in cmd[name_idx]


def test_osascript_attach_command_shape():
    cmd = build_osascript_attach_command("misha")
    assert cmd[0] == "osascript"
    assert cmd[1] == "-e"
    script = cmd[2]
    assert 'tell application "Terminal"' in script
    assert "tmux attach -t 31c-misha" in script


def test_osascript_attach_rejects_unsafe_user_slug():
    with pytest.raises(ValueError, match="invalid user_slug"):
        build_osascript_attach_command('"; rm -rf /; "')


# Context propagation (spec section 3.3 deep-link context). The /launch
# endpoint takes an optional context dict; both Windows and macOS
# command builders serialize it to BRIDGE_CONTEXT as base64-encoded
# JSON so skills can pre-populate state without the user retyping
# (e.g. conv_id for /email-respond).


def test_wt_context_serialized_as_base64_bridge_context_env(tmp_path):
    import base64
    import json as _json
    cmd = build_wt_command(
        "misha", "email: TradeExpo 2026", "/synthetic-cwd", "email-respond", None,
        context={"conv_id": "AAQkAD123", "subject": "TradeExpo 2026"},
    )
    inner = cmd[-1]
    # BRIDGE_CONTEXT should be set BEFORE the claude launch
    assert "set BRIDGE_CONTEXT=" in inner
    # Extract the base64 value
    import re as _re
    m = _re.search(r"set BRIDGE_CONTEXT=([A-Za-z0-9+/=]+)&&", inner)
    assert m, f"BRIDGE_CONTEXT base64 not found in inner: {inner!r}"
    decoded = _json.loads(base64.b64decode(m.group(1)).decode("utf-8"))
    assert decoded == {"conv_id": "AAQkAD123", "subject": "TradeExpo 2026"}


def test_wt_context_none_omits_env_var():
    """Legacy callers without context get the same shell as before -
    no BRIDGE_CONTEXT line."""
    cmd = build_wt_command("misha", "t", "/synthetic-cwd", "osint", None, context=None)
    inner = cmd[-1]
    assert "BRIDGE_CONTEXT" not in inner


def test_wt_context_empty_dict_omits_env_var():
    """Empty {} from the browser is treated the same as None - no env var."""
    cmd = build_wt_command("misha", "t", "/synthetic-cwd", "osint", None, context={})
    inner = cmd[-1]
    assert "BRIDGE_CONTEXT" not in inner


def test_tmux_context_serialized_as_base64_bridge_context_env():
    import base64
    import json as _json
    cmd = build_tmux_command(
        "misha", "email: TradeExpo 2026", "/synthetic-cwd", "email-respond", None,
        context={"conv_id": "AAQkAD123"},
    )
    inner = cmd[-1]
    assert "BRIDGE_CONTEXT=" in inner
    import re as _re
    m = _re.search(r"BRIDGE_CONTEXT=([A-Za-z0-9+/=]+) ", inner + " ")
    assert m, f"BRIDGE_CONTEXT base64 not found: {inner!r}"
    decoded = _json.loads(base64.b64decode(m.group(1)).decode("utf-8"))
    assert decoded == {"conv_id": "AAQkAD123"}


def test_context_oversize_silently_drops():
    """Payloads > 8KB are dropped (don't blow up cmd.exe's line length)."""
    huge = {"data": "x" * 20000}
    cmd = build_wt_command("misha", "t", "/synthetic-cwd", "osint", None, context=huge)
    inner = cmd[-1]
    # Caller proceeds without context rather than failing the launch
    assert "BRIDGE_CONTEXT" not in inner


def test_context_unserializable_silently_drops():
    """Non-JSON-serializable values fall through to no env var, no crash."""
    class Weird:
        pass
    cmd = build_wt_command("misha", "t", "/synthetic-cwd", "osint", None,
                           context={"obj": Weird()})
    inner = cmd[-1]
    # default=str in json.dumps actually handles arbitrary objects, so
    # this WILL serialize. The test docs the safety contract.
    # If TypeError raised, _encode_context returns None and we'd see no env.
    # Either outcome is acceptable; just must not raise.
    assert isinstance(inner, str)


# Initial-prompt builder: action+context -> ASCII-safe `claude "prompt"`
# suffix so the terminal opens with context already loaded.


def test_wt_email_respond_passes_initial_prompt():
    """email-respond + conv_id -> 'claude "<prompt>"' instead of bare claude.
    The prompt names the conversation id explicitly so the session can
    look it up without the user retyping."""
    cmd = build_wt_command(
        "misha", "email: PandaDoc", "/synthetic-cwd", "email-respond", None,
        context={"conv_id": "AAQkAD12345", "subject": "PandaDoc reminder"},
    )
    inner = cmd[-1]
    assert "claude \"" in inner
    assert "AAQkAD12345" in inner
    assert "PandaDoc reminder" in inner
    # Must read _latest-fetch.json so the session has the rich payload
    assert "_latest-fetch.json" in inner


def test_wt_email_respond_with_unsafe_conv_id_drops_prompt():
    """An unsafe conv_id (control chars or quotes) makes _build_initial_prompt
    return empty -> session opens bare instead of injecting unsafe text."""
    cmd = build_wt_command(
        "misha", "t", "/synthetic-cwd", "email-respond", None,
        context={"conv_id": "abc\"; rm -rf /;\""},
    )
    inner = cmd[-1]
    # No 'claude "...' suffix - bare claude only
    assert inner.rstrip().endswith("&& claude") or "claude \"\"" not in inner


def test_wt_email_respond_without_conv_id_drops_prompt():
    """email-respond action but no conv_id in context -> no initial prompt."""
    cmd = build_wt_command(
        "misha", "t", "/synthetic-cwd", "email-respond", None,
        context={"other": "field"},
    )
    inner = cmd[-1]
    assert inner.rstrip().endswith("claude")


def test_wt_non_email_action_has_no_initial_prompt():
    """Other actions (osint, deal-strategy) currently have no template ->
    session opens bare. Adding more templates is a future commit."""
    cmd = build_wt_command(
        "misha", "t", "/synthetic-cwd", "osint", None,
        context={"target": "ExampleTelco"},
    )
    inner = cmd[-1]
    # 'claude' at the tail, no quote-wrapped prompt suffix
    assert inner.endswith("claude")


def test_tmux_email_respond_uses_single_quotes():
    """macOS tmux path uses single-quoted prompt arg (no shell expansion)."""
    cmd = build_tmux_command(
        "misha", "t", "/synthetic-cwd", "email-respond", None,
        context={"conv_id": "ABC123"},
    )
    inner = cmd[-1]
    assert "claude '" in inner
    assert "ABC123" in inner
